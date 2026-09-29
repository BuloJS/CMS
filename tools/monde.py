#!/usr/bin/env python3
"""Fond de carte du monde entier, pour poser un scénario n'importe où.

`web/coastline.json` (tools/coastline.py) est un découpage fin — Natural
Earth 10 m — autour d'une seule zone. Ce script fabrique l'autre moitié :
le monde entier en Natural Earth 50 m, arrondi au centième de degré (~1 km),
dans le même format. La console s'en sert dès que l'origine d'un scénario
sort de la zone fine, et le découpe elle-même autour du porteur.

    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\\
master/geojson/ne_50m_coastline.geojson
    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\\
master/geojson/ne_50m_land.geojson
    python3 tools/monde.py ne_50m_coastline.geojson ne_50m_land.geojson \\
        -o web/monde.json

50 m et non 10 m : le 10 m mondial pèse vingt mégaoctets, le 50 m trois. À
110 NM de portée le littoral reste lisible ; ce qu'on perd, ce sont les
petits îlots et les détails de port.

Données Natural Earth, domaine public.
"""
import argparse
import json
import math
from pathlib import Path

try:
    from tools.coastline import clip_polygone
except ImportError:                       # lancé comme script : python3 tools/monde.py
    from coastline import clip_polygone


def _arrondi(points, nd=2):
    """Arrondit et retire les points consécutifs devenus identiques."""
    out = []
    for lon, lat in points:
        p = [round(lon, nd), round(lat, nd)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def lignes(doc):
    out = []
    for f in doc["features"]:
        g = f.get("geometry") or {}
        if g.get("type") == "LineString":
            parts = [g["coordinates"]]
        elif g.get("type") == "MultiLineString":
            parts = g["coordinates"]
        else:
            continue
        for part in parts:
            ln = _arrondi(part)
            if len(ln) > 1:
                out.append(ln)
    return out


def terres(doc):
    out = []
    for f in doc["features"]:
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon":
            polys = [g["coordinates"]]
        elif g.get("type") == "MultiPolygon":
            polys = g["coordinates"]
        else:
            continue
        for poly in polys:
            anneaux = [_arrondi(r) for r in poly]
            anneaux = [r for r in anneaux if len(r) > 3]
            if anneaux:
                out.append(anneaux)
    return out


def decouper(monde, lat0, lon0, rayon_nm=300.0):
    """Le monde découpé à une boîte autour de (lat0, lon0), au même format
    que `web/coastline.json` — ce que fait aussi la console côté client.

    Sert aux contrôles hors ligne (tools/eaux.py) : tester un point contre
    1 400 polygones mondiaux à chaque pas d'une route serait absurde, contre
    la poignée qui touche la zone ça ne l'est plus.
    """
    dlat = rayon_nm / 60.0
    dlon = min(179.0, dlat / max(math.cos(math.radians(lat0)), 0.05))
    lat_min, lat_max = max(-90.0, lat0 - dlat), min(90.0, lat0 + dlat)
    out = {"ref": [lat0, lon0], "rayon_nm": rayon_nm, "lignes": [], "terres": [],
           "source": monde.get("source", "")}
    # Trois copies décalées de 360° : une zone à cheval sur l'antiméridien
    # voit ainsi ses deux rives dans un repère continu.
    for sh in (0.0, -360.0, 360.0):
        lo_min, lo_max = lon0 - dlon, lon0 + dlon
        if lo_max < -180.0 + sh or lo_min > 180.0 + sh:
            continue
        for ln in monde["lignes"]:
            run = []
            for lon, lat in ln:
                lon += sh
                if lo_min <= lon <= lo_max and lat_min <= lat <= lat_max:
                    run.append([lon, lat])
                elif run:
                    run.append([lon, lat])
                    if len(run) > 1:
                        out["lignes"].append(run)
                    run = []
            if len(run) > 1:
                out["lignes"].append(run)
        for poly in monde["terres"]:
            dec = []
            for ring in poly:
                lons = [p[0] + sh for p in ring]
                lats = [p[1] for p in ring]
                if max(lons) < lo_min or min(lons) > lo_max \
                        or max(lats) < lat_min or min(lats) > lat_max:
                    continue
                c = clip_polygone([[x + sh, y] for x, y in ring],
                                  lo_min, lat_min, lo_max, lat_max)
                if len(c) > 2:
                    dec.append(c)
            if dec:
                out["terres"].append(dec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("coastline", help="ne_50m_coastline.geojson")
    ap.add_argument("land", help="ne_50m_land.geojson")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    doc = {
        "monde": True,
        "source": "Natural Earth 50m (domaine public)",
        "lignes": lignes(json.loads(Path(a.coastline).read_text(encoding="utf-8"))),
        "terres": terres(json.loads(Path(a.land).read_text(encoding="utf-8"))),
    }
    Path(a.out).write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    print("%s : %d lignes, %d polygones, %.1f Mo"
          % (a.out, len(doc["lignes"]), len(doc["terres"]),
             Path(a.out).stat().st_size / 1e6))


if __name__ == "__main__":
    main()
