"""Contacts posés en latitude/longitude plutôt qu'en gisement/distance.

    python3 -m unittest discover -s tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.engine import Engine                 # noqa: E402
from sim.geo import NM, Projection, bearing, rng   # noqa: E402
from sim.scenario import load                 # noqa: E402

BASE = """
name = "t"
seed = 1
duration = 60
%s
[ownship]
%s
course = 90
speed_kt = 10

[[contact]]
id = "A"
kind = "surf"
%s
rcs = 1000
"""


def _charger(origine, own, contact):
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
        f.write(BASE % (origine, own, contact))
    try:
        return load(f.name)
    finally:
        Path(f.name).unlink()


class TestLatLon(unittest.TestCase):
    def test_contact_en_latlon_avec_origine(self):
        sc = _charger("[origine]\nlat = 36.0\nlon = -6.0", "", "lat = 36.5\nlon = -6.0")
        c = sc["contacts"][0]
        # 30' de latitude plein nord = 30 NM (à quelques dizaines de mètres près)
        self.assertAlmostEqual(c.x, 0.0, delta=1.0)
        self.assertAlmostEqual(c.y / NM, 30.0, delta=0.2)

    def test_la_position_du_porteur_tient_lieu_d_origine(self):
        sc = _charger("", "lat = 36.0\nlon = -6.0", "lat = 36.0\nlon = -5.0")
        self.assertEqual(sc["origine"], {"lat": 36.0, "lon": -6.0})
        c = sc["contacts"][0]
        self.assertAlmostEqual(bearing(c.x, c.y), 90.0, delta=0.1)
        # 1° de longitude à 36° N ≈ 48,6 NM
        self.assertAlmostEqual(rng(c.x, c.y) / NM, 48.6, delta=0.3)

    def test_meme_point_par_les_deux_formes(self):
        sc = _charger("[origine]\nlat = 59.9\nlon = 25.25", "", "brg = 45\nrng_nm = 10")
        c1 = sc["contacts"][0]
        la, lo = Projection(59.9, 25.25).to_latlon(c1.x, c1.y)
        sc2 = _charger("[origine]\nlat = 59.9\nlon = 25.25", "", "lat = %r\nlon = %r" % (la, lo))
        c2 = sc2["contacts"][0]
        self.assertAlmostEqual(c1.x, c2.x, delta=0.5)
        self.assertAlmostEqual(c1.y, c2.y, delta=0.5)

    def test_latlon_sans_reference_est_refuse(self):
        with self.assertRaises(ValueError):
            _charger("", "", "lat = 36.0\nlon = -6.0")

    def test_scenario_monde_se_charge_et_tourne(self):
        e = Engine(load(ROOT / "scenarios" / "08-monde-gibraltar.toml"))
        self.assertAlmostEqual(e.proj.lat0, 35.95)
        for _ in range(200):
            e.step()
        f = e.snapshot()
        self.assertAlmostEqual(f["own"]["lat"], 35.95, delta=0.05)
        self.assertIn("radar", f)
        self.assertEqual(f["radar"]["max_nm"], 110.0)
        self.assertGreater(f["radar"]["horizon_nm"], 10.0)


if __name__ == "__main__":
    unittest.main()
