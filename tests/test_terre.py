"""Échouement, routes à terre, et zones prédéfinies.

    python3 -m unittest discover -s tests
"""
import json
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.engine import Engine                          # noqa: E402
from sim.geo import KT, NM                             # noqa: E402
from sim.scenario import load                          # noqa: E402
from sim.terre import Terre, problemes_de_route        # noqa: E402
from tools.eaux import verifier_placement              # noqa: E402

# Un carré de terre de 10 km de côté, à 2 km au nord de l'origine.
CARRE = [(-5000, 2000), (5000, 2000), (5000, 12000), (-5000, 12000)]


class TestTerre(unittest.TestCase):
    def test_point_dans_et_hors_du_carre(self):
        t = Terre([CARRE])
        self.assertTrue(t.a_terre(0, 5000))
        self.assertFalse(t.a_terre(0, 1000))
        self.assertFalse(t.a_terre(9000, 5000))

    def test_anneau_interieur_creuse_la_terre(self):
        lac = [(-1000, 5000), (1000, 5000), (1000, 7000), (-1000, 7000)]
        t = Terre([CARRE, lac])
        self.assertFalse(t.a_terre(0, 6000))
        self.assertTrue(t.a_terre(3000, 6000))

    def test_eau_libre_refuse_une_position_collee_a_la_cote(self):
        t = Terre([CARRE])
        self.assertTrue(t.eau_libre(0, -5000, 1000))
        self.assertFalse(t.eau_libre(0, 1500, 1000))     # la côte est à 500 m

    def test_route_qui_s_echoue(self):
        class C:
            kind, uid, x, y = "surf", "X", 0.0, 0.0
            vxy = (0.0, 10.0)                              # plein nord, 10 m/s
        pb = problemes_de_route(Terre([CARRE]), [C()], 600)
        self.assertEqual([p["code"] for p in pb], ["contact_echoue"])
        self.assertTrue(180 <= pb[0]["t"] <= 240)          # 2 km à 10 m/s

    def test_contact_pose_a_terre(self):
        class C:
            kind, uid, x, y = "surf", "X", 0.0, 5000.0
            vxy = (0.0, 0.0)
        pb = problemes_de_route(Terre([CARRE]), [C()], 600)
        self.assertEqual(pb[0]["code"], "contact_terre")


class TestEchouement(unittest.TestCase):
    def _engine(self):
        e = Engine(load(ROOT / "scenarios" / "06-plc-armement.toml"))
        e.terre = Terre([CARRE])
        return e

    def test_le_porteur_qui_touche_la_cote_arrete_tout(self):
        e = self._engine()
        e.own.course = e.own.ordered_course = 0.0
        e.own.speed = e.own.ordered_speed = 10 * KT
        for _ in range(9000):                  # 2 km à ~5 m/s : ~400 s = 8000 pas
            e.step()
        self.assertIsNotNone(e.crash)
        self.assertEqual(e.crash["type"], "terre")
        self.assertEqual(e.own.speed, 0.0)
        t = e.t
        for _ in range(100):
            e.step()
        self.assertEqual(e.t, t)                           # la simulation est figée
        self.assertEqual(e.snapshot()["crash"]["type"], "terre")

    def test_sans_terre_rien_ne_change(self):
        e = Engine(load(ROOT / "scenarios" / "06-plc-armement.toml"))
        e.own.speed = e.own.ordered_speed = 10 * KT
        for _ in range(2000):
            e.step()
        self.assertIsNone(e.crash)

    def test_contact_de_surface_s_arrete_a_la_cote(self):
        e = self._engine()
        e.own.speed = e.own.ordered_speed = 0.0
        from sim.entities import Contact
        c = Contact(uid="C1", name="C1", kind="surf", x=0.0, y=1000.0, alt=0.0,
                    course=0.0, speed=10.0, rcs=100.0)
        e.world["C1"] = c
        for _ in range(2500):                  # 1 km à 10 m/s : 100 s = 2000 pas
            e.step()
        self.assertEqual(c.speed, 0.0)
        self.assertLess(c.y, 2100.0)
        self.assertIsNone(e.crash)


class TestZones(unittest.TestCase):
    def test_chaque_zone_accueille_chaque_scenario_relatif(self):
        """Les zones prédéfinies existent pour qu'un placement ne tombe pas
        sur la terre : elles doivent donc accepter tous les scénarios dont
        les contacts sont relatifs au porteur. Un scénario posé en lat/lon
        (08) est ancré à sa région, il n'est pas concerné."""
        zones = json.loads((ROOT / "web" / "zones.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(zones), 20)
        relatifs = []
        for p in sorted((ROOT / "scenarios").glob("*.toml")):
            d = tomllib.loads(p.read_text(encoding="utf-8"))
            if any("lat" in c for c in d.get("contact", [])):
                continue
            relatifs.append(p)
        self.assertGreaterEqual(len(relatifs), 6)
        for z in zones:
            for p in relatifs:
                pb = verifier_placement(p, z["lat"], z["lon"])
                self.assertEqual(pb, [], "%s dans %s : %s" % (p.stem, z["id"], pb))

    def test_la_terre_est_refusee(self):
        p = ROOT / "scenarios" / "07-flotte-mixte.toml"
        pb = verifier_placement(p, 10.0, 20.0)           # Tchad
        self.assertEqual(pb[0]["code"], "porteur_terre")

    def test_la_cote_collee_est_refusee(self):
        p = ROOT / "scenarios" / "07-flotte-mixte.toml"
        pb = verifier_placement(p, 60.10, 24.94)         # rade d'Helsinki
        self.assertTrue(pb)


if __name__ == "__main__":
    unittest.main()
