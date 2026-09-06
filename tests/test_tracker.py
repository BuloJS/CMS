"""Convergence du filtre de pistage.

Le contrôle qui manquait le plus : la vitesse estimée. La position, elle,
se voit tout de suite à l'écran quand elle est fausse ; une vitesse fausse
de moitié sur une position juste ne se remarque pas — sauf par le TEWA, qui
en tire un temps avant CPA et une butée de tir également faux.

Ces tests couvrent les deux régimes que le simulateur doit tenir en même
temps : un missile à 300 m/s et un caboteur à 13 nœuds. Une valeur unique
de bruit de manœuvre n'y arrive pas, d'où `Track._adapte_q` — dont ils sont
le garde-fou.

    python3 -m unittest discover -s tests
"""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.entities import Ownship                              # noqa: E402
from sim.geo import KT, NM, bearing, rng, vel                 # noqa: E402
from sim.tracker import Q_MAX, Q_MIN, Track, Tracker          # noqa: E402

SIGMA_R = 25.0        # m, comme le radar de veille
SIGMA_B = 0.3         # deg
TOUR = 4.0            # s, un tour d'antenne à 15 tr/min


def piste_sur_trajectoire(x0, y0, course, vitesse, tours=60, graine=12345):
    """Fait tourner le pistage sur une cible en ligne droite et rend la
    piste obtenue, avec la vérité au même instant."""
    rnd = random.Random(graine)
    own = Ownship(x=0.0, y=0.0, course=0.0, speed=0.0,
                  ordered_course=0.0, ordered_speed=0.0)
    trk = Tracker()
    vx, vy = vel(course, vitesse)
    x, y, t = x0, y0, 0.0
    for _ in range(tours):
        t += TOUR
        x, y = x + vx * TOUR, y + vy * TOUR
        r = rng(x - own.x, y - own.y)
        b = bearing(x - own.x, y - own.y)
        plot = (r + rnd.gauss(0, SIGMA_R),
                (b + rnd.gauss(0, SIGMA_B)) % 360.0, SIGMA_R, SIGMA_B)
        trk.step(TOUR, own, [plot], t, scan_done=True)
    pistes = trk.confirmed()
    return (pistes[0] if pistes else None), (x, y, vitesse, course)


class TestConvergence(unittest.TestCase):

    def test_missile_rasant(self):
        """300 m/s en rapprochement. C'est le régime pour lequel le filtre a
        été réglé à l'origine, et il ne doit pas bouger."""
        tr, (x, y, v, crs) = piste_sur_trajectoire(0.0, 25 * NM, 180.0, 300.0)
        self.assertIsNotNone(tr, "aucune piste confirmée")
        self.assertAlmostEqual(tr.speed, v, delta=0.05 * v)
        self.assertAlmostEqual((tr.course - crs + 180) % 360 - 180, 0.0, delta=3.0)

    def test_caboteur(self):
        """13 nœuds. Sans bruit de manœuvre adapté, le filtre rend ici une
        vitesse fausse de plus de moitié sur une position pourtant juste :
        c'est exactement la panne que ce test existe pour attraper."""
        v = 13.0 * KT
        tr, (x, y, vrai, crs) = piste_sur_trajectoire(4 * NM, 6 * NM, 250.0, v)
        self.assertIsNotNone(tr, "aucune piste confirmée")
        self.assertAlmostEqual(tr.speed, vrai, delta=0.15 * vrai)
        self.assertAlmostEqual((tr.course - crs + 180) % 360 - 180, 0.0, delta=8.0)

    def test_position_juste_dans_les_deux_cas(self):
        """La position doit rester bonne quel que soit le régime — c'est ce
        qui rend la vitesse fausse si difficile à repérer à l'œil."""
        for crs, v, x0, y0 in [(180.0, 300.0, 0.0, 25 * NM),
                               (250.0, 13.0 * KT, 4 * NM, 6 * NM)]:
            tr, (x, y, _, _) = piste_sur_trajectoire(x0, y0, crs, v)
            px, py = tr.pos
            self.assertLess(math.hypot(px - x, py - y), 250.0)


class TestBruitDeManoeuvre(unittest.TestCase):
    """`q` s'indexe sur la vitesse estimée, borné aux deux bouts."""

    def _q(self, vitesse_ms):
        tr = Track(0.0, 0.0, 0.0)
        tr.s = [0.0, vitesse_ms, 0.0, 0.0]
        tr._adapte_q()
        return tr.q

    def test_mobile_lent_au_plancher(self):
        self.assertAlmostEqual(self._q(13.0 * KT), Q_MIN, places=6)

    def test_missile_au_plafond(self):
        """La borne haute est la valeur historique : les scénarios
        antinavires doivent se comporter exactement comme avant."""
        self.assertAlmostEqual(self._q(300.0), Q_MAX, places=6)
        self.assertAlmostEqual(self._q(175.0), Q_MAX, places=6)

    def test_croissance_monotone(self):
        vs = [5.0, 20.0, 60.0, 100.0, 140.0]
        qs = [self._q(v) for v in vs]
        self.assertEqual(qs, sorted(qs))
        self.assertTrue(all(Q_MIN <= q <= Q_MAX for q in qs))


if __name__ == "__main__":
    unittest.main()
