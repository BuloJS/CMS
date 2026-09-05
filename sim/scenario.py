"""Chargement des scénarios.

Les fichiers sont écrits en unités du domaine — milles nautiques, nœuds,
pieds — et convertis en SI à l'entrée. Le format est TOML, lu par
`tomllib` de la bibliothèque standard : aucune dépendance à installer,
donc une image Docker sans `pip install`.
"""
import tomllib
from pathlib import Path

from .ais import decode as decode_ais
from .entities import Contact
from .geo import FT, KT, NM, to_xy

WEAPONS = {
    # missile antinavire rasant : c'est lui qui définit le tempo du domaine
    "asm": dict(kind="missile", rcs=0.09, alt=5.0, speed=270.0, name="Missile antinavire"),
    "asm_supersonic": dict(kind="missile", rcs=0.15, alt=12.0, speed=680.0,
                           name="Missile antinavire supersonique"),
}


def load(path):
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    sc = {
        "name": data.get("name", Path(path).stem),
        "brief": data.get("brief", ""),
        "attendu": data.get("attendu", ""),
        "seed": int(data.get("seed", 1)),
        "duration": float(data.get("duration", 900)),
        "ownship": data.get("ownship", {}),
        # Ancre le plan tangent local sur la carte. Sans elle le scénario
        # reste purement relatif — ce qui suffisait tant que rien de réel
        # n'entrait dans le système.
        "origine": data.get("origine", {}),
        "events": sorted(data.get("event", []), key=lambda e: e.get("at", 0)),
        "contacts": [],
    }
    for c in data.get("contact", []):
        x, y = to_xy(float(c["brg"]), float(c["rng_nm"]) * NM)
        sc["contacts"].append(Contact(
            uid=c["id"], name=c.get("name", c["id"]), kind=c.get("kind", "surf"),
            x=x, y=y, alt=float(c.get("alt_ft", 0)) * FT,
            course=float(c.get("course", 0)), speed=float(c.get("speed_kt", 0)) * KT,
            rcs=float(c.get("rcs", 100)), iff=bool(c.get("iff", False)),
            ais=bool(c.get("ais", False)),
            # Le scénario écrit le statique AIS dans les champs bruts de la
            # norme, comme le ferait un vrai message : un scénario et un
            # navire réel doivent être indiscernables en aval.
            ais_static=decode_ais(c.get("ais_data", {})) if c.get("ais_data") else {},
            emitters=list(c.get("emitters", [])),
            intent=c.get("intent", "neutral")))
    return sc
