"""Modèles de senseurs.

Deux lois portent tout le réalisme du domaine naval :

  * l'équation radar en R^4, qui rend la détection d'un missile à faible
    surface équivalente radar bien plus tardive que celle d'une frégate ;
  * l'horizon radio, qui fait *surgir* un missile rasant au lieu de le
    laisser approcher tranquillement.

Le reste (fouillis, multitrajet, conduits) est du décor et peut attendre.
"""
import math

from .geo import KT, NM, bearing, rng


def radar_horizon(h_ant, h_tgt):
    """Portée optique-radio en mètres. d(km) = 4.12 (racine(h1) + racine(h2)), h en m.

    Antenne à 30 m, missile rasant à 5 m : 31,8 km, soit 17 NM. À Mach 0,9
    cela laisse moins de deux minutes. C'est tout le sujet.
    """
    return 4120.0 * (math.sqrt(max(h_ant, 0.1)) + math.sqrt(max(h_tgt, 0.1)))


class Radar:
    """Veille air/surface à antenne tournante.

    `k_db` est calibré pour Pd = 0,5 sur 0,1 m² à 20 NM ; une frégate de
    10 000 m² sort alors très au-delà de l'horizon, donc limitée par lui —
    ce qui est exactement le comportement réel.
    """

    def __init__(self, name="RAD-1", rpm=15.0, k_db=205.75, sigma_r=25.0,
                 sigma_b=0.3, max_range=110 * NM):
        self.name = name
        self.rpm = rpm
        self.k_db = k_db
        self.sigma_r = sigma_r          # m
        self.sigma_b = sigma_b          # deg
        self.max_range = max_range
        self.sweep = 0.0                # deg
        self.power = 1.0                # facteur 0..1 imposé par l'IPMS

    def step(self, dt):
        prev = self.sweep
        self.sweep = (self.sweep + 6.0 * self.rpm * dt) % 360.0
        return prev

    def crossed(self, prev, brg):
        """Le faisceau vient-il de passer sur ce gisement pendant le tick ?"""
        if prev <= self.sweep:
            return prev <= brg < self.sweep
        return brg >= prev or brg < self.sweep

    def snr_db(self, rcs, r_m):
        if r_m < 1.0:
            return 99.0
        # Déclassement de puissance : -6 dB de SNR ramène la portée à 71 %.
        # Le plancher n'est là que pour éviter log10(0) — pas pour limiter
        # l'effet réel du déclassement, qui doit pouvoir aller jusqu'à une
        # quasi-coupure (voir sim/platform.py P_TRIP).
        return (self.k_db + 10 * math.log10(max(rcs, 1e-3))
                - 40 * math.log10(r_m) + 10 * math.log10(max(self.power, 1e-6)))

    def detect(self, own, c, rand):
        """Retourne un plot (r, brg) bruité, ou None."""
        dx, dy = c.x - own.x, c.y - own.y
        r = rng(dx, dy)
        if r > self.max_range:
            return None
        if r > radar_horizon(own.mast_height, c.height):
            return None
        snr = self.snr_db(c.rcs, r)
        pd = 1.0 / (1.0 + math.exp(-(snr - 13.0) / 2.2))
        if rand.random() > pd:
            return None
        return (r + rand.gauss(0, self.sigma_r),
                (bearing(dx, dy) + rand.gauss(0, self.sigma_b)) % 360.0,
                snr)


class Esm:
    """Détection électromagnétique passive.

    Trajet en R^2 et non R^4 : on « entend » un radar bien avant de voir la
    plateforme qui le porte. Et le passage d'un émetteur en conduite de tir
    est une intention, pas une hypothèse.
    """
    RANGES = {"nav": 40 * NM, "search": 120 * NM, "fc": 90 * NM}

    def __init__(self, sigma_b=1.5):
        self.sigma_b = sigma_b

    def detect(self, own, c, rand):
        if not c.emitters:
            return None
        dx, dy = c.x - own.x, c.y - own.y
        r = rng(dx, dy)
        best = None
        for e in c.emitters:
            reach = self.RANGES.get(e, 30 * NM)
            if r <= reach and r <= radar_horizon(own.mast_height + 10, max(c.height, 3)):
                if best is None or self.RANGES.get(e, 0) > self.RANGES.get(best, 0):
                    best = e
        if best is None:
            return None
        return ((bearing(dx, dy) + rand.gauss(0, self.sigma_b)) % 360.0, best)


class Iff:
    """Interrogateur ami/ennemi. Une absence de réponse n'est pas une
    hostilité : c'est une absence de réponse. Le TEWA en tient compte comme
    d'un facteur parmi d'autres, jamais comme d'une preuve."""

    def interrogate(self, own, c):
        if rng(c.x - own.x, c.y - own.y) > 80 * NM:
            return None
        return "ami" if c.iff else "pas de réponse"


class Ais:
    """Identité coopérative. Portée VHF, donc horizon de surface.

    Un navire coopératif ne déclare pas qu'un nom : indicatif, numéro OMI,
    type, dimensions, tirant d'eau, statut de navigation, destination.
    Cette richesse est le vrai intérêt de l'AIS pour un CMS — non pas
    parce qu'elle renseigne, mais parce qu'elle se compare. Un plot radar
    sans AIS, ou un AIS dont la cinématique ne colle pas au plot, est
    exactement le contact qui mérite un opérateur.
    """

    def receive(self, own, c):
        if not c.ais:
            return None
        if rng(c.x - own.x, c.y - own.y) > radar_horizon(own.mast_height, 20):
            return None
        rec = {"name": c.name}
        rec.update(c.ais_static)
        # La position et la cinématique **déclarées**. Le système ne
        # reçoit que celles-là ; la vérité terrain ne lui est jamais
        # accessible, et la corrélation doit donc se faire sur elles.
        dx, dy = getattr(c, "ais_ecart", (0.0, 0.0))
        rec["x"], rec["y"] = c.x + dx, c.y + dy
        rec["sog"] = c.speed / KT
        rec["cog"] = c.course
        rec.update(c.ais_declare or {})
        return rec
