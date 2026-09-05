"""Géométrie et cinématique.

Repère local tangent : x vers l'est, y vers le nord, z altitude, tout en
mètres et en m/s. Les milles nautiques et les nœuds n'existent qu'à
l'affichage — mélanger les deux dans l'équation radar est l'erreur qui
coûte une soirée.
"""
from math import atan2, cos, degrees, hypot, radians, sin, sqrt

NM = 1852.0          # mètres
KT = 1852.0 / 3600   # m/s
FT = 0.3048


def to_xy(brg_deg, rng_m):
    """Gisement (0 = nord, sens horaire) + distance -> (x est, y nord)."""
    a = radians(brg_deg)
    return rng_m * sin(a), rng_m * cos(a)


def bearing(x, y):
    return (degrees(atan2(x, y)) + 360.0) % 360.0


def rng(x, y):
    return hypot(x, y)


def vel(course_deg, speed):
    a = radians(course_deg)
    return speed * sin(a), speed * cos(a)


def cpa(rx, ry, vx, vy):
    """CPA/TCPA d'un contact en position et vitesse relatives au porteur.

    Retourne (distance CPA en m, TCPA en s). TCPA < 0 : le contact s'ouvre.
    """
    v2 = vx * vx + vy * vy
    if v2 < 1e-9:
        return hypot(rx, ry), -1.0
    t = -(rx * vx + ry * vy) / v2
    if t <= 0:
        return hypot(rx, ry), -1.0
    return hypot(rx + vx * t, ry + vy * t), t


def intercept_time(rx, ry, vx, vy, v_int):
    """Temps de vol d'un intercepteur de vitesse v_int lancé du porteur.

    Résout |r + v t| = v_int t. Retourne None si la cible ne peut être
    rattrapée (elle fuit plus vite que l'intercepteur).
    """
    a = vx * vx + vy * vy - v_int * v_int
    b = 2 * (rx * vx + ry * vy)
    c = rx * rx + ry * ry
    if abs(a) < 1e-6:
        return None if abs(b) < 1e-9 else (-c / b if -c / b > 0 else None)
    disc = b * b - 4 * a * c
    if disc < 0:
        return None
    s = sqrt(disc)
    roots = [t for t in ((-b - s) / (2 * a), (-b + s) / (2 * a)) if t > 0]
    return min(roots) if roots else None


def turn_toward(cur_deg, tgt_deg, rate_dps, dt):
    """Fait tourner un cap vers un cap consigne, taux limité."""
    d = (tgt_deg - cur_deg + 540.0) % 360.0 - 180.0
    step = max(-rate_dps * dt, min(rate_dps * dt, d))
    return (cur_deg + step) % 360.0


def approach(cur, tgt, tau, dt):
    """Premier ordre : inertie de vitesse, de température, de tout."""
    return cur + (tgt - cur) * min(1.0, dt / max(tau, 1e-6))


# --------------------------------------------------------------------- #
# Projection géographique
#
# Le cœur travaille en mètres dans un plan tangent local. Les données
# réelles (AIS, cartographie) arrivent en latitude/longitude. La conversion
# est ici, et nulle part ailleurs : aucun degré géographique ne doit
# circuler dans le pistage ou les senseurs.
# --------------------------------------------------------------------- #

WGS84_A = 6378137.0                      # demi-grand axe, m
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


class Projection:
    """Plan tangent local autour d'un point de référence.

    Approximation équirectangulaire, mais avec les vrais rayons de courbure
    de l'ellipsoïde au point de référence plutôt qu'un rayon sphérique
    moyen : sur une centaine de milles l'écart entre les deux atteint
    plusieurs centaines de mètres, soit largement de quoi décaler une piste
    d'une ellipse d'incertitude entière.

    Au-delà de ~150 NM du point de référence la distorsion en longitude
    devient sensible ; ce n'est pas la bonne projection pour une carte du
    monde, c'en est une très bonne pour un scope.
    """

    def __init__(self, lat0, lon0):
        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        s = sin(radians(self.lat0))
        w2 = 1.0 - WGS84_E2 * s * s
        # rayon de courbure méridien (nord-sud) et grande normale (est-ouest)
        self.m_per_lat = radians(1.0) * WGS84_A * (1.0 - WGS84_E2) / (w2 ** 1.5)
        self.m_per_lon = radians(1.0) * WGS84_A * cos(radians(self.lat0)) / sqrt(w2)

    def to_xy(self, lat, lon):
        """lat/lon en degrés -> (x est, y nord) en mètres."""
        dlon = (float(lon) - self.lon0 + 540.0) % 360.0 - 180.0    # passage 180°
        return dlon * self.m_per_lon, (float(lat) - self.lat0) * self.m_per_lat

    def to_latlon(self, x, y):
        """(x est, y nord) en mètres -> lat/lon en degrés."""
        lat = self.lat0 + y / self.m_per_lat
        lon = self.lon0 + x / self.m_per_lon
        return lat, (lon + 540.0) % 360.0 - 180.0


def fmt_latlon(lat, lon):
    """Degrés et minutes décimales — la façon dont une position se lit et se
    dicte à la passerelle. Le format décimal est pour les machines."""
    def one(v, pos, neg):
        h = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        return "%d°%05.2f'%s" % (d, (v - d) * 60.0, h)
    return one(lat, "N", "S") + " " + one(lon, "E", "W")
