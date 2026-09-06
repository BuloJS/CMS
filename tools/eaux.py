#!/usr/bin/env python3
"""Vérifie que les scénarios sont posés dans de vraies eaux.

Une origine de scénario n'est pas un détail cosmétique. Elle décide de ce
que le trait de côte montre à l'écran, de la quantité d'eau libre autour du
porteur, et — dès qu'on branche l'AIS — de la zone dans laquelle on va
chercher du trafic réel. Un bâtiment de veille posé à deux milles d'un port,
au milieu des skerries, se voit tout de suite et n'a aucun sens.

L'œil ne suffit pas à trancher : sur un scope à cinquante milles, la
différence entre « au large » et « dans les cailloux » tient à quelques
pixels. Ce contrôle s'appuie donc sur les polygones de terre découpés par
`tools/coastline.py`, et rend un chiffre.

    python3 tools/eaux.py                 # tous les scénarios
    python3 tools/eaux.py --lat 59.9 --lon 25.25

Limite à connaître : Natural Earth 10 m ne porte pas les petits îlots. Une
position déclarée « au large » ici peut encore se trouver dans un archipel
de détail. Le contrôle attrape la faute grossière, pas la subtile.
"""
import argparse
import json
import math
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.geo import KT, NM, Projection, vel   # noqa: E402
from sim.scenario import load as charger      # noqa: E402

CARTE = ROOT / "web" / "coastline.json"
MARGE_NM = 8.0      # en deçà, on est en eaux resserrées


def _anneaux(doc):
    for poly in doc.get("terres", []):
        for ring in poly:
            yield ring


def a_terre(lon, lat, doc):
    """Rayon lancé vers l'est. Un point dans un nombre impair d'anneaux est
    à terre ; les anneaux intérieurs — lacs — s'annulent d'eux-mêmes."""
    n = 0
    for ring in _anneaux(doc):
        dedans = False
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > lat) != (yj > lat) and \
                    lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                dedans = not dedans
            j = i
        if dedans:
            n += 1
    return n % 2 == 1


def distance_terre(lon, lat, doc):
    """Distance au segment de côte le plus proche, en mètres."""
    proj = Projection(lat, lon)
    px, py = 0.0, 0.0                    # le point est l'origine du plan
    best = float("inf")
    for ring in _anneaux(doc):
        prev = None
        for lo, la in ring:
            x, y = proj.to_xy(la, lo)
            if prev is not None:
                ax, ay = prev
                dx, dy = x - ax, y - ay
                l2 = dx * dx + dy * dy
                t = 0.0 if l2 == 0 else max(0.0, min(
                    1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
                if d < best:
                    best = d
            prev = (x, y)
    return best


def juger(lat, lon, doc, marge_nm=MARGE_NM):
    """Rend (verdict, distance en NM). Verdict : terre, resserre, ou large."""
    if a_terre(lon, lat, doc):
        return "terre", 0.0
    d = distance_terre(lon, lat, doc) / NM
    return ("resserre" if d < marge_nm else "large"), d


def route(lat, lon, cap_deg, vitesse_kt, duree_s, doc, pas_s=60.0):
    """Suit une route en ligne droite et dit si elle finit par toucher terre.

    Un contact correctement posé au départ ne prouve rien : à quatorze nœuds
    pendant un quart d'heure, un navire parcourt trois milles et demi, et une
    route mal choisie le fait traverser une île sans que personne ne l'ait
    voulu. C'est le défaut qui se voit le plus à l'écran, parce qu'il se voit
    *pendant* qu'on regarde.

    Rend (instant du premier échouage en secondes, ou None ; distance
    minimale à la côte le long de la route, en NM).
    """
    proj = Projection(lat, lon)
    vx, vy = vel(cap_deg, vitesse_kt * KT)
    mini, t = float("inf"), 0.0
    while t <= duree_s:
        la, lo = proj.to_latlon(vx * t, vy * t)
        if a_terre(lo, la, doc):
            return t, 0.0
        mini = min(mini, distance_terre(lo, la, doc))
        t += pas_s
    return None, mini / NM


def contacts_du_scenario(sc, doc, marge_nm=0.5):
    """Contrôle chaque contact scripté : sa position, puis sa route.

    La marge est bien plus faible que pour le porteur : un scénario a de
    bonnes raisons de placer une vedette près de la côte — elle en sort,
    c'est le sujet. Ce qu'on veut interdire, c'est la terre ferme.
    """
    og = sc.get("origine") or {}
    if "lat" not in og:
        return []
    proj = Projection(og["lat"], og["lon"])
    duree = float(sc.get("duration", 600))
    out = []
    for c in sc.get("contacts", []):
        if c.kind != "surf":
            continue                       # un aéronef survole ce qu'il veut
        la, lo = proj.to_latlon(c.x, c.y)
        v, d = juger(la, lo, doc, marge_nm)
        quand, mini = (None, d)
        if v != "terre":
            quand, mini = route(la, lo, c.course, c.speed / KT, duree, doc)
        out.append({"id": c.uid, "nom": c.name, "lat": la, "lon": lo,
                    "verdict": "terre" if v == "terre" else
                               ("echoue" if quand is not None else "ok"),
                    "distance_nm": d, "mini_nm": mini, "echoue_a": quand})
    return out


def inspecter_capture(chemin, doc):
    """Liste les navires d'un instantané AIS que la carte place à terre.

    Attendu sur une capture réelle, et ce n'est pas une erreur du flux : un
    navire à quai *est* dans un bassin portuaire, que Natural Earth ne
    modélise pas. Le pont les écarte sur leur statut déclaré, pas sur la
    géométrie — mais il reste utile de voir ce que contient une capture,
    ne serait-ce que pour vérifier que le filtre fait son travail.
    """
    from services.ais import A_QUAI, Fichier
    src = Fichier(chemin)
    pos = src.positions()
    a_terre_l, quai = [], 0
    for p in pos:
        st = src.statique.get(p["mmsi"], {})
        if p.get("navStat") in A_QUAI:
            quai += 1
        if a_terre(p["lon"], p["lat"], doc):
            a_terre_l.append((st.get("name") or p["mmsi"], p, st))

    print("%s — %d navires, %d se déclarent à quai ou échoués"
          % (chemin, len(pos), quai))
    if not a_terre_l:
        print("  aucun sur la terre selon la carte.")
        return 0
    print("  %d sur la terre selon la carte :" % len(a_terre_l))
    for nom, p, st in a_terre_l[:30]:
        ecarte = "écarté" if p.get("navStat") in A_QUAI else "CONSERVÉ"
        print("    %-22s %8.4fN %8.4fE  %5.1f kt  %-22s %s"
              % (str(nom)[:22], p["lat"], p["lon"], p["sog"],
                 (st.get("navstat") or "statut inconnu")[:22], ecarte))
    reste = [x for x in a_terre_l if x[1].get("navStat") not in A_QUAI]
    if reste:
        print("  %d resteraient affichés sur la terre. Causes ordinaires :"
              % len(reste))
        print("    un chenal entre des îlots que Natural Earth 10 m ne porte")
        print("    pas, ou un navire en manœuvre dans un port sans avoir")
        print("    encore basculé son statut.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--carte", default=str(CARTE))
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--marge", type=float, default=MARGE_NM,
                    help="eau libre attendue autour du porteur, en NM")
    ap.add_argument("--capture", help="inspecte un instantané AIS au lieu "
                                      "des scénarios")
    a = ap.parse_args()

    doc = json.loads(Path(a.carte).read_text(encoding="utf-8"))
    if not doc.get("terres"):
        print("La carte ne porte pas de polygones de terre. Régénérez-la :")
        print("  python3 tools/coastline.py ne_10m_coastline.geojson \\")
        print("      --terres ne_10m_land.geojson --lat .. --lon .. -o web/coastline.json")
        return 2

    if a.capture:
        return inspecter_capture(a.capture, doc)

    ETIQ = {"terre": "À TERRE", "resserre": "eaux resserrées", "large": "au large"}
    if a.lat is not None and a.lon is not None:
        v, d = juger(a.lat, a.lon, doc, a.marge)
        print("%.4f N %.4f E — %s, côte la plus proche %.2f NM"
              % (a.lat, a.lon, ETIQ[v], d))
        return 0 if v == "large" else 1

    mauvais = 0
    for p in sorted((ROOT / "scenarios").glob("*.toml")):
        o = tomllib.loads(p.read_text(encoding="utf-8")).get("origine")
        if not o:
            print("%-30s pas d'ancrage géographique" % p.stem)
            continue
        v, d = juger(o["lat"], o["lon"], doc, a.marge)
        print("%-30s %-17s %6.2f NM de la côte" % (p.stem, ETIQ[v], d))
        if v != "large":
            mauvais += 1
        for c in contacts_du_scenario(charger(p), doc):
            if c["verdict"] == "ok":
                continue
            mauvais += 1
            if c["verdict"] == "terre":
                print("    %-14s %-22s À TERRE au départ" % (c["id"], c["nom"][:22]))
            else:
                print("    %-14s %-22s s'échoue à t+%.0f s"
                      % (c["id"], c["nom"][:22], c["echoue_a"]))
    return 1 if mauvais else 0


if __name__ == "__main__":
    sys.exit(main())
