#!/usr/bin/env python3
"""Adaptateur d'entrées/sorties du simulateur.

Le cœur ne connaît ni le réseau ni l'écran. Ce service lui donne les deux :

  * il fait tourner la simulation en temps réel dans un fil dédié ;
  * il diffuse les instantanés en SSE (Server-Sent Events) — un flux HTTP
    ordinaire, sans dépendance, là où un WebSocket imposerait soit une
    bibliothèque, soit cent lignes de trame à écrire à la main ;
  * il accepte les ordres de l'opérateur en POST ;
  * il sert la console statique ;
  * il peut injecter du trafic maritime réel par AIS.

Le pont Modbus tourne dans un autre fil et dégrade proprement : sans
automate joignable, l'IPMS bascule sur son modèle logiciel et la console
l'affiche. La stack complète démarre donc sans OpenPLC.

Le pont AIS suit exactement le même contrat, et il est éteint par défaut :

    AIS_SOURCE=digitraffic python3 services/server.py    # flux public réel
    AIS_SOURCE=fichier python3 services/server.py        # instantané rejoué

Il n'agit que sur un scénario portant un bloc [origine] — sans point de
référence géographique, une position AIS n'a nulle part où aller.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.modbus import ModbusTcp                     # noqa: E402
from sim.engine import DT, Engine                         # noqa: E402
from sim.scenario import load                             # noqa: E402

WEB = ROOT / "web"
SCEN = ROOT / "scenarios"
HOST = os.environ.get("CMS_HOST", "0.0.0.0")
PORT = int(os.environ.get("CMS_PORT", "8000"))
PLC_HOST = os.environ.get("PLC_HOST", "")
PLC_PORT = int(os.environ.get("PLC_PORT", "502"))
DEFAULT_SC = os.environ.get("CMS_SCENARIO", "02-saturation-asm.toml")
AIS_SOURCE = os.environ.get("AIS_SOURCE", "").lower()
AIS_FILE = os.environ.get("AIS_FILE", "fixtures/ais-golfe-finlande.json")
AIS_RAYON = float(os.environ.get("AIS_RAYON_NM", "60"))
AIS_PERIODE = float(os.environ.get("AIS_PERIODE", "6"))


class Sim:
    """La simulation et son horloge. Un seul verrou : la boucle avance, les
    requêtes HTTP lisent le dernier instantané ou déposent un ordre."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cv = threading.Condition()
        self.frame = {}
        self.rev = 0
        self.rate = 1.0
        self.running = True
        self.engine = None
        self.name = ""
        self.meta = {}
        self.ais = None
        self.load(DEFAULT_SC)

    def load(self, fname):
        path = SCEN / Path(fname).name
        if not path.exists():
            return False
        sc = load(path)
        with self.lock:
            self.engine = Engine(sc)
            self.engine.doctrine["auto_id"] = True
            self.name = path.name
            self.meta = {"scenario": sc["name"], "brief": sc["brief"],
                         "attendu": sc["attendu"], "fichier": path.name}
        return True

    def command(self, c):
        k = c.get("cmd")
        with self.lock:
            e = self.engine
            if k == "hook":
                e.hooked = c.get("piste")
            elif k == "classify":
                tr = e.tracker.tracks.get(c.get("piste"))
                if tr:
                    tr.aff, tr.classified_by = c.get("aff", "unknown"), "opérateur"
                    e.log("info", "%s — classée %s par l'opérateur"
                          % (tr.num, tr.aff.upper()))
            elif k == "engage":
                return {"ok": e.engage(c.get("piste"), c.get("effecteur"))}
            elif k == "decoys":
                return {"ok": e.deploy_decoys()}
            elif k == "doctrine":
                e.doctrine[c.get("cle")] = bool(c.get("valeur"))
                e.log("info", "Doctrine %s : %s" % (c.get("cle"),
                      "armée" if c.get("valeur") else "désarmée"))
            elif k == "order":
                if "course" in c:
                    e.own.ordered_course = float(c["course"]) % 360
                if "speed_kt" in c:
                    from sim.geo import KT
                    e.own.ordered_speed = float(c["speed_kt"]) * KT
            elif k == "pump":
                e.platform.pump = bool(c.get("valeur", True))
                e.log("crit" if not e.platform.pump else "info",
                      "IPMS — pompe %s depuis le poste instructeur"
                      % ("arrêtée" if not e.platform.pump else "relancée"))
            elif k == "rate":
                self.rate = max(0.0, min(20.0, float(c.get("valeur", 1))))
            elif k == "scenario":
                pass
            elif k == "restart":
                pass
        if k == "scenario":
            return {"ok": self.load(c.get("fichier", ""))}
        if k == "restart":
            return {"ok": self.load(self.name)}
        return {"ok": True}

    def run(self):
        acc, last, pub = 0.0, time.monotonic(), 0.0
        while self.running:
            now = time.monotonic()
            acc += (now - last) * self.rate
            last = now
            steps = 0
            while acc >= DT and steps < 400:
                with self.lock:
                    self.engine.step()
                acc -= DT
                steps += 1
            if now - pub >= 0.1:
                pub = now
                with self.lock:
                    f = self.engine.snapshot()
                    f["meta"] = self.meta
                    f["rate"] = self.rate
                    f["ais_feed"] = ({"etat": self.ais.etat, "n": self.ais.n}
                                     if self.ais else None)
                with self.cv:
                    self.frame, self.rev = f, self.rev + 1
                    self.cv.notify_all()
            time.sleep(0.005)

    def wait(self, since, timeout=5.0):
        with self.cv:
            if self.rev <= since:
                self.cv.wait(timeout)
            return self.frame, self.rev


class PlcBridge(threading.Thread):
    """Lit l'automate et injecte l'état plateforme dans la simulation.

    S'il n'y a pas d'automate, on ne casse rien : l'IPMS reste sur son
    modèle logiciel et la console affiche « SIMULÉ » au lieu de « MODBUS ».
    """
    daemon = True

    def __init__(self, sim):
        super().__init__()
        self.sim = sim
        self.cli = ModbusTcp(PLC_HOST, PLC_PORT) if PLC_HOST else None

    def run(self):
        if not self.cli:
            return
        while True:
            try:
                # Le programme automate écrit dans %QW / %QX : côté Modbus ce
                # sont des registres de maintien et des bobines. Les %IW sont
                # ses entrées, il ne peut pas y écrire.
                regs = self.cli.read_holding_registers(0, 4)
                coils = self.cli.read_coils(0, 8)
                with self.sim.lock:
                    self.sim.engine.platform.ingest(regs, coils)
            except Exception:
                self.cli.close()
                with self.sim.lock:
                    self.sim.engine.platform.source = "SIMULÉ"
                time.sleep(3.0)
                continue
            time.sleep(0.2)


def demarrer_ais(sim):
    """Monte le pont AIS si l'exploitant l'a demandé. Une source injoignable
    n'empêche pas le lab de démarrer : on le dit et on continue."""
    if AIS_SOURCE not in ("digitraffic", "fichier"):
        return None
    from services.ais import AisBridge, Digitraffic, Fichier
    try:
        src = (Fichier(ROOT / AIS_FILE) if AIS_SOURCE == "fichier" else Digitraffic())
    except OSError as e:
        print("AIS : source illisible (%s) — le lab démarre sans" % e, flush=True)
        return None
    pont = AisBridge(sim, src, rayon_nm=AIS_RAYON, periode=AIS_PERIODE)
    sim.ais = pont
    pont.start()
    return pont


SIM = Sim()

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json",
         ".jsonl": "application/x-ndjson", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/stream":
            return self.sse()
        if path == "/api/scenarios":
            items = [{"fichier": p.name, "nom": p.stem} for p in sorted(SCEN.glob("*.toml"))]
            return self._send(200, json.dumps(items).encode(), "application/json")
        if path == "/api/frame":
            f, _ = SIM.wait(-1, 0.1)
            return self._send(200, json.dumps(f, ensure_ascii=False).encode(),
                              "application/json")
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (WEB / rel).resolve()
        if not str(f).startswith(str(WEB.resolve())) or not f.is_file():
            return self._send(404, b"introuvable")
        return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def do_POST(self):
        if urlparse(self.path).path != "/cmd":
            return self._send(404, b"introuvable")
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            cmd = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, b"json invalide")
        res = SIM.command(cmd)
        return self._send(200, json.dumps(res).encode(), "application/json")

    def sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        rev = -1
        try:
            while True:
                frame, rev = SIM.wait(rev)
                payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(b"data: " + payload.encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def main():
    threading.Thread(target=SIM.run, daemon=True).start()
    PlcBridge(SIM).start()
    demarrer_ais(SIM)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("CMS-Lab sur http://%s:%d  (scénario %s, IPMS %s, AIS %s)"
          % (HOST, PORT, DEFAULT_SC,
             "Modbus " + PLC_HOST if PLC_HOST else "simulé",
             AIS_SOURCE or "éteint"),
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
