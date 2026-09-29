"""Signal K — le simulateur publié comme une source Signal K standard.

Signal K (https://signalk.org) est un modèle de données JSON pour la
navigation : des chemins normalisés (`navigation.position`), des unités SI
(radians, mètres par seconde, hertz), un contexte par objet (`vessels.<id>`).
En publiant l'instantané du simulateur dans ce modèle, tout client Signal K —
OpenCPN, Kip, WilhelmSK, un tableau de bord maison — voit le porteur, ses
pistes et ses alertes sans rien savoir de CMS-Lab.

Ce module ne touche pas au cœur `sim/` : il convertit l'instantané que la
console reçoit déjà (`Engine.snapshot()`). Il est **désactivé par défaut** ;
`SIGNALK=1` l'active (voir docker-compose.signalk.yml).

Ce qui est publié (voir `releves()`) :

  * le porteur, en `vessels.self` : position, cap, vitesse, barre, RPM,
    tableau électrique, ordre de cap ;
  * chaque piste radar, en `vessels.urn:mrn:cms:track:<T012>` : position,
    cap, vitesse, identité AIS quand il y en a une ;
  * chaque site connu (batterie…), en `atons.urn:mrn:cms:site:<id>` ;
  * les alertes (verrouillage, départ missile, impact, échouement…), en
    `notifications.cms.*` sur le porteur.

Ce que Signal K ne sait pas dire — affiliation, qualité de piste, TCPA/CPA,
score de menace, zone radar d'un site — va sous un espace de noms à nous,
`cms.*`. Les clients standard l'ignorent sans dommage.

Transport : l'API REST (`/signalk/v1/api/…`) et le flux WebSocket de deltas
(`/signalk/v1/stream`), écrit à la main — pas de dépendance, comme le reste.
"""
import base64
import hashlib
import json
import math
import struct
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

VERSION = "1.7.0"
KT = 1852.0 / 3600.0            # nœud -> m/s
NM = 1852.0
SELF_ID = "urn:mrn:signalk:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_DNS, "cms-lab.ownship"))
SELF_CTX = "vessels." + SELF_ID
SOURCE = {"label": "cms-lab", "type": "simulator"}
GUID_WS = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"      # RFC 6455

# Alertes du journal -> notifications Signal K : (état, message anglais).
# Les états sont ceux de la spécification : normal, alert, warn, alarm, emergency.
ALERTES = {
    "verrouillage": ("alarm", "Enemy fire control lock on own ship, bearing {brg:03d}°"),
    "illumination": ("alarm", "Fire-control illumination on track {piste}"),
    "depart_missile": ("emergency", "Missile launch detected — origin {src}"),
    "impact": ("emergency", "Impact on own ship — {nom}"),
    "echouement": ("emergency", "Grounding — own ship has touched the coast"),
    "cible_detruite": ("alert", "{nom} destroyed by missile"),
    "leurres": ("normal", "Decoys deployed — {n} missile(s) seduced"),
}


def maintenant():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _position(frame, x_nm, y_nm):
    """(x, y) en NM relatifs au porteur -> position Signal K, ou None sans géo."""
    g, own = frame.get("geo"), frame.get("own") or {}
    if not g or "lat" not in own:
        return None
    return {"latitude": round(own["lat"] + y_nm * NM / g["m_lat"], 6),
            "longitude": round(own["lon"] + x_nm * NM / g["m_lon"], 6)}


def _ajoute(out, ctx, path, valeur):
    if valeur is not None:
        out.setdefault(ctx, []).append((path, valeur))


def releves(frame):
    """L'instantané -> {contexte: [(chemin, valeur), ...]}, en unités SI.

    Pure et sans état : la même fonction sert l'API REST (on plie la liste en
    arbre) et le flux WebSocket (on l'envoie en deltas). Les valeurs `None`
    — un RPM que l'automate ne fournit pas, une position sans géoréférence —
    sont omises plutôt que publiées vides.
    """
    out = {}
    own = frame.get("own") or {}
    A = lambda p, v: _ajoute(out, SELF_CTX, p, v)          # noqa: E731

    A("name", "CMS-Lab own ship")
    pos = _position(frame, 0.0, 0.0)
    A("navigation.position", pos)
    if own:
        A("navigation.courseOverGroundTrue", round(math.radians(own["crs"]), 5))
        A("navigation.headingTrue", round(math.radians(own["crs"]), 5))
        A("navigation.speedOverGround", round(own["spd"] * KT, 3))
        A("navigation.speedThroughWater", round(own["spd"] * KT, 3))
        A("steering.autopilot.target.headingTrue", round(math.radians(own["ord_crs"]), 5))
        A("cms.orderedSpeed", round(own["ord_spd"] * KT, 3))
    m = frame.get("machine") or {}
    if m.get("barre") is not None:
        A("steering.rudderAngle", round(math.radians(m["barre"]), 5))
    if m.get("rpm") is not None:
        A("propulsion.main.revolutions", round(m["rpm"] / 60.0, 3))     # Hz
    if "propulsion_dispo" in m and m["propulsion_dispo"] is not None:
        A("propulsion.main.state", "started" if m["propulsion_dispo"] else "stopped")
        A("electrical.switches.battery.state", bool(m.get("batterie")))
        A("electrical.switches.generator.state", bool(m.get("generateur_pret")))
        A("electrical.switches.breaker.state", bool(m.get("disjoncteur")))
    A("cms.simulation.time", frame.get("t"))
    A("cms.simulation.scenario", frame.get("scenario"))
    A("cms.simulation.electricalSource", m.get("source"))
    r = frame.get("radar") or {}
    if r:
        A("cms.radar.maxRange", round(r.get("max_nm", 0) * NM, 1))
        A("cms.radar.surfaceHorizon", round(r.get("horizon_nm", 0) * NM, 1))

    for t in frame.get("tracks") or []:
        ctx = "vessels.urn:mrn:cms:track:" + t["id"]
        B = lambda p, v, c=ctx: _ajoute(out, c, p, v)      # noqa: E731
        ais = t.get("ais") or {}
        B("name", ais.get("name") or t.get("ident") or t["id"])
        B("navigation.position", _position(frame, t["x"], t["y"]))
        B("navigation.courseOverGroundTrue", round(math.radians(t["crs"]), 5))
        B("navigation.speedOverGround", round(t["spd"] * KT, 3))
        if ais.get("mmsi"):
            B("mmsi", str(ais["mmsi"]))
        if ais.get("callsign"):
            B("communication.callsignVhf", ais["callsign"])
        if ais.get("imo"):
            B("registrations.imo", "IMO " + str(ais["imo"]))
        if ais.get("dest"):
            B("navigation.destination.commonName", ais["dest"])
        if ais.get("loa"):
            B("design.length", {"overall": ais["loa"]})
        if ais.get("beam"):
            B("design.beam", ais["beam"])
        if ais.get("draught"):
            B("design.draft", {"maximum": ais["draught"]})
        B("cms.affiliation", t.get("aff"))
        B("cms.trackQuality", t.get("qual"))
        B("cms.iffResponse", t.get("iff"))
        B("cms.emitter", t.get("emitter") or None)
        B("cms.threatScore", t.get("score"))
        B("cms.cpa", round((t.get("cpa") or 0) * NM, 1))
        B("cms.tcpa", t["tcpa"] if t.get("tcpa", -1) >= 0 else None)

    for s in frame.get("sites") or []:
        ctx = "atons.urn:mrn:cms:site:" + s["id"]
        C = lambda p, v, c=ctx: _ajoute(out, c, p, v)      # noqa: E731
        C("name", s["nom"])
        C("navigation.position", _position(frame, s["x"], s["y"]))
        C("cms.kind", s.get("kind"))
        C("cms.zoneRadius", round(s.get("zone_nm", 0) * NM, 1))
        C("cms.state", s.get("etat"))
        C("cms.shotsFired", s.get("tirs"))
    return out


def notifications(frame, depuis_n=None):
    """Alertes du journal -> [(chemin, valeur)] pour le porteur.

    `depuis_n` : ne garder que les événements plus récents que ce numéro (le
    flux WebSocket n'envoie une alerte qu'une fois). Sans lui, la dernière
    alerte de chaque type — l'état courant, pour l'API REST.
    """
    out, vus = [], set()
    for ev in frame.get("events") or []:                       # plus récent d'abord
        code = ev.get("code")
        if code not in ALERTES:
            continue
        if depuis_n is not None and ev["n"] <= depuis_n:
            break
        if depuis_n is None and code in vus:
            continue
        vus.add(code)
        etat, modele = ALERTES[code]
        try:
            msg = modele.format(**(ev.get("p") or {}))
        except (KeyError, ValueError, IndexError):
            msg = modele
        out.append(("notifications.cms." + code,
                    {"state": etat, "method": ["visual", "sound"], "message": msg}))
    crash = frame.get("crash")
    if crash:
        out.append(("notifications.cms.crash",
                    {"state": "emergency", "method": ["visual", "sound"],
                     "message": "Simulation halted — " + ("missile hit" if crash.get("type") == "missile"
                                                           else "grounding")}))
    return out


def en_deltas(rel, notifs=(), ts=None, contextes=None):
    """`releves()` -> liste de deltas Signal K. `contextes` filtre (None = tous)."""
    ts = ts or maintenant()
    deltas = []
    for ctx, valeurs in rel.items():
        if contextes is not None and ctx not in contextes:
            continue
        vals = [{"path": p, "value": v} for p, v in valeurs]
        if ctx == SELF_CTX:
            vals += [{"path": p, "value": v} for p, v in notifs]
        deltas.append({"context": ctx,
                       "updates": [{"source": SOURCE, "timestamp": ts, "values": vals}]})
    return deltas


def _range(arbre, chemin, feuille):
    """Pose `feuille` à `chemin` (avec points) dans l'arbre imbriqué."""
    *tete, dernier = chemin.split(".")
    for k in tete:
        arbre = arbre.setdefault(k, {})
    arbre[dernier] = feuille


def modele(frame, ts=None):
    """Le modèle complet (`GET /signalk/v1/api/`) : feuilles {value, timestamp,
    $source} rangées en arbre par contexte."""
    ts = ts or maintenant()
    rel = releves(frame)
    racine = {"version": VERSION, "self": SELF_CTX,
              "sources": {"cms-lab": {"label": "CMS-Lab simulator", "type": "simulator"}}}
    for ctx, valeurs in list(rel.items()):
        if ctx == SELF_CTX:
            valeurs = valeurs + notifications(frame)
        famille, ident = ctx.split(".", 1)
        noeud = racine.setdefault(famille, {}).setdefault(ident, {})
        for chemin, v in valeurs:
            _range(noeud, chemin, {"value": v, "timestamp": ts, "$source": "cms-lab"})
    return racine


def resoudre(arbre, chemin):
    """`/vessels/self/navigation/position` -> sous-arbre, ou None. `self`
    est l'alias du porteur, comme le veut la spécification."""
    segs = [s for s in chemin.split("/") if s]
    if not segs:
        return arbre
    if len(segs) >= 2 and segs[0] == "vessels" and segs[1] == "self":
        segs[1] = SELF_ID
    noeud = arbre
    for s in segs:
        if not isinstance(noeud, dict) or s not in noeud:
            return None
        noeud = noeud[s]
    return noeud


# ---------------------------------------------------------------- WebSocket

def cle_acceptation(cle):
    return base64.b64encode(hashlib.sha1((cle + GUID_WS).encode()).digest()).decode()


def trame_texte(texte):
    """Une trame WebSocket texte serveur -> client (non masquée)."""
    data = texte.encode("utf-8")
    n = len(data)
    if n < 126:
        tete = struct.pack("!BB", 0x81, n)
    elif n < 65536:
        tete = struct.pack("!BBH", 0x81, 126, n)
    else:
        tete = struct.pack("!BBQ", 0x81, 127, n)
    return tete + data


def lire_trame(rfile):
    """Lit une trame client -> (opcode, charge utile) ; (None, b"") à la fermeture."""
    def exact(n):
        buf = b""
        while len(buf) < n:
            morceau = rfile.read(n - len(buf))
            if not morceau:
                return None
            buf += morceau
        return buf
    tete = exact(2)
    if not tete:
        return None, b""
    opcode, n = tete[0] & 0x0F, tete[1] & 0x7F
    masque = bool(tete[1] & 0x80)
    if n == 126:
        n = struct.unpack("!H", exact(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", exact(8))[0]
    cle = exact(4) if masque else None
    data = exact(n) if n else b""
    if data is None or (masque and cle is None):
        return None, b""
    if masque:
        data = bytes(b ^ cle[i % 4] for i, b in enumerate(data))
    return opcode, data


def _flux(handler, sim, requete):
    """Le flux de deltas d'un client. `subscribe` : self (défaut, comme la
    spécification), all, none."""
    cle = handler.headers.get("Sec-WebSocket-Key", "")
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", cle_acceptation(cle))
    handler.end_headers()
    handler.close_connection = True

    mode = (requete.get("subscribe") or ["self"])[0]
    try:
        periode = max(0.1, float((requete.get("period") or ["1.0"])[0]))
    except ValueError:
        periode = 1.0
    ecrire = lambda obj: handler.wfile.write(trame_texte(json.dumps(obj, ensure_ascii=False)))   # noqa: E731
    vivant = threading.Event()
    vivant.set()

    def lecteur():
        # Le client peut pinger, fermer, ou (re)souscrire — le prototype
        # n'honore que ping et fermeture, et les « subscribe » de message.
        nonlocal mode
        while vivant.is_set():
            try:
                op, data = lire_trame(handler.rfile)
            except (OSError, ValueError, struct.error):
                break
            if op is None or op == 0x8:
                break
            if op == 0x9:
                try:
                    handler.wfile.write(b"\x8a" + bytes([len(data)]) + data)
                except OSError:
                    break
            elif op == 0x1:
                try:
                    msg = json.loads(data.decode("utf-8"))
                    if isinstance(msg, dict) and "subscribe" in msg:
                        mode = "all" if msg.get("context") in ("*", "vessels.*") else "self"
                except (ValueError, UnicodeDecodeError):
                    pass
        vivant.clear()

    threading.Thread(target=lecteur, daemon=True).start()
    try:
        ecrire({"name": "cms-lab", "version": VERSION, "self": SELF_CTX,
                "roles": ["master", "main"], "timestamp": maintenant()})
        rev, dernier_n, derniere = -1, None, 0.0
        vus, derniers = set(), {}
        while vivant.is_set():
            frame, rev = sim.wait(rev, 1.0)
            if not frame or time.monotonic() - derniere < periode:
                continue
            derniere = time.monotonic()
            if mode == "none":
                continue
            rel = releves(frame)
            evs = frame.get("events") or []
            if dernier_n is None:                       # pas d'historique à la connexion
                dernier_n = evs[0]["n"] if evs else 0
            notifs = notifications(frame, dernier_n)
            if evs:
                dernier_n = evs[0]["n"]
            contextes = {SELF_CTX} if mode == "self" else None
            # Seules les valeurs qui ont changé partent : un flux à 1 Hz de
            # cinquante pistes n'a pas à répéter ce qui n'a pas bougé.
            filtre = {}
            for ctx, valeurs in rel.items():
                if contextes is not None and ctx not in contextes:
                    continue
                nouveaux = [(p, v) for p, v in valeurs if derniers.get((ctx, p)) != v]
                for p, v in nouveaux:
                    derniers[(ctx, p)] = v
                if nouveaux:
                    filtre[ctx] = nouveaux
            # Une piste qui disparaît est dite perdue, une fois.
            presents = set(rel)
            if mode == "all":
                for ctx in vus - presents:
                    filtre[ctx] = [("cms.status", "lost")]
            vus = presents
            for d in en_deltas(filtre, notifs):
                ecrire(d)
            if not filtre and notifs:
                for d in en_deltas({SELF_CTX: []}, notifs):
                    ecrire(d)
    except (OSError, ValueError):
        pass
    finally:
        vivant.clear()


# --------------------------------------------------------------------- HTTP

def repondre(handler, sim, url):
    """Traite `url` s'il est de Signal K et rend True ; False sinon (le
    serveur continue avec ses routes habituelles)."""
    p = urlparse(url)
    chemin = p.path
    if not chemin.startswith("/signalk"):
        return False
    hote = handler.headers.get("Host", "localhost")
    envoie = lambda code, obj: handler._send(code, json.dumps(obj, ensure_ascii=False).encode(),   # noqa: E731
                                              "application/json")
    if chemin.rstrip("/") == "/signalk":
        return envoie(200, {
            "endpoints": {"v1": {"version": VERSION,
                                 "signalk-http": "http://%s/signalk/v1/api/" % hote,
                                 "signalk-ws": "ws://%s/signalk/v1/stream" % hote}},
            "server": {"id": "cms-lab", "version": "prototype"}}) or True
    if chemin.rstrip("/") == "/signalk/v1/stream":
        if handler.headers.get("Upgrade", "").lower() != "websocket":
            return handler._send(426, b"WebSocket requis") or True
        _flux(handler, sim, parse_qs(p.query))
        return True
    if chemin.startswith("/signalk/v1/api"):
        frame, _ = sim.wait(-1, 0.1)
        if not frame:
            return envoie(503, {"error": "pas encore de trame"}) or True
        sous = resoudre(modele(frame), chemin[len("/signalk/v1/api"):])
        if sous is None:
            return handler._send(404, b"introuvable") or True
        return envoie(200, sous) or True
    return False
