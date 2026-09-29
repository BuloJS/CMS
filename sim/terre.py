"""Terre — le porteur, ou un contact, a-t-il les pieds au sec ?

Contours de terre convertis une fois dans le plan tangent du scénario
(mètres), avec la boîte englobante de chaque anneau : un test de point ne
parcourt que les anneaux dont la boîte contient le point, ce qui le rend
assez peu cher pour être rejoué chaque demi-seconde sur tous les navires.

Même règle pair-impair que tools/eaux.py — les anneaux intérieurs (lacs, mers
intérieures) s'annulent d'eux-mêmes. Les données sont du Natural Earth : le
50 m ne porte pas les îlots, donc « pas à terre » veut dire « pas sur un
polygone connu », pas « eau profonde ».
"""
from math import cos, hypot, sin, radians

from .geo import KT, NM


class Terre:
    def __init__(self, anneaux):
        """anneaux : liste d'anneaux [(x, y), ...] en mètres."""
        self.anneaux = []
        for ring in anneaux:
            if len(ring) < 3:
                continue
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            self.anneaux.append((min(xs), min(ys), max(xs), max(ys), ring))

    @classmethod
    def depuis_carte(cls, carte, proj):
        """`carte` au format web/coastline.json (clés `terres`), `proj` une
        sim.geo.Projection : ce que la console affiche est ce que le
        simulateur teste."""
        anneaux = []
        for poly in carte.get("terres", []):
            for ring in poly:
                anneaux.append([proj.to_xy(lat, lon) for lon, lat in ring])
        return cls(anneaux)

    def a_terre(self, x, y):
        n = 0
        for x0, y0, x1, y1, ring in self.anneaux:
            if x < x0 or x > x1 or y < y0 or y > y1:
                continue
            dedans = False
            j = len(ring) - 1
            for i in range(len(ring)):
                xi, yi = ring[i]
                xj, yj = ring[j]
                if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                    dedans = not dedans
                j = i
            if dedans:
                n += 1
        return n % 2 == 1

    def eau_libre(self, x, y, rayon_m, n=12):
        """Approximation : le point et n points sur un cercle de rayon
        `rayon_m` autour de lui sont tous en mer. Ne détecte pas une pointe
        plus étroite que l'espacement des points — assez pour refuser une
        position collée à la côte."""
        if self.a_terre(x, y):
            return False
        for k in range(n):
            a = radians(360.0 * k / n)
            if self.a_terre(x + rayon_m * sin(a), y + rayon_m * cos(a)):
                return False
        return True


def problemes_de_route(terre, contacts, duree_s, pas_s=30.0):
    """Contacts de surface qui partent à terre ou s'échouent avant la fin.

    Rend une liste de dicts {"code", "id", "t"} — des codes, pas des
    phrases : la console les met en mots dans la langue de l'opérateur.
    Route en ligne droite, comme le simulateur les fait avancer.
    """
    out = []
    for c in contacts:
        if c.kind != "surf":
            continue
        if terre.a_terre(c.x, c.y):
            out.append({"code": "contact_terre", "id": c.uid, "t": 0})
            continue
        vx, vy = c.vxy
        t = pas_s
        while t <= duree_s:
            if terre.a_terre(c.x + vx * t, c.y + vy * t):
                out.append({"code": "contact_echoue", "id": c.uid, "t": int(t)})
                break
            t += pas_s
    return out
