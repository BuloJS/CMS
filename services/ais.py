#!/usr/bin/env python3
"""Ingestion de trafic maritime réel par AIS.

Le cœur ne connaît ni le réseau ni l'écran. Ce service lui apporte des
`Contact` fabriqués à partir de messages AIS réels — et rien d'autre. Une
fois entrés dans le monde, ces navires sont indiscernables de ceux qu'un
scénario écrit à la main : le radar les voit selon la même équation en R⁴,
l'horizon les coupe à la même distance, le pistage construit les mêmes
pistes. **La physique déjà écrite devient un filtre sur du trafic réel.**

Source par défaut : Digitraffic (Fintraffic), eaux finlandaises, sans clé
ni inscription, en JSON sur HTTPS — donc `urllib` de la bibliothèque
standard suffit et la règle zéro-dépendance tient. Un flux MQTT existe mais
imposerait un client.

    python3 services/ais.py --capture golfe.json     # enregistrer un instantané
    python3 services/ais.py --fichier golfe.json     # rejouer sans réseau
    AIS_SOURCE=digitraffic python3 services/server.py

Le parseur est volontairement tolérant. Deux raisons : un message AIS réel
est souvent incomplet — un navire diffuse sa position toutes les quelques
secondes mais son statique toutes les six minutes — et l'enveloppe exacte
d'une API publique n'est pas un contrat gravé. Ce qui manque est absent,
jamais remplacé par une valeur par défaut trompeuse.
"""
import argparse
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.ais import decode as decode_ais, rcs_from_length   # noqa: E402
from sim.entities import Contact                            # noqa: E402
from sim.geo import KT, NM                                  # noqa: E402

BASE = "https://meri.digitraffic.fi/api/ais/v1"
# Digitraffic demande que chaque client s'identifie. Ce n'est pas une clé,
# c'est une politesse d'exploitation : elle leur permet de joindre l'auteur
# d'un trafic anormal plutôt que de bloquer une plage d'adresses.
UA = "CMS-Lab/1.0 (simulateur de systeme de combat naval; usage laboratoire)"

# Valeurs « non disponible » de la norme UIT-R M.1371. Les laisser passer
# donne des navires à 102 nœuds au pôle Nord, ce qui se voit — mais
# seulement une fois que le pistage a construit la piste.
SOG_INDISPO = 102.3
COG_INDISPO = 360.0


# --------------------------------------------------------------------- #
# Lecture de la source
# --------------------------------------------------------------------- #

def _get(url, timeout=12.0):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Digitraffic-User": UA,
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _features(doc):
    """Rend une liste d'enregistrements, quelle que soit l'enveloppe.

    GeoJSON (`FeatureCollection`), tableau nu, ou objet portant une liste :
    on accepte les trois. Deviner l'enveloppe coûte dix lignes ; se tromper
    coûte un service qui ne rend rien sans dire pourquoi.
    """
    if isinstance(doc, list):
        return doc
    if not isinstance(doc, dict):
        return []
    for cle in ("features", "vessels", "locations", "data", "items"):
        v = doc.get(cle)
        if isinstance(v, list):
            return v
    return []


def _flatten(rec):
    """Aplatit un enregistrement : mmsi, coordonnées et propriétés au même
    niveau, d'où qu'ils viennent dans la structure."""
    out = {}
    if not isinstance(rec, dict):
        return out
    out.update({k: v for k, v in rec.items()
                if k not in ("geometry", "properties", "type")})
    props = rec.get("properties")
    if isinstance(props, dict):
        out.update(props)
    geom = rec.get("geometry")
    if isinstance(geom, dict) and isinstance(geom.get("coordinates"), (list, tuple)):
        c = geom["coordinates"]
        if len(c) >= 2:
            out.setdefault("lon", c[0])
            out.setdefault("lat", c[1])
    return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def normalise(rec):
    """Enregistrement brut -> position exploitable, ou None.

    Rejette tout ce qui n'a pas de MMSI ni de position valide. Les valeurs
    « non disponible » de la norme sont traitées comme absentes, pas comme
    des mesures.
    """
    r = _flatten(rec)
    mmsi = r.get("mmsi")
    lat, lon = _num(r.get("lat")), _num(r.get("lon"))
    if mmsi is None or lat is None or lon is None:
        return None
    # 91 / 181 sont les sentinelles « position inconnue » de la norme.
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    sog = _num(r.get("sog"))
    if sog is None or sog >= SOG_INDISPO or sog < 0:
        sog = 0.0
    cog = _num(r.get("cog"))
    if cog is None or cog >= COG_INDISPO:
        # Sans route sur le fond, le cap compas est le meilleur substitut.
        cog = _num(r.get("heading"))
    if cog is None or not (0.0 <= cog < 360.0):
        cog = None

    return {"mmsi": str(int(mmsi)), "lat": lat, "lon": lon,
            "sog": sog, "cog": cog,
            "navStat": r.get("navStat"),
            "time": _num(r.get("timestampExternal")) or _num(r.get("time")) or 0.0}


class Digitraffic:
    """Source réseau. Interroge positions et statique, et les recolle par MMSI."""

    def __init__(self, base=BASE, timeout=12.0):
        self.base = base
        self.timeout = timeout
        self.statique = {}          # mmsi -> champs décodés, mis en cache
        self.etat = "jamais interrogé"

    def positions(self, lat=None, lon=None, rayon_nm=None):
        """Positions du moment, filtrées côté serveur si possible.

        Les paramètres de zone allègent la réponse quand le service les
        honore. Le filtrage par distance est de toute façon refait côté
        client : s'ils sont ignorés, on télécharge plus, on ne rend pas
        faux. En revanche l'unité du rayon n'est pas un contrat vérifié —
        on suppose des kilomètres — donc un refus du serveur fait retomber
        sur la requête nue plutôt que de tout perdre.
        """
        nu = self.base + "/locations"
        url = nu
        if lat is not None and lon is not None and rayon_nm:
            url += "?latitude=%.5f&longitude=%.5f&radius=%.1f" % (
                lat, lon, rayon_nm * NM / 1000.0)
        try:
            doc = _get(url, self.timeout)
        except urllib.error.HTTPError as e:
            if url == nu or e.code not in (400, 404, 422):
                raise
            doc = _get(nu, self.timeout)
        return [p for p in (normalise(f) for f in _features(doc)) if p]

    def rafraichir_statique(self):
        """Le statique change rarement : on le relit de loin en loin et on
        le garde. C'est aussi ce que fait un récepteur AIS réel, qui ne
        reçoit un message 5 que toutes les six minutes par navire."""
        for rec in _features(_get(self.base + "/vessels", self.timeout)):
            r = _flatten(rec)
            if r.get("mmsi") is None:
                continue
            d = decode_ais(r)
            if d:
                self.statique[str(int(r["mmsi"]))] = d


class Fichier:
    """Source hors ligne : un instantané capturé plus tôt.

    Indispensable, et pas seulement pour les tests. Le flux public n'est
    pas toujours joignable — réseau d'entreprise, proxy, panne du service —
    et un lab qui ne démarre pas sans internet n'est pas un lab.
    """

    def __init__(self, path):
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        self._pos = [p for p in (normalise(f) for f in doc.get("positions", [])) if p]
        self.statique = doc.get("statique", {})
        self.etat = "fichier"

    def positions(self, lat=None, lon=None, rayon_nm=None):
        return list(self._pos)

    def rafraichir_statique(self):
        pass


# --------------------------------------------------------------------- #
# Conversion en contacts du simulateur
# --------------------------------------------------------------------- #

def en_contact(pos, statique, proj, defaut_rcs=600.0):
    """Position AIS + statique -> `Contact` de la vérité terrain.

    Le `Contact` produit est un navire ordinaire du monde simulé. Il n'a
    aucune marque d'origine : c'est délibéré. Ce qui distingue un contact
    réel d'un contact inventé, en aval, c'est ce que les senseurs en font —
    rien d'autre.
    """
    st = statique.get(pos["mmsi"], {})
    x, y = proj.to_xy(pos["lat"], pos["lon"])
    rcs = rcs_from_length(st.get("loa"), st.get("type_code"))
    return Contact(
        uid="AIS-" + pos["mmsi"],
        name=st.get("name") or ("MMSI " + pos["mmsi"]),
        kind="surf",
        x=x, y=y,
        course=pos["cog"] if pos["cog"] is not None else 0.0,
        speed=pos["sog"] * KT,
        rcs=rcs if rcs is not None else defaut_rcs,
        ais=True,
        ais_static=dict(st, mmsi=pos["mmsi"]),
        # Un navire marchand n'est ni ami ni ennemi : il est là. C'est au
        # TEWA et à l'opérateur de trancher, jamais à la source de données.
        intent="neutral",
    )


class AisBridge(threading.Thread):
    """Injecte le trafic réel dans le monde du simulateur.

    Même contrat que le pont Modbus : s'il n'y a pas de flux, on ne casse
    rien. Le scénario tourne seul, la console affiche l'état de la source,
    et un retour du réseau reprend sans redémarrage.

    Entre deux messages, on ne fige pas les navires : on les laisse
    naviguer sur leur dernière route connue. C'est déjà ce que fait
    `Contact.step()`, et c'est aussi ce que fait un vrai système entre deux
    réceptions — un navire au mouillage n'émet que toutes les trois
    minutes, un navire rapide toutes les deux secondes.
    """
    daemon = True

    def __init__(self, sim, source=None, rayon_nm=60.0, periode=6.0, oubli=600.0):
        super().__init__()
        self.sim = sim
        self.source = source
        self.cfg = None             # configuration en vigueur, suivie du scénario
        self.rayon_nm = rayon_nm
        self.periode = periode
        self.oubli = oubli          # s sans nouvelle -> le navire sort du monde
        self.etat = "éteint" if source is None else "démarrage"
        self.n = 0
        self.vus = {}               # uid -> horodatage de simulation
        # Dernier message effectivement appliqué, par navire. Sans lui, un
        # message inchangé — le cas normal pour un navire lent, qui n'émet
        # que toutes les trois minutes — replacerait le contact à sa
        # position d'il y a trois minutes à chaque interrogation. Le navire
        # avancerait puis reculerait, et le filtre lirait une vitesse
        # divisée par deux. C'est le genre de faute qui ne lève rien et qui
        # ne se voit que sur la vitesse affichée.
        self.applique = {}          # uid -> (horodatage, lat, lon)

    def _proj(self):
        with self.sim.lock:
            return self.sim.engine.proj

    def _suivre_scenario(self):
        """Aligne la source sur ce que demande le scénario courant.

        Un changement de scénario depuis la console doit suffire : c'est le
        scénario qui sait s'il a besoin du flux, pas l'exploitant qui doit
        s'en souvenir au lancement. Rend False quand aucune source n'est
        demandée — le pont dort alors sans rien consommer.
        """
        cfg = getattr(self.sim, "ais_cfg", None)
        if cfg == self.cfg:
            return self.source is not None
        self.cfg, self.source = cfg, None
        self.vus, self.applique, self.n = {}, {}, 0
        if not cfg:
            self.etat = "éteint"
            return False
        try:
            if cfg["source"] == "fichier":
                chemin = Path(cfg["fichier"])
                self.source = Fichier(chemin if chemin.is_absolute()
                                      else ROOT / chemin)
            else:
                self.source = Digitraffic()
        except (OSError, ValueError, json.JSONDecodeError) as e:
            self.etat = "source illisible (%s)" % type(e).__name__
            return False
        self.rayon_nm = cfg["rayon_nm"]
        self.periode = cfg["periode"]
        self.etat = "démarrage"
        self._statique_le = 0.0
        return True

    def _cycle(self):
        proj = self._proj()
        if proj is None:
            self.etat = "scénario sans [origine]"
            return
        positions = self.source.positions(proj.lat0, proj.lon0, self.rayon_nm)
        statique = getattr(self.source, "statique", {})

        limite = self.rayon_nm * NM
        with self.sim.lock:
            eng = self.sim.engine
            monde, t = eng.world, eng.t
            for p in positions:
                c = en_contact(p, statique, proj)
                if math.hypot(c.x - eng.own.x, c.y - eng.own.y) > limite:
                    continue
                signature = (p["time"], p["lat"], p["lon"])
                neuf = self.applique.get(c.uid) != signature
                ancien = monde.get(c.uid)
                if ancien is None:
                    monde[c.uid] = c
                else:
                    # Mise à jour en place : le pistage suit un contact, pas
                    # un objet. Le remplacer romprait la corrélation par
                    # proximité et referait naître une piste à chaque tour.
                    #
                    # La cinématique ne se recale que sur un message neuf.
                    # Entre deux, le contact continue sur sa dernière route
                    # connue — ce que fait `Contact.step()`, et ce que fait
                    # tout système qui reçoit des positions espacées.
                    if neuf:
                        (ancien.x, ancien.y) = (c.x, c.y)
                        ancien.course, ancien.speed = c.course, c.speed
                    # Le statique, lui, se rafraîchit toujours : il arrive
                    # par un autre message, à une autre cadence.
                    ancien.rcs, ancien.name = c.rcs, c.name
                    ancien.ais_static = c.ais_static
                self.applique[c.uid] = signature
                self.vus[c.uid] = t

            for uid, vu in list(self.vus.items()):
                if t - vu > self.oubli:
                    monde.pop(uid, None)
                    self.vus.pop(uid, None)
                    self.applique.pop(uid, None)
            self.n = len(self.vus)
        self.etat = "%s — %d navires" % (
            "AIS RÉEL" if not isinstance(self.source, Fichier) else "AIS FICHIER", self.n)

    def run(self):
        self._statique_le = 0.0
        while True:
            if not self._suivre_scenario():
                time.sleep(2.0)
                continue
            try:
                if time.monotonic() - self._statique_le > 300.0:
                    self.source.rafraichir_statique()
                    self._statique_le = time.monotonic()
                self._cycle()
                attente = self.periode
            except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                    json.JSONDecodeError, ValueError) as e:
                # Dégradation, jamais d'arrêt : le lab reste utilisable et
                # dit à l'écran que la source est tombée.
                self.etat = "flux indisponible (%s)" % type(e).__name__
                attente = max(self.periode, 20.0)
            time.sleep(attente)


# --------------------------------------------------------------------- #
# Ligne de commande : capturer, inspecter
# --------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lat", type=float, default=60.10)
    ap.add_argument("--lon", type=float, default=24.90)
    ap.add_argument("--rayon", type=float, default=60.0, help="rayon en NM")
    ap.add_argument("--capture", help="écrit un instantané rejouable dans ce fichier")
    ap.add_argument("--fichier", help="lit un instantané au lieu du réseau")
    a = ap.parse_args()

    src = Fichier(a.fichier) if a.fichier else Digitraffic()
    src.rafraichir_statique()
    pos = src.positions(a.lat, a.lon, a.rayon)
    print("%d positions, %d navires au statique connu" % (len(pos), len(src.statique)))

    from sim.geo import Projection
    proj = Projection(a.lat, a.lon)
    contacts = [en_contact(p, src.statique, proj) for p in pos]
    contacts = [c for c in contacts if math.hypot(c.x, c.y) <= a.rayon * NM]
    contacts.sort(key=lambda c: math.hypot(c.x, c.y))
    print("%d dans le rayon de %.0f NM\n" % (len(contacts), a.rayon))
    for c in contacts[:25]:
        st = c.ais_static
        print("  %-9s %-22s %5.1f NM  %5.1f kt  %-22s %8.0f m²"
              % (st.get("mmsi", "?"), (c.name or "")[:22],
                 math.hypot(c.x, c.y) / NM, c.speed / KT,
                 (st.get("type") or "—")[:22], c.rcs))

    if a.capture:
        Path(a.capture).write_text(json.dumps(
            {"positions": pos, "statique": src.statique,
             "ref": [a.lat, a.lon], "rayon_nm": a.rayon},
            ensure_ascii=False), encoding="utf-8")
        print("\ninstantané -> %s" % a.capture)


if __name__ == "__main__":
    main()
