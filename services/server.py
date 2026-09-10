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
FIELD_HOST = os.environ.get("FIELD_HOST", "")
FIELD_PORT = int(os.environ.get("FIELD_PORT", "5020"))
FIELD_TIMESCALE = float(os.environ.get("FIELD_TIMESCALE", "1"))
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
        self.ais_cfg = None
        self.capture = None          # compte rendu de la dernière capture
        self.capture_en_cours = False
        self._pompe_seq_vu = 0
        self.load(DEFAULT_SC)

    def load(self, fname):
        path = SCEN / Path(fname).name
        if not path.exists():
            return False
        sc = load(path)
        with self.lock:
            self.engine = Engine(sc)
            self.engine.platform.timescale = FIELD_TIMESCALE
            self.engine.doctrine["auto_id"] = True
            self._pompe_seq_vu = 0
            self.name = path.name
            self.meta = {"scenario": sc["name"], "brief": sc["brief"],
                         "attendu": sc["attendu"], "fichier": path.name,
                         "en": sc.get("en") or {}}
            self.ais_cfg = self._resoudre_ais(sc)
        return True

    @staticmethod
    def _resoudre_ais(sc):
        """Quelle source AIS pour ce scénario, et avec quels réglages.

        La variable d'environnement l'emporte : c'est l'exploitant qui parle.
        À défaut, le scénario décide — un scénario sans contact scripté a
        besoin du flux pour montrer quoi que ce soit, et doit pouvoir le dire
        lui-même plutôt que d'attendre qu'on devine.

        Un scénario ne peut demander que la source hors ligne. Aller chercher
        le réseau reste un acte explicite de l'exploitant : ouvrir un fichier
        de scénario ne doit pas déclencher de trafic sortant.
        """
        bloc = sc.get("ais") or {}
        source = AIS_SOURCE or bloc.get("source", "")
        if source == "digitraffic" and not AIS_SOURCE:
            source = "fichier"
        if source not in ("digitraffic", "fichier"):
            return None
        return {"source": source,
                "fichier": bloc.get("fichier", AIS_FILE),
                "rayon_nm": float(bloc.get("rayon_nm", AIS_RAYON)),
                "periode": float(bloc.get("periode", AIS_PERIODE)),
                # Les navires à quai encombrent le scope et se dessinent sur
                # la terre. On les écarte par défaut ; un scénario de
                # surveillance portuaire peut les vouloir.
                "inclure_a_quai": bool(bloc.get("inclure_a_quai", False))}

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
                          % (tr.num, tr.aff.upper()),
                          code="classee_operateur", piste=tr.num, aff=tr.aff)
            elif k == "engage":
                return {"ok": e.engage(c.get("piste"), c.get("effecteur"))}
            elif k == "decoys":
                return {"ok": e.deploy_decoys()}
            elif k == "doctrine":
                e.doctrine[c.get("cle")] = bool(c.get("valeur"))
                e.log("info", "Doctrine %s : %s" % (c.get("cle"),
                      "armée" if c.get("valeur") else "désarmée"),
                      code="doctrine", cle=c.get("cle"),
                      armee=bool(c.get("valeur")))
            elif k == "order":
                if "course" in c:
                    e.own.ordered_course = float(c["course"]) % 360
                if "speed_kt" in c:
                    from sim.geo import KT
                    e.own.ordered_speed = float(c["speed_kt"]) * KT
            elif k == "pump":
                marche = bool(c.get("valeur", True))
                e.platform.pump = marche
                e.log("crit" if not marche else "info",
                      "IPMS — pompe %s depuis le poste instructeur"
                      % ("arrêtée" if not marche else "relancée"),
                      code="pompe_instructeur", marche=marche)
                # En mode automate réel, le pont Modbus réécrit platform.pump
                # à chaque sondage (0,2 s) depuis la bobine lue sur l'automate
                # — l'affectation ci-dessus serait donc aussitôt écrasée. On
                # commande le point réel côté capteurs de terrain, au même
                # titre qu'un vrai contacteur ; l'écho revient ensuite par le
                # chemin normal (terrain → automate → console).
                self.commander_pompe_terrain(marche)
            elif k == "armement_tir":
                self.commander_tir_armement(c.get("effecteur", ""))
            elif k == "ais_capture":
                return self.lancer_capture()
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

    def lancer_capture(self):
        """Va chercher un instantané du flux public et l'enregistre.

        Le chemin d'écriture est fixé côté serveur, jamais fourni par le
        client : ce point d'entrée écrit un fichier, et une console n'a
        aucune raison de choisir lequel.

        La capture part dans un fil : elle prend quelques secondes, et la
        simulation ne doit pas s'arrêter pendant ce temps.
        """
        if self.capture_en_cours:
            return {"ok": False, "erreur": "capture déjà en cours"}
        og = (self.engine.sc.get("origine") or {}) if self.engine else {}
        if "lat" not in og or "lon" not in og:
            return {"ok": False, "erreur": "scénario sans [origine] : "
                                           "aucune zone à capturer"}
        rayon = (self.ais_cfg or {}).get("rayon_nm", AIS_RAYON)
        self.capture_en_cours = True
        self.capture = {"etat": "en cours"}

        def travail():
            from services.ais import capturer
            r = capturer(og["lat"], og["lon"], rayon, ROOT / AIS_FILE)
            self.capture = dict(r, etat="ok" if r["ok"] else "échec")
            self.capture_en_cours = False
            # Le journal, pas seulement une infobulle : une capture ratée est
            # un événement d'exploitation, et l'opérateur ne survole pas les
            # boutons pour savoir ce qui s'est passé.
            with self.lock:
                if self.engine:
                    if r["ok"]:
                        self.engine.log("info", "AIS — instantané capturé : "
                                        "%d positions, %d navires au statique"
                                        % (r["n"], r["statique"]),
                                        code="capture_ok", n=r["n"],
                                        statique=r["statique"])
                    else:
                        self.engine.log("warn", "AIS — capture impossible : %s"
                                        % r["erreur"],
                                        code="capture_ko", erreur=r["erreur"])
            # Faire relire l'instantané au pont : sans cela la console
            # continuerait d'afficher l'ancien jusqu'au prochain changement
            # de scénario. Écrire cfg depuis ce fil est sans danger — au pire
            # le pont reconstruit sa source une fois de trop.
            if r["ok"] and self.ais:
                self.ais.cfg = None

        threading.Thread(target=travail, daemon=True).start()
        return {"ok": True, "etat": "en cours"}

    def commander_pompe_terrain(self, marche):
        """Écrit la bobine pompe du capteur de terrain (fc5, adresse 1).

        Sans FIELD_HOST, on est en mode purement logiciel : rien à écrire,
        et l'affectation locale déjà faite dans command() suffit. Avec un
        automate réel dans la boucle, c'est ce point-là — pas l'affichage —
        qui fait foi ; on l'écrit dans un fil pour ne jamais bloquer la
        boucle de simulation sur un réseau lent ou indisponible.
        """
        if not FIELD_HOST:
            return

        def travail():
            cli = ModbusTcp(FIELD_HOST, FIELD_PORT, timeout=2.0)
            try:
                cli.write_coil(1, marche)
            except Exception:
                pass
            finally:
                cli.close()

        threading.Thread(target=travail, daemon=True).start()

    def commander_tir_armement(self, effecteur):
        """Écrit %MW0 sur l'automate — un mot mémoire, pas une bobine %Q, et
        décalé de +1024 dans l'espace d'adressage Modbus d'OpenPLC (les
        registres de maintien 0-1023 sont %QW, 1024-2047 sont %MW ; vérifié
        dans le code source d'OpenPLC_v3, pas deviné). Écrit le code
        effecteur puis 255 (repos) peu après : c'est ce front qui déclenche
        un coup côté programme armement, voir plc/program.st.

        Vit sur l'automate lui-même (PLC_HOST), pas sur field-sim — pas de
        capteur physique à simuler ici, tout est interne au programme.
        """
        code = {"sam": 0, "ciws": 1, "gun": 2, "ssm": 3}.get(effecteur)
        if code is None or not PLC_HOST:
            return

        def travail():
            cli = ModbusTcp(PLC_HOST, PLC_PORT, timeout=2.0)
            try:
                cli.write_register(1024, code)
                time.sleep(0.15)
                cli.write_register(1024, 255)
            except Exception:
                pass
            finally:
                cli.close()

        threading.Thread(target=travail, daemon=True).start()

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
            # Un événement de scénario ("pompe" coupée à t+60s, par exemple)
            # fait la même affectation locale que la commande "pump" du
            # poste instructeur — et se heurte au même piège en mode
            # automate réel : le pont réécrit platform.pump à chaque
            # sondage Modbus et l'écraserait aussitôt. On rejoue donc vers
            # le capteur de terrain tout événement "pompe" du journal qu'on
            # n'a pas encore vu, exactement comme le fait la commande F10.
            with self.lock:
                seq_max, marche = self._pompe_seq_vu, None
                for ev in self.engine.events:
                    if ev["n"] <= self._pompe_seq_vu:
                        break
                    seq_max = max(seq_max, ev["n"])
                    if ev.get("code") == "pompe" and marche is None:
                        marche = ev["p"]["marche"]
                self._pompe_seq_vu = seq_max
            if marche is not None:
                self.commander_pompe_terrain(marche)
            if now - pub >= 0.1:
                pub = now
                with self.lock:
                    f = self.engine.snapshot()
                    f["meta"] = self.meta
                    f["rate"] = self.rate
                    f["ais_feed"] = ({"etat": self.ais.etat, "n": self.ais.n,
                                      "source": self.ais.etiquette_source()}
                                     if self.ais else None)
                    f["capture"] = self.capture
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
                # Bloc armement : maintien 10-13 (munitions), bobines 8-19
                # (%QX1.0-1.3 prêt, %QX2.0-2.3 défaut — voir modbus-map.md).
                # Lu dans la même passe puisque c'est le même automate ; sans
                # le programme armement chargé, ça lit des zéros, pas une
                # erreur — la console affichera juste « 0 munitions ».
                regs_arm = self.cli.read_holding_registers(10, 4)
                coils_arm = self.cli.read_coils(8, 12)
                with self.sim.lock:
                    self.sim.engine.platform.ingest(regs, coils)
                    self.sim.engine.armement.ingest(regs_arm, coils_arm)
            except Exception:
                self.cli.close()
                with self.sim.lock:
                    self.sim.engine.platform.source = "SIMULÉ"
                    self.sim.engine.armement.source = "SIMULÉ"
                time.sleep(3.0)
                continue
            time.sleep(0.2)


def demarrer_ais(sim):
    """Monte le pont AIS, qui suivra ensuite le scénario courant.

    Le pont est toujours démarré : il coûte un fil endormi, et il permet à
    un changement de scénario depuis la console d'allumer la source toute
    seule. Sans cela, choisir « veille en trafic réel » dans le menu
    donnerait un scope vide jusqu'à ce qu'on pense à relancer le serveur
    avec la bonne variable d'environnement.
    """
    from services.ais import AisBridge
    pont = AisBridge(sim)
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
    # 0.0.0.0 est une adresse d'écoute — « toutes les interfaces » — pas une
    # adresse qu'un navigateur sait ouvrir : Windows et Safari la refusent
    # net. Afficher l'adresse de liaison telle quelle envoie donc
    # l'utilisateur sur une URL morte alors que le serveur marche.
    visible = "localhost" if HOST in ("0.0.0.0", "::", "") else HOST
    print("CMS-Lab sur http://%s:%d  (scénario %s, IPMS %s, AIS %s)"
          % (visible, PORT, DEFAULT_SC,
             "Modbus " + PLC_HOST if PLC_HOST else "simulé",
             AIS_SOURCE or "éteint"),
          flush=True)
    if visible != HOST:
        print("           écoute sur %s:%d — accessible aussi depuis le réseau local"
              % (HOST, PORT), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
