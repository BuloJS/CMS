#!/usr/bin/env python3
"""Découpe un fond de carte Natural Earth pour une zone d'opérations.

Le fichier mondial 10 m fait dix mégaoctets. Un scope n'en montre jamais
qu'une poignée de polylignes. On découpe donc une fois, à la main, et la
console charge quelques dizaines de kilo-octets.

Deux couches, et il faut les deux :

  * le **trait de côte** (`ne_10m_coastline`), des polylignes, qui donne le
    dessin fin du littoral ;
  * les **polygones de terre** (`ne_10m_land`), qui permettent de remplir.
    Un trait seul ne dit pas de quel côté est la mer — sur un scope dense
    c'est illisible, et ça ne permet pas non plus de vérifier qu'un
    scénario est bien posé dans l'eau.

    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\
master/geojson/ne_10m_coastline.geojson
    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\
master/geojson/ne_10m_land.geojson
    python3 tools/coastline.py ne_10m_coastline.geojson \
        --terres ne_10m_land.geojson \
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


def _couper_bord(points, bord, seuil, garder_sup):
    """Un côté du découpage de Sutherland-Hodgman."""
    def dedans(p):
        v = p[0] if bord == "x" else p[1]
        return v >= seuil if garder_sup else v <= seuil
    def couper(a, b):
        i = 0 if bord == "x" else 1
        j = 1 - i
        if b[i] == a[i]:
            return list(a)
        t = (seuil - a[i]) / (b[i] - a[i])
        out = [0.0, 0.0]
        out[i] = seuil
        out[j] = a[j] + t * (b[j] - a[j])
        return out

    out = []
    n = len(points)
    for k in range(n):
        a, b = points[k - 1], points[k]
        da, db = dedans(a), dedans(b)
        if db:
            if not da:
                out.append(couper(a, b))
            out.append(list(b))
        elif da:
            out.append(couper(a, b))
    return out


def clip_polygone(ring, lon_min, lat_min, lon_max, lat_max):
    """Sutherland-Hodgman contre un rectangle.

    Le rectangle est convexe, donc l'algorithme s'applique tel quel. Il peut
    laisser des arêtes dégénérées le long du bord sur un contour concave —
    sans conséquence pour un remplissage, qui est le seul usage ici.
    """
    pts = [list(p) for p in ring]
    for bord, seuil, sup in (("x", lon_min, True), ("x", lon_max, False),
                             ("y", lat_min, True), ("y", lat_max, False)):
        if not pts:
            return []
        pts = _couper_bord(pts, bord, seuil, sup)
    return pts


def terres(features, lon_min, lat_min, lon_max, lat_max):
    """Contours de terre découpés à la zone, prêts à être remplis.

    Les anneaux intérieurs (lacs, mers intérieures) sont conservés tels
    quels : la console les dessine dans le même chemin, et la règle de
    remplissage pair-impair les creuse toute seule.
    """
    out = []
    for f in features:
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon":
            polys = [g["coordinates"]]
        elif g.get("type") == "MultiPolygon":
            polys = g["coordinates"]
        else:
            continue
        for poly in polys:
            decoupe = []
            for ring in poly:
                lons = [p[0] for p in ring]
                lats = [p[1] for p in ring]
                if max(lons) < lon_min or min(lons) > lon_max \
                        or max(lats) < lat_min or min(lats) > lat_max:
                    continue
                c = clip_polygone(ring, lon_min, lat_min, lon_max, lat_max)
                if len(c) > 2:
                    decoupe.append([[round(x, 5), round(y, 5)] for x, y in c])
            if decoupe:
                out.append(decoupe)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="GeoJSON Natural Earth (ne_10m_coastline.geojson)")
    ap.add_argument("--lat", type=float, required=True, help="latitude de référence")
    ap.add_argument("--lon", type=float, required=True, help="longitude de référence")
    ap.add_argument("--rayon", type=float, default=120.0, help="demi-côté en NM")
    ap.add_argument("--terres", help="GeoJSON des polygones de terre (ne_10m_land)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    data = json.loads(Path(a.source).read_text(encoding="utf-8"))
    # Un mille nautique de latitude vaut une minute d'arc, par définition.
    # En longitude il en faut d'autant plus qu'on est haut en latitude.
    half_lat = a.rayon / 60.0
    half_lon = a.rayon / 60.0 / max(math.cos(math.radians(a.lat)), 0.05)

    lines = clip(data.get("features", []), a.lat, a.lon, half_lat, half_lon)
    doc = {"ref": [a.lat, a.lon], "rayon_nm": a.rayon, "lignes": lines,
           "source": "Natural Earth 10m (domaine public)"}

    if a.terres:
        land = json.loads(Path(a.terres).read_text(encoding="utf-8"))
        doc["terres"] = terres(land.get("features", []),
                               a.lon - half_lon, a.lat - half_lat,
                               a.lon + half_lon, a.lat + half_lat)

    body = json.dumps(doc, separators=(",", ":"))
    Path(a.out).write_text(body, encoding="utf-8")
    pts = sum(len(l) for l in lines)
    nt = sum(len(r) for p in doc.get("terres", []) for r in p)
    print("%s — %d polylignes (%d points), %d contours de terre (%d points), %.1f ko"
          % (a.out, len(lines), pts, len(doc.get("terres", [])), nt,
             len(body) / 1024.0))


if __name__ == "__main__":
    main()
