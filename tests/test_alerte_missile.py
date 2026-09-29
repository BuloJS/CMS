"""Scénario 09 : l'ennemi décide seul — illumination, délai, salve, impact.

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.engine import Engine        # noqa: E402
from sim.scenario import load        # noqa: E402

SC = ROOT / "scenarios" / "09-alerte-missile.toml"


def _codes(e):
    """(t, code) du plus ancien au plus récent."""
    return [(ev["t"], ev.get("code")) for ev in reversed(e.events) if ev.get("code")]


class TestAlerteMissile(unittest.TestCase):
    def test_le_scenario_porte_ses_reglages(self):
        e = Engine(load(SC))
        self.assertEqual(e.decoys, 20)                       # stock propre au scénario
        self.assertFalse(e.doctrine["auto_ciws"])            # doctrine propre au scénario
        attaquants = [c for c in e.world.values() if c.attaque]
        self.assertEqual(len(attaquants), 2)
        self.assertTrue(all(c.speed == 0 for c in attaquants))     # bâtiments à poste

    def test_l_ennemi_illumine_puis_tire_puis_touche(self):
        e = Engine(load(SC))
        e.doctrine["auto_id"] = True
        while e.t < 900 and not e.crash:
            e.step()
        self.assertIsNotNone(e.crash, "sans réaction, le porteur doit être touché")
        self.assertEqual(e.crash["type"], "missile")
        codes = [c for _, c in _codes(e)]
        i_lock, i_dep, i_imp = (codes.index(c) for c in ("verrouillage", "depart_missile", "impact"))
        self.assertLess(i_lock, i_dep)
        self.assertLess(i_dep, i_imp)
        # Le départ suit le verrouillage du délai demandé (25 s pour HST-1).
        t_lock = next(t for t, c in _codes(e) if c == "verrouillage")
        t_dep = next(t for t, c in _codes(e) if c == "depart_missile")
        self.assertAlmostEqual(t_dep - t_lock, 25.0, delta=6.0)

    def test_la_simulation_est_figee_apres_l_impact(self):
        e = Engine(load(SC))
        while e.t < 900 and not e.crash:
            e.step()
        t = e.t
        for _ in range(200):
            e.step()
        self.assertEqual(e.t, t)
        self.assertEqual(e.snapshot()["crash"]["type"], "missile")

    def test_le_montecarlo_peut_compter_toute_la_salve(self):
        """fin_sur_impact = False : les impacts ne figent plus la simulation."""
        e = Engine(load(SC))
        e.fin_sur_impact = False
        while e.t < 900:
            e.step()
        self.assertIsNone(e.crash)
        # Le journal ne garde que les 60 derniers événements : on compte les
        # missiles arrivés au but, pas leurs lignes de journal.
        missiles = [c for c in e.world.values() if c.kind == "missile"]
        self.assertGreaterEqual(len(missiles), 3)
        self.assertTrue(any(not c.alive for c in missiles))

    def test_un_tireur_a_poste_tire_toutes_les_cadences(self):
        e = Engine(load(SC))
        e.fin_sur_impact = False
        e.own.ordered_speed = 0.0
        e.own.speed = 0.0
        # Le porteur reste en zone : placé à 20 NM de HST-1, immobile.
        h1 = e.world["HST-1"]
        e.own.x, e.own.y = h1.x - 20 * 1852.0, h1.y
        while e.t < 600:
            e.step()
        uids = sorted(u for u in e.world if u.startswith("HST-1-M"))
        self.assertGreaterEqual(len(uids), 4, uids)                # ≥ 4 tirs en 600 s
        lancers = sorted(e.world[u].launched_at for u in uids)
        for a, b in zip(lancers, lancers[1:]):
            self.assertAlmostEqual(b - a, 120.0, delta=3.0)

    def test_sortir_de_la_zone_fait_perdre_le_verrou(self):
        e = Engine(load(SC))
        h1 = e.world["HST-1"]
        e.own.ordered_speed = 0.0
        e.own.speed = 0.0
        e.own.x, e.own.y = h1.x - 20 * 1852.0, h1.y               # dans la zone (24 NM)
        for _ in range(100):
            e.step()
        self.assertEqual(h1.emitters, ["fc"])
        e.own.x, e.own.y = h1.x - 40 * 1852.0, h1.y               # hors zone
        for _ in range(100):
            e.step()
        self.assertEqual(h1.emitters, ["search"])                  # verrou rendu

    def test_un_contact_sans_attaque_ne_tire_jamais(self):
        e = Engine(load(ROOT / "scenarios" / "07-flotte-mixte.toml"))
        # 07 tire par événement scripté (720 s) : avant, aucun départ.
        while e.t < 600:
            e.step()
        self.assertNotIn("depart_missile", [c for _, c in _codes(e)])
        self.assertNotIn("verrouillage", [c for _, c in _codes(e)])


if __name__ == "__main__":
    unittest.main()
