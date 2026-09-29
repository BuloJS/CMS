#!/usr/bin/env python3
"""Frontières et noms des pays, pour la console.

  * `ne_10m_admin_0_boundary_lines_land` — les frontières terrestres, en
    polylignes (les côtes viennent déjà de web/coastline.json / monde.json) ;
  * `ne_50m_admin_0_countries` — un point d'étiquette, un niveau de zoom et un
    nom français par pays.

    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\\
master/geojson/ne_10m_admin_0_boundary_lines_land.geojson
    curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/\\
master/geojson/ne_50m_admin_0_countries.geojson
    python3 tools/pays.py ne_10m_admin_0_boundary_lines_land.geojson \\
        ne_50m_admin_0_countries.geojson -o web/pays.json

Frontières arrondies au centième de degré (~1 km) : le tracé d'une frontière
sur un scope à 100 milles n'en demande pas plus. Ce sont les frontières
reconnues par Natural Earth, pas une position politique.
"""
import argparse
import json
from pathlib import Path


def frontieres(doc):
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
            ln = []
            for lon, lat in part:
                p = [round(lon, 2), round(lat, 2)]
                if not ln or ln[-1] != p:
                    ln.append(p)
            if len(ln) > 1:
                out.append(ln)
    return out


def pays(doc):
    out = []
    for f in doc["features"]:
        p = f["properties"]
        if p.get("TYPE") == "Dependency" and (p.get("POP_EST") or 0) < 1e5:
            pass                                   # petits territoires : gardés, niveau de zoom élevé
        en = p.get("NAME_EN") or p.get("NAME")
        fr = p.get("NAME_FR") or en
        lx, ly = p.get("LABEL_X"), p.get("LABEL_Y")
        if lx is None or ly is None:
            continue
        out.append([en, fr if fr != en else "", round(ly, 2), round(lx, 2),
                    p.get("MIN_LABEL") or 5, int(p.get("POP_EST") or 0)])
    out.sort(key=lambda c: -c[5])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frontieres", help="ne_10m_admin_0_boundary_lines_land.geojson")
    ap.add_argument("pays", help="ne_50m_admin_0_countries.geojson")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    doc = {"source": "Natural Earth (domaine public)",
           "frontieres": frontieres(json.loads(Path(a.frontieres).read_text(encoding="utf-8"))),
           "pays": pays(json.loads(Path(a.pays).read_text(encoding="utf-8")))}
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("%s : %d frontières, %d pays, %.0f ko" % (a.out, len(doc["frontieres"]),
          len(doc["pays"]), Path(a.out).stat().st_size / 1e3))


if __name__ == "__main__":
    main()
