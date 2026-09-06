"""TEWA — Threat Evaluation and Weapon Assignment.

C'est le cœur d'un CMS ; le reste est de la plomberie. Deux produits :

  * une liste de menaces ordonnée, avec le détail des facteurs qui ont
    fait le score — un opérateur doit pouvoir contester le classement ;
  * des solutions d'engagement, chacune portant sa **butée de tir** :
    l'instant au-delà duquel il est trop tard. C'est ce compte à rebours
    qui pilote réellement une console de défense aérienne.

Le système propose, l'opérateur dispose. Rien ne part au tir sans une
action explicite, sauf si la doctrine d'auto-engagement a été armée.
"""
from math import ceil, log

from .geo import NM, cpa, intercept_time
from .veracite import gravite

REACTION_S = 8.0          # décision + désignation + séquence de mise de feu


def _clamp(v, a=0.0, b=1.0):
    return max(a, min(b, v))


def evaluate(track, own, t):
    """Score de menace 0..1, avec le détail des contributions."""
    x, y = track.pos
    vx, vy = track.vel
    ox, oy = own.vxy
    rx, ry = x - own.x, y - own.y
    d_cpa, tcpa = cpa(rx, ry, vx - ox, vy - oy)
    rngm = (rx * rx + ry * ry) ** 0.5

    f = {}
    if tcpa < 0:
        f["route"] = 0.0                       # s'ouvre : ce n'est pas une menace
    else:
        f["route"] = _clamp(1.0 - d_cpa / (8 * NM)) * 0.34
        f["temps"] = _clamp(1.0 - tcpa / 600.0) * 0.26
    f.setdefault("temps", 0.0)
    f["vitesse"] = _clamp((track.speed - 100.0) / 250.0) * 0.12
    f["profil"] = 0.14 if (track.speed > 120 and rngm < 40 * NM) else 0.0
    f["iff"] = 0.06 if track.iff == "pas de réponse" else (-0.10 if track.iff == "ami" else 0.0)
    f["esm"] = {"fc": 0.30, "search": 0.06}.get(track.emitter, 0.0)
    f["ident"] = {"hostile": 0.25, "friend": -0.60, "neutral": -0.25}.get(track.aff, 0.0)
    # Vraisemblance de la déclaration AIS. Le poids est délibérément modeste
    # et plafonné : une incohérence n'est pas une intention. Un GPS fatigué
    # et une dissimulation produisent le même écart, et le système n'a aucun
    # moyen de les distinguer — il ne doit donc pas prétendre le faire. Ce
    # facteur sert à faire remonter un contact dans la liste pour qu'un
    # opérateur le regarde, pas à le désigner.
    f["veracite"] = gravite(getattr(track, "anomalies", [])) * 0.16

    score = _clamp(sum(f.values()))
    return {"score": score, "facteurs": {k: round(v, 3) for k, v in f.items() if v},
            "cpa": d_cpa, "tcpa": tcpa, "rng": rngm}


class Effector:
    """Un effecteur et son enveloppe. Les canaux de conduite de tir sont la
    ressource rare : c'est elle qui crée le vrai problème d'ordonnancement,
    pas le nombre de munitions."""

    def __init__(self, key, label, role, v, rmin, rmax, alt_max=1e9,
                 pk=0.7, rounds=8, channels=2, targets=("air", "missile"),
                 cost=1):
        self.key, self.label, self.role = key, label, role
        self.cost = cost               # effet gradué : on n'ouvre pas au missile
                                       # antinavire sur une vedette à 9 NM
        self.v = v                     # m/s, vitesse moyenne de vol
        self.rmin, self.rmax = rmin, rmax
        self.alt_max = alt_max
        self.pk = pk
        self.rounds = rounds
        self.channels = channels
        self.busy = 0
        self.targets = targets

    @property
    def free_channels(self):
        return max(0, self.channels - self.busy)


def default_effectors():
    return [
        Effector("sam", "SAM courte portée", "défense", 900, 1.5 * NM, 25 * NM,
                 alt_max=20000, pk=0.72, rounds=16, channels=2, cost=8),
        Effector("ciws", "CIWS", "défense", 1100, 200, 2 * NM,
                 alt_max=3000, pk=0.55, rounds=999, channels=1, cost=2),
        Effector("gun", "Artillerie 76 mm", "attaque", 850, 0.4 * NM, 9 * NM,
                 pk=0.35, rounds=120, channels=1, targets=("surf",), cost=1),
        Effector("ssm", "Missile antinavire", "attaque", 290, 4 * NM, 60 * NM,
                 pk=0.8, rounds=8, channels=2, targets=("surf",), cost=40),
    ]


def salvo_for(pk, target_pk=0.90):
    """Taille de salve pour atteindre une probabilité de destruction visée."""
    if pk <= 0.01:
        return 99
    return max(1, ceil(log(1 - target_pk) / log(1 - pk)))


def solutions(tracks_eval, own, effectors, t, doctrine):
    """Construit les solutions d'engagement proposées à l'opérateur."""
    out = []
    for te in tracks_eval:
        tr, ev = te["track"], te["eval"]
        kind = "missile" if tr.speed > 200 and tr.quality > 0.2 else (
            "air" if tr.speed > 60 else "surf")
        hostile = tr.aff == "hostile"
        if not hostile:
            continue
        x, y = tr.pos
        vx, vy = tr.vel
        ox, oy = own.vxy
        rx, ry = x - own.x, y - own.y
        rngm = (rx * rx + ry * ry) ** 0.5

        for ef in effectors:
            if kind not in ef.targets:
                continue
            tof = intercept_time(rx, ry, vx - ox, vy - oy, ef.v)
            if tof is None:
                continue
            # Point d'interception : où la cible sera à l'issue du vol.
            ix, iy = rx + (vx - ox) * tof, ry + (vy - oy) * tof
            r_int = (ix * ix + iy * iy) ** 0.5
            n = salvo_for(ef.pk)
            butee = (ev["tcpa"] - tof - REACTION_S) if ev["tcpa"] > 0 else 1e9

            if r_int > ef.rmax or r_int < ef.rmin:
                status = "hors enveloppe"
            elif ef.rounds < n:
                status = "munitions insuffisantes"
            elif ef.free_channels < 1:
                status = "canal occupé"
            elif butee < 0:
                status = "butée dépassée"
            else:
                status = "recommandé"

            out.append({
                "piste": tr.num, "effecteur": ef.key, "label": ef.label,
                "role": ef.role, "salve": n,
                "pk": round(1 - (1 - ef.pk) ** n, 3),
                "tof": round(tof, 1),
                "r_int": round(r_int / NM, 2),
                "rng": round(rngm / NM, 2),
                "butee": round(butee, 1) if butee < 1e8 else None,
                "statut": status,
                "cout": ef.cost,
                "auto": doctrine.get("auto_" + ef.key, False),
            })
    # Sous pression de temps, la butée décide. Hors pression, c'est l'effet
    # gradué : le moyen le plus économique qui fait le travail passe devant.
    def key(s):
        urgent = s["butee"] is not None and s["butee"] < 60
        return (s["statut"] != "recommandé", 0 if urgent else 1,
                s["butee"] if urgent else s["cout"])
    out.sort(key=key)
    return out
