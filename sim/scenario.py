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
from .geo import FT, KT, NM, Projection, to_xy

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
        # Traduction facultative. Un scénario sans elle s'affiche en
        # français quelle que soit la langue de la console — mieux vaut du
        # texte dans la mauvaise langue qu'un cadre vide.
        "en": {k[:-3]: v for k, v in data.items()
               if k.endswith("_en") and isinstance(v, str)},
        "seed": int(data.get("seed", 1)),
        "duration": float(data.get("duration", 900)),
        "ownship": data.get("ownship", {}),
        # Surcharge de doctrine propre au scénario (auto_ciws, auto_sam…) :
        # un scénario de leurres n'a pas de sens si le CIWS règle tout seul.
        "doctrine": data.get("doctrine", {}),
        # Ancre le plan tangent local sur la carte. Sans elle le scénario
        # reste purement relatif — ce qui suffisait tant que rien de réel
        # n'entrait dans le système.
        "origine": data.get("origine", {}),
        # Un scénario dont tout le contenu vient du flux AIS doit le dire.
        # Sans cela il s'ouvre sur un scope vide, et rien à l'écran
        # n'explique pourquoi — c'est un piège, pas une configuration.
        "ais": data.get("ais", {}),
        "events": sorted(data.get("event", []), key=lambda e: e.get("at", 0)),
        "contacts": [],
    }
    # Un contact se pose soit par gisement/distance depuis le porteur, soit
    # par sa vraie latitude/longitude — au choix, contact par contact. La
    # seconde forme suppose un point de référence : celui de [origine], ou à
    # défaut la position du porteur lui-même. C'est ce qui permet de poser un
    # scénario n'importe où sur la carte sans calculer de gisements à la main.
    og = sc["origine"]
    own = sc["ownship"]
    if not og and "lat" in own and "lon" in own:
        og = sc["origine"] = {"lat": own["lat"], "lon": own["lon"]}
    proj = Projection(og["lat"], og["lon"]) if "lat" in og and "lon" in og else None
    for c in data.get("contact", []):
        if "lat" in c and "lon" in c:
            if proj is None:
                raise ValueError("contact %s : lat/lon sans [origine] ni position du porteur"
                                 % c.get("id", "?"))
            x, y = proj.to_xy(float(c["lat"]), float(c["lon"]))
        else:
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
            attaque=dict(c.get("attaque", {})),
            connu=bool(c.get("connu", False)),
            intent=c.get("intent", "neutral")))
    return sc
