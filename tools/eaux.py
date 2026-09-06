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

from sim.geo import NM, Projection            # noqa: E402

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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--carte", default=str(CARTE))
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--marge", type=float, default=MARGE_NM,
                    help="eau libre attendue autour du porteur, en NM")
    a = ap.parse_args()

    doc = json.loads(Path(a.carte).read_text(encoding="utf-8"))
    if not doc.get("terres"):
        print("La carte ne porte pas de polygones de terre. Régénérez-la :")
        print("  python3 tools/coastline.py ne_10m_coastline.geojson \\")
        print("      --terres ne_10m_land.geojson --lat .. --lon .. -o web/coastline.json")
        return 2

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
    return 1 if mauvais else 0


if __name__ == "__main__":
    sys.exit(main())
