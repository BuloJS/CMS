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
