#!/usr/bin/env python3
"""Découpe un trait de côte Natural Earth pour une zone d'opérations.

Le fichier mondial 10 m fait dix mégaoctets et quatre mille polylignes. Un
scope n'en montre jamais plus d'une poignée. On découpe donc une fois, à la
main, et la console charge un fichier de quelques dizaines de kilo-octets.

    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\
master/geojson/ne_10m_coastline.geojson
    python3 tools/coastline.py ne_10m_coastline.geojson \
        --lat 59.85 --lon 24.85 --rayon 120 -o web/coastline.json

Données Natural Earth, domaine public.
"""
import argparse
import json
import math
from pathlib import Path


def clip(features, lat0, lon0, half_lat, half_lon):
    """Garde les segments qui touchent la boîte, en coupant les polylignes.

    Découper plutôt que filtrer : une polyligne Natural Earth peut faire le
    tour de la Baltique. La garder entière parce qu'un de ses points est
    dans la zone ferait entrer tout le reste avec elle.
    """
    lat_min, lat_max = lat0 - half_lat, lat0 + half_lat
    lon_min, lon_max = lon0 - half_lon, lon0 + half_lon
    out = []
    for f in features:
        g = f.get("geometry") or {}
        if g.get("type") != "LineString":
            continue
        run = []
        for lon, lat in g["coordinates"]:
            inside = lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
            if inside:
                run.append([round(lon, 5), round(lat, 5)])
            elif run:
                # On garde le premier point sorti : sans lui la côte
                # s'arrête net au bord du cadre au lieu de le traverser.
                run.append([round(lon, 5), round(lat, 5)])
                if len(run) > 1:
                    out.append(run)
                run = []
        if len(run) > 1:
            out.append(run)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="GeoJSON Natural Earth (ne_10m_coastline.geojson)")
    ap.add_argument("--lat", type=float, required=True, help="latitude de référence")
    ap.add_argument("--lon", type=float, required=True, help="longitude de référence")
    ap.add_argument("--rayon", type=float, default=120.0, help="demi-côté en NM")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    data = json.loads(Path(a.source).read_text(encoding="utf-8"))
    # Un mille nautique de latitude vaut une minute d'arc, par définition.
    # En longitude il en faut d'autant plus qu'on est haut en latitude.
    half_lat = a.rayon / 60.0
    half_lon = a.rayon / 60.0 / max(math.cos(math.radians(a.lat)), 0.05)

    lines = clip(data.get("features", []), a.lat, a.lon, half_lat, half_lon)
    doc = {"ref": [a.lat, a.lon], "rayon_nm": a.rayon, "lignes": lines,
           "source": "Natural Earth 10m coastline (domaine public)"}
    body = json.dumps(doc, separators=(",", ":"))
    Path(a.out).write_text(body, encoding="utf-8")
    pts = sum(len(l) for l in lines)
    print("%s — %d polylignes, %d points, %.1f ko"
          % (a.out, len(lines), pts, len(body) / 1024.0))


if __name__ == "__main__":
    main()
