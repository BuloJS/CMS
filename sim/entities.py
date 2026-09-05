"""Entités de la vérité terrain.

Le simulateur connaît la vérité ; les senseurs n'en voient qu'une version
bruitée et incomplète. Rien de ce qui est ici ne doit remonter tel quel à
l'IHM — la console ne voit que des pistes construites par le pistage.
"""
from dataclasses import dataclass, field

from .geo import KT, approach, turn_toward, vel


@dataclass
class Contact:
    """Une plateforme réelle : navire, aéronef, missile."""
    uid: str
    name: str
    kind: str                 # air | surf | missile
    x: float
    y: float
    alt: float = 0.0          # m
    course: float = 0.0       # deg
    speed: float = 0.0        # m/s
    rcs: float = 100.0        # m²
    iff: bool = False         # répond à l'interrogation ami
    ais: bool = False         # émet une identité AIS
    emitters: list = field(default_factory=list)   # ["nav", "search", "fc"]
    intent: str = "neutral"   # neutral | hostile | friend — vérité, jamais affichée
    alive: bool = True
    # missiles seulement
    target: str = ""
    launched_at: float = 0.0
    seduced: bool = False

    def step(self, dt, world):
        if not self.alive:
            return
        if self.kind == "missile" and self.target and not self.seduced:
            tgt = world.get(self.target)
            if tgt:
                from .geo import bearing
                self.course = bearing(tgt.x - self.x, tgt.y - self.y)
        vx, vy = vel(self.course, self.speed)
        self.x += vx * dt
        self.y += vy * dt

    @property
    def height(self):
        """Hauteur vue par l'horizon radio. Un navire n'est pas un point au
        niveau de la mer : c'est sa superstructure qu'on voit dépasser, une
        quinzaine de mètres. Lui donner une hauteur nulle le rend invisible."""
        return max(self.alt, 15.0) if self.kind == "surf" else self.alt

    @property
    def vxy(self):
        return vel(self.course, self.speed)


@dataclass
class Ownship:
    """Le porteur. Cinématique volontairement inertielle : un bâtiment de
    4 000 tonnes ne change pas de cap comme un curseur de souris."""
    x: float = 0.0
    y: float = 0.0
    course: float = 0.0
    speed: float = 7.0
    ordered_course: float = 0.0
    ordered_speed: float = 7.0
    turn_rate: float = 1.8        # deg/s à vitesse de manœuvre
    accel_tau: float = 45.0       # s, constante de temps de la propulsion
    mast_height: float = 30.0     # m, hauteur de l'antenne de veille

    def step(self, dt):
        self.course = turn_toward(self.course, self.ordered_course, self.turn_rate, dt)
        self.speed = approach(self.speed, self.ordered_speed, self.accel_tau, dt)
        vx, vy = vel(self.course, self.speed)
        self.x += vx * dt
        self.y += vy * dt

    @property
    def vxy(self):
        return vel(self.course, self.speed)
