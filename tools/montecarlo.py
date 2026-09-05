#!/usr/bin/env python3
"""Mode analyse : N runs headless, statistiques en sortie.

C'est la raison d'être d'un cœur sans écran. La même logique qui alimente
la console répond ici à des questions du type « avec cette doctrine, quelle
est la probabilité qu'un missile passe ? » — en faisant varier la graine et
en comptant.

    python3 tools/montecarlo.py scenarios/02-saturation-asm.toml -n 50
"""
import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.engine import Engine            # noqa: E402
from sim.scenario import load            # noqa: E402


def run(path, seed, doctrine):
    # Rechargement complet à chaque run. Le scénario porte des objets Contact
    # que le moteur fait bouger et détruit : une copie superficielle ferait
    # repartir le run suivant de l'état final du précédent, et les
    # statistiques ne voudraient plus rien dire.
    sc = load(path)
    sc["seed"] = seed
    e = Engine(sc)
    e.doctrine.update(doctrine)
    while e.t < sc["duration"]:
        e.step()
    impacts = sum(1 for ev in e.events if "IMPACT" in ev["txt"])
    sam = next(x for x in e.effectors if x.key == "sam")
    first = next((ev["t"] for ev in reversed(e.events)
                  if "classée HOSTILE" in ev["txt"]), None)
    return {"impacts": impacts, "sam": 16 - sam.rounds, "premiere_id": first}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("--csv")
    ap.add_argument("--no-auto-sam", dest="auto_sam", action="store_false", default=True)
    a = ap.parse_args()

    base = load(a.scenario)
    doctrine = {"auto_id": True, "auto_sam": a.auto_sam}
    rows = [run(a.scenario, base["seed"] + i, doctrine) for i in range(a.n)]

    imp = [r["impacts"] for r in rows]
    sam = [r["sam"] for r in rows]
    ids = [r["premiere_id"] for r in rows if r["premiere_id"] is not None]
    fuite = sum(1 for v in imp if v > 0) / len(imp)

    print("%s — %d runs, doctrine SAM auto %s"
          % (base["name"], a.n, "armée" if a.auto_sam else "désarmée"))
    print("  probabilité de fuite (>=1 impact) : %.1f %%" % (fuite * 100))
    print("  impacts par run                   : moy %.2f  max %d" % (statistics.fmean(imp), max(imp)))
    print("  munitions SAM consommées          : moy %.1f  max %d" % (statistics.fmean(sam), max(sam)))
    if ids:
        print("  première identification hostile   : moy %.0f s  min %.0f s"
              % (statistics.fmean(ids), min(ids)))
    if a.csv:
        with open(a.csv, "w", encoding="utf-8") as fh:
            fh.write("run,impacts,sam,premiere_id\n")
            for i, r in enumerate(rows):
                fh.write("%d,%d,%d,%s\n" % (i, r["impacts"], r["sam"], r["premiere_id"]))
        print("  -> %s" % a.csv)


if __name__ == "__main__":
    main()
