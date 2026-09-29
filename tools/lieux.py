#!/usr/bin/env python3
"""Noms de lieux pour la console : villes, capitales, mers, océans, détroits.

Deux fichiers Natural Earth (domaine public), un seul JSON compact en sortie :

  * `ne_10m_populated_places` — on garde les capitales d'État et toute ville
    de plus de 100 000 habitants (~3 100 lieux, environ 250 ko) ;
  * `ne_10m_geography_marine_polys` — océans, mers, golfes, détroits, canaux…
    avec leur contour, pour répondre à « dans quelle mer suis-je ? », et un
    point d'étiquette pour les écrire sur la carte.

    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\\
master/geojson/ne_10m_populated_places.geojson
    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\\
master/geojson/ne_10m_geography_marine_polys.geojson
    python3 tools/lieux.py ne_10m_populated_places.geojson \\
        ne_10m_geography_marine_polys.geojson -o web/lieux.json

Les mers sont triées de la plus petite à la plus grande : le premier contour
qui contient un point est donc le plus précis (détroit avant mer avant océan).
Les contours sont arrondis — au dixième de degré, au centième pour les petits
(un détroit de 20 milles ne survivrait pas au dixième).
"""
import argparse
import json
from pathlib import Path

POP_MIN = 100000


def _nom(props, cle_en="NAME_EN", cle="NAME"):
    n = props.get(cle_en) or props.get(cle) or ""
    return n.title() if n.isupper() else n


def villes(doc):
    out = []
    for f in doc["features"]:
        p = f["properties"]
        cap = p.get("FEATURECLA") in ("Admin-0 capital", "Admin-0 capital alt")
        pop = int(p.get("POP_MAX") or 0)
        if not cap and pop < POP_MIN:
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        en = p.get("NAME_EN") or p.get("NAME")
        fr = p.get("NAME_FR") or en
        out.append([en, fr if fr != en else "", round(lat, 2), round(lon, 2),
                    pop, 1 if cap else 0, p.get("ADM0NAME") or ""])
    out.sort(key=lambda v: (-v[5], -v[4]))
    return out


def _aire(ring):
    """Aire signée (formule du lacet), en degrés carrés — sert au tri et au
    centroïde, pas à une mesure."""
    a = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        a += x0 * y1 - x1 * y0
    return a / 2.0


def _dans(ring, lon, lat):
    dedans = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            dedans = not dedans
        j = i
    return dedans


def _point_etiquette(ring):
    """Centroïde s'il est dans le contour, sinon le point intérieur d'une
    grille le plus proche de lui (un contour concave — la Baltique — a un
    centroïde en pleine terre)."""
    a = _aire(ring)
    if abs(a) < 1e-9:
        return ring[0][1], ring[0][0]
    cx = sum((x0 + x1) * (x0 * y1 - x1 * y0)
             for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])) / (6 * a)
    cy = sum((y0 + y1) * (x0 * y1 - x1 * y0)
             for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])) / (6 * a)
    if _dans(ring, cx, cy):
        return cy, cx
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    best, bd = (ring[0][1], ring[0][0]), 1e18
    n = 24
    for i in range(n + 1):
        for j in range(n + 1):
            x = min(xs) + (max(xs) - min(xs)) * i / n
            y = min(ys) + (max(ys) - min(ys)) * j / n
            if _dans(ring, x, y):
                d = (x - cx) ** 2 + (y - cy) ** 2
                if d < bd:
                    best, bd = (y, x), d
    return best


def _arrondi(ring, nd):
    out = []
    for lon, lat in ring:
        p = [round(lon, nd), round(lat, nd)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def mers(doc):
    out = []
    for f in doc["features"]:
        p = f["properties"]
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon":
            polys = [g["coordinates"]]
        elif g.get("type") == "MultiPolygon":
            polys = g["coordinates"]
        else:
            continue
        if p.get("featurecla") in ("reef", "river"):
            continue
        anneaux = [poly[0] for poly in polys]      # contours extérieurs seulement
        plus_grand = max(anneaux, key=lambda r: abs(_aire(r)))
        aire = sum(abs(_aire(r)) for r in anneaux)
        xs = [x for r in anneaux for x, _ in r]
        ys = [y for r in anneaux for _, y in r]
        nd = 2 if (max(xs) - min(xs) < 3 and max(ys) - min(ys) < 3) else 1
        lat, lon = _point_etiquette(plus_grand)
        en = (p.get("name_en") or p.get("name") or "")
        en = en.title() if en.isupper() else en
        fr = p.get("name_fr") or ""
        out.append({
            "n": en, "f": fr if fr != en else "", "c": p.get("featurecla"),
            "z": p.get("min_label"), "p": [round(lat, 2), round(lon, 2)],
            "a": round(aire, 2),
            "poly": [r for r in (_arrondi(a, nd) for a in anneaux) if len(r) > 3],
        })
    out.sort(key=lambda m: m["a"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("villes", help="ne_10m_populated_places.geojson")
    ap.add_argument("mers", help="ne_10m_geography_marine_polys.geojson")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    doc = {
        "source": "Natural Earth (domaine public)",
        "villes": villes(json.loads(Path(a.villes).read_text(encoding="utf-8"))),
        "mers": mers(json.loads(Path(a.mers).read_text(encoding="utf-8"))),
    }
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
    print("%s : %d villes (%d capitales), %d mers, %.0f ko"
          % (a.out, len(doc["villes"]), sum(v[5] for v in doc["villes"]),
             len(doc["mers"]), Path(a.out).stat().st_size / 1e3))


if __name__ == "__main__":
    main()
