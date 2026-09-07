#!/usr/bin/env python3
"""Rejoue un scénario en headless et écrit un enregistrement JSONL.

Le même fichier alimente l'aperçu hors ligne de la console (artefact) et
sert de jeu de données de référence pour développer le front sans lancer la
stack. Une graine fixée rend l'enregistrement reproductible au tick près.

    python3 tools/record.py scenarios/02-saturation-asm.toml -o fixtures/x.jsonl
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.engine import DT, Engine          # noqa: E402
from sim.scenario import load              # noqa: E402


def slim(f):
    """Allège une trame pour l'embarquement : une trace sur deux suffit à
    dessiner le sillage, et le détail des facteurs de menace n'est utile que
    sur les premières pistes du classement."""
    for i, t in enumerate(f["tracks"]):
        t["trail"] = t["trail"][::2]
        t["x"], t["y"] = round(t["x"], 3), round(t["y"], 3)
        t["ell"] = [round(v, 3) for v in t["ell"]]
        if i >= 4:
            t.pop("fact", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--hz", type=float, default=1.0, help="cadence d'échantillonnage")
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--until", type=float, default=None)
    ap.add_argument("--slim", action="store_true",
                    help="allège les trames pour l'embarquement dans un artefact")
    ap.add_argument("--auto-id", action="store_true", default=True)
    ap.add_argument("--no-auto-id", dest="auto_id", action="store_false")
    ap.add_argument("--auto-sam", action="store_true", default=True)
    ap.add_argument("--no-auto-sam", dest="auto_sam", action="store_false")
    a = ap.parse_args()

    sc = load(a.scenario)
    eng = Engine(sc)
    eng.doctrine.update(auto_id=a.auto_id, auto_sam=a.auto_sam)
    end = a.until if a.until is not None else sc["duration"]
    every = max(1, round(1.0 / a.hz / DT))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        # En-tête : tout ce qu'il faut pour rejouer sans le simulateur.
        fh.write(json.dumps({"meta": {
            "scenario": sc["name"], "brief": sc["brief"], "attendu": sc["attendu"],
            # La traduction voyage avec l'enregistrement : l'artefact hors
            # ligne doit pouvoir basculer de langue comme la console servie.
            "en": sc.get("en") or {},
            "seed": sc["seed"], "hz": a.hz, "duration": end,
            "doctrine": {"auto_id": a.auto_id, "auto_sam": a.auto_sam},
        }}, ensure_ascii=False) + "\n")
        i = 0
        while eng.t < end:
            eng.step()
            i += 1
            if i % every == 0 and eng.t >= a.t0:
                snap = eng.snapshot()
                if a.slim:
                    slim(snap)
                fh.write(json.dumps(snap, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")
                n += 1
    print("%s : %d trames, %.1f Ko" % (out, n, out.stat().st_size / 1024))


if __name__ == "__main__":
    main()
