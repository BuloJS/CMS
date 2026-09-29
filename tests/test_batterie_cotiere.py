"""Scénario 10 : une batterie côtière (installation à terre) qui verrouille une
zone et tire par salves.

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.engine import Engine                        # noqa: E402
from sim.scenario import load                        # noqa: E402
from sim.terre import Terre                          # noqa: E402
from tools.eaux import carte_pour, verifier_placement    # noqa: E402

SC = ROOT / "scenarios" / "10-batterie-cotiere.toml"


class TestBatterie(unittest.TestCase):
    def _engine(self):
        e = Engine(load(SC))
        e.terre = Terre.depuis_carte(carte_pour(e.proj.lat0, e.proj.lon0), e.proj)
        return e

    def test_la_batterie_est_a_terre_et_immobile(self):
        e = self._engine()
        bat = e.world["BAT-1"]
        self.assertEqual(bat.kind, "land")
        self.assertEqual(bat.speed, 0.0)
        self.assertTrue(e.terre.a_terre(bat.x, bat.y))

    def test_une_batterie_ne_peut_pas_etre_deplacee_en_mer(self):
        pb = verifier_placement(SC, 25.5, -90.0)          # golfe du Mexique
        self.assertIn("batterie_en_mer", [p["code"] for p in pb])

    def test_l_immobile_a_terre_ne_s_arrete_pas_a_la_cote_ni_ne_bouge(self):
        e = self._engine()
        x, y = e.world["BAT-1"].x, e.world["BAT-1"].y
        for _ in range(400):
            e.step()
        self.assertEqual((e.world["BAT-1"].x, e.world["BAT-1"].y), (x, y))
        self.assertTrue(e.world["BAT-1"].alive)

    def test_salve_de_quatre_puis_seconde_salve(self):
        e = self._engine()
        e.fin_sur_impact = False
        e.own.speed = e.own.ordered_speed = 0.0
        bat = e.world["BAT-1"]
        e.own.x, e.own.y = bat.x - 20 * 1852.0, bat.y + 5 * 1852.0     # dans la zone (30 NM)
        while e.t < 200:
            e.step()
        salve1 = [u for u in e.world if u.startswith("BAT-1-M")]
        self.assertEqual(len(salve1), 4)                                # 4 missiles
        while e.t < 400:
            e.step()
        salve2 = [u for u in e.world if u.startswith("BAT-1-M")]
        self.assertEqual(len(salve2), 8)                                # tirs_max = 2
        for _ in range(4000):                                           # 200 s de plus : pas de 3e salve
            e.step()
        self.assertEqual(len([u for u in e.world if u.startswith("BAT-1-M")]), 8)

    def test_la_batterie_est_vue_de_la_zone(self):
        """Antenne en hauteur : son horizon radio couvre sa zone de 30 NM, donc
        le porteur la pistera (ou l'entendra) quand elle le verrouille."""
        from sim.sensors import radar_horizon
        e = self._engine()
        self.assertGreater(radar_horizon(e.own.mast_height, e.world["BAT-1"].height), 30 * 1852.0)


if __name__ == "__main__":
    unittest.main()
