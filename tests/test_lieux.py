"""web/lieux.json : villes, capitales, mers — cohérence des données.

    python3 -m unittest discover -s tests
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.lieux import _dans          # noqa: E402

DOC = json.loads((ROOT / "web" / "lieux.json").read_text(encoding="utf-8"))


class TestLieux(unittest.TestCase):
    def test_capitales_presentes(self):
        caps = {v[0]: v for v in DOC["villes"] if v[5]}
        self.assertGreaterEqual(len(caps), 190)
        for nom, lat, lon in (("Paris", 48.87, 2.33), ("Helsinki", 60.17, 24.94),
                              ("Tokyo", 35.69, 139.75)):
            self.assertIn(nom, caps)
            self.assertAlmostEqual(caps[nom][2], lat, delta=0.3)
            self.assertAlmostEqual(caps[nom][3], lon, delta=0.3)

    def test_noms_francais_quand_ils_different(self):
        rome = next(v for v in DOC["villes"] if v[0] == "Rome")
        self.assertEqual(rome[0], "Rome")
        mers = {m["n"]: m for m in DOC["mers"]}
        self.assertEqual(mers["Strait of Gibraltar"]["f"], "détroit de Gibraltar")

    def test_mers_triees_de_la_plus_petite_a_la_plus_grande(self):
        aires = [m["a"] for m in DOC["mers"]]
        self.assertEqual(aires, sorted(aires))

    def test_le_point_d_etiquette_est_dans_le_contour(self):
        """Sinon la mer serait écrite sur la terre. Vrai pour tout contour
        dont le point d'étiquette n'est pas par construction sur un bord."""
        hors = []
        for m in DOC["mers"]:
            lat, lon = m["p"]
            if not any(_dans(r, lon, lat) for r in m["poly"]):
                hors.append(m["n"])
        # L'arrondi du contour peut faire sortir un point d'un détroit étroit :
        # quelques cas tolérés, pas une dérive.
        self.assertLess(len(hors), 12, hors)

    def test_helsinki_dans_le_golfe_de_finlande(self):
        # Un point en mer au large d'Helsinki tombe dans « Gulf of Finland ».
        lat, lon = 59.9, 25.25
        trouves = [m["n"] for m in DOC["mers"]
                   if any(_dans(r, lon, lat) for r in m["poly"])]
        self.assertIn("Gulf of Finland", trouves)
        # Le plus petit qui le contient vient en premier.
        self.assertEqual(trouves[0], "Gulf of Finland")


PAYS = json.loads((ROOT / "web" / "pays.json").read_text(encoding="utf-8"))


class TestPays(unittest.TestCase):
    def test_pays_et_noms_francais(self):
        par_nom = {c[0]: c for c in PAYS["pays"]}
        self.assertGreaterEqual(len(par_nom), 200)
        self.assertEqual(par_nom["Germany"][1], "Allemagne")
        # le point d'étiquette de la France est en France (métropole)
        _, _, lat, lon, *_ = par_nom["France"]
        self.assertTrue(41 < lat < 51.5 and -5.5 < lon < 9.6, (lat, lon))

    def test_frontieres_presentes_et_bien_formees(self):
        self.assertGreater(len(PAYS["frontieres"]), 5000)
        for ln in PAYS["frontieres"][:200]:
            self.assertGreaterEqual(len(ln), 2)
            for lon, lat in ln:
                self.assertTrue(-180 <= lon <= 180 and -90 <= lat <= 90)

    def test_villes_plus_nombreuses_pour_le_zoom_progressif(self):
        self.assertGreater(len(DOC["villes"]), 2500)
        self.assertTrue(any(v[4] < 400000 and not v[5] for v in DOC["villes"]))


if __name__ == "__main__":
    unittest.main()
