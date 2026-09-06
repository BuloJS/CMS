"""Les scénarios doivent être posés dans de vraies eaux.

Ce n'est pas de la coquetterie. L'origine d'un scénario décide de ce que le
trait de côte montre, de l'eau libre autour du porteur, et de la zone dans
laquelle l'ingestion AIS va chercher du trafic. Deux scénarios ont été
écrits à deux milles et demi d'un port, au milieu des skerries — ça se voit
tout de suite à l'écran, et personne ne l'avait relevé parce que l'œil ne
distingue pas « au large » de « dans les cailloux » sur un scope à
cinquante milles.

    python3 -m unittest discover -s tests
"""
import json
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.eaux import a_terre, distance_terre, juger    # noqa: E402
from sim.geo import NM                                   # noqa: E402

CARTE = ROOT / "web" / "coastline.json"
MARGE_NM = 8.0


def charger():
    return json.loads(CARTE.read_text(encoding="utf-8"))


class TestCarte(unittest.TestCase):

    def test_la_carte_porte_les_polygones_de_terre(self):
        """Sans eux, ni remplissage à l'écran ni contrôle possible."""
        doc = charger()
        self.assertTrue(doc.get("terres"), "web/coastline.json sans polygones")
        self.assertTrue(doc.get("lignes"))

    def test_reperes_connus(self):
        """Deux points dont on sait de source sûre où ils sont."""
        doc = charger()
        # Helsinki, gare centrale : à terre.
        self.assertTrue(a_terre(24.9414, 60.1719, doc))
        # Milieu du golfe de Finlande : en mer.
        self.assertFalse(a_terre(25.25, 59.90, doc))

    def test_la_distance_croit_en_s_eloignant(self):
        doc = charger()
        proche = distance_terre(24.90, 60.10, doc)
        loin = distance_terre(25.25, 59.90, doc)
        self.assertLess(proche, loin)
        self.assertGreater(loin / NM, 10.0)


class TestScenarios(unittest.TestCase):

    def test_toutes_les_origines_sont_au_large(self):
        doc = charger()
        for p in sorted((ROOT / "scenarios").glob("*.toml")):
            o = tomllib.loads(p.read_text(encoding="utf-8")).get("origine")
            if not o:
                continue
            verdict, d = juger(o["lat"], o["lon"], doc, MARGE_NM)
            self.assertEqual(
                verdict, "large",
                "%s est en %s : %.2f NM de la côte, il en faut %.0f"
                % (p.stem, verdict, d, MARGE_NM))

    def test_les_origines_sont_dans_la_zone_cartographiee(self):
        """Une origine hors du découpage afficherait un scope sans côte —
        et le contrôle ci-dessus la déclarerait « au large » par ignorance."""
        doc = charger()
        ref_lat, ref_lon = doc["ref"]
        demi = doc["rayon_nm"] / 60.0
        for p in sorted((ROOT / "scenarios").glob("*.toml")):
            o = tomllib.loads(p.read_text(encoding="utf-8")).get("origine")
            if not o:
                continue
            self.assertLess(abs(o["lat"] - ref_lat), demi, p.stem)
            self.assertLess(abs(o["lon"] - ref_lon), demi * 2.5, p.stem)


if __name__ == "__main__":
    unittest.main()
