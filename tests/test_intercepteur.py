"""Position réelle d'un intercepteur en vol.

Le bug que ce fichier garde : `Interceptor` ne portait qu'un compte à
rebours (`eta`), pas de position qui avance — la console ne pouvait donc
dessiner qu'un trait figé du porteur vers la cible pendant tout le vol,
pas un objet qui se déplace et se suit à la trace.

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.engine import DT, Engine               # noqa: E402
from sim.scenario import load                   # noqa: E402
from sim.tracker import Track                   # noqa: E402

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "06-plc-armement.toml"


class TestPositionIntercepteur(unittest.TestCase):
    def setUp(self):
        self.e = Engine(load(SCENARIO))
        # Cible stationnaire à portée du CIWS : simplifie le calcul du point
        # d'interception attendu (immobile, la solution ne bouge pas).
        self.tr = Track(1000.0, 0.0, self.e.t)
        self.e.tracker.tracks[self.tr.num] = self.tr

    def test_part_du_porteur_et_avance_vers_la_solution(self):
        self.assertTrue(self.e.engage(self.tr.num, "ciws"))
        s = self.e.shots[0]
        self.assertEqual((s.x, s.y), (self.e.own.x, self.e.own.y),
                         "au tir, la position doit être celle du porteur")
        # Cible immobile : le point d'interception est sa propre position.
        self.assertAlmostEqual(s.hx, self.tr.s[0], places=3)
        self.assertAlmostEqual(s.hy, self.tr.s[2], places=3)

        avant = (s.x, s.y)
        for _ in range(20):        # 1 s à DT=0.05
            self.e.step()
        apres = (s.x, s.y)
        self.assertNotEqual(avant, apres,
                            "la position doit avoir bougé après 1 s de vol")
        # Toujours strictement entre départ et solution, jamais au-delà.
        self.assertLess(abs(s.x - s.x0), abs(s.hx - s.x0) + 1e-6)

    def test_atteint_la_solution_a_l_echeance(self):
        self.assertTrue(self.e.engage(self.tr.num, "ciws"))
        s = self.e.shots[0]
        tof = s.tof
        # Juste avant l'échéance : proche mais pas encore arrivé.
        for _ in range(int(tof / DT) - 2):
            self.e.step()
        self.assertGreater(abs(s.x - s.hx) + abs(s.y - s.hy), 1e-6)
        # Juste après : à la solution, pas au-delà (la fraction est
        # plafonnée à 1 même si eta continue de descendre sous zéro).
        for _ in range(5):
            self.e.step()
        self.assertAlmostEqual(s.x, s.hx, places=3)
        self.assertAlmostEqual(s.y, s.hy, places=3)

    def test_historique_alimente_pour_la_trace(self):
        self.assertTrue(self.e.engage(self.tr.num, "ciws"))
        s = self.e.shots[0]
        self.assertEqual(len(s.history), 1, "un seul point tant que rien n'a bougé")
        for _ in range(10):
            self.e.step()
        self.assertGreater(len(s.history), 1)
        self.assertLessEqual(len(s.history), 20, "l'historique doit rester borné")


if __name__ == "__main__":
    unittest.main()
