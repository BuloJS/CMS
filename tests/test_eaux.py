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

from tools.eaux import a_terre, carte_pour, dans_la_zone, distance_terre, juger    # noqa: E402
from sim.scenario import load as charger                # noqa: E402
from sim.geo import NM                                   # noqa: E402

CARTE = ROOT / "web" / "coastline.json"
MARGE_NM = 8.0


def charger_carte():
    return json.loads(CARTE.read_text(encoding="utf-8"))


class TestCarte(unittest.TestCase):

    def test_la_carte_porte_les_polygones_de_terre(self):
        """Sans eux, ni remplissage à l'écran ni contrôle possible."""
        doc = charger_carte()
        self.assertTrue(doc.get("terres"), "web/coastline.json sans polygones")
        self.assertTrue(doc.get("lignes"))

    def test_reperes_connus(self):
        """Deux points dont on sait de source sûre où ils sont."""
        doc = charger_carte()
        # Helsinki, gare centrale : à terre.
        self.assertTrue(a_terre(24.9414, 60.1719, doc))
        # Milieu du golfe de Finlande : en mer.
        self.assertFalse(a_terre(25.25, 59.90, doc))

    def test_la_distance_croit_en_s_eloignant(self):
        doc = charger_carte()
        proche = distance_terre(24.90, 60.10, doc)
        loin = distance_terre(25.25, 59.90, doc)
        self.assertLess(proche, loin)
        self.assertGreater(loin / NM, 10.0)


class TestScenarios(unittest.TestCase):

    def test_toutes_les_origines_sont_au_large(self):
        """Contre la carte que la console afficherait : le découpage fin si
        l'origine y est, sinon le monde découpé autour d'elle."""
        fine = charger_carte()
        for p in sorted((ROOT / "scenarios").glob("*.toml")):
            o = charger(p)["origine"]
            if not o:
                continue
            carte = carte_pour(o["lat"], o["lon"], fine)
            verdict, d = juger(o["lat"], o["lon"], carte, MARGE_NM)
            self.assertEqual(
                verdict, "large",
                "%s est en %s : %.2f NM de la côte, il en faut %.0f"
                % (p.stem, verdict, d, MARGE_NM))

    def test_chaque_origine_a_une_carte_avec_de_la_terre(self):
        """Une origine sans aucun polygone de terre autour d'elle serait
        déclarée « au large » par ignorance — c'est ce que ce test attrape.
        Les scénarios posés en pleine mer n'y échappent pas : à 300 NM de
        rayon il reste toujours une côte quelque part."""
        fine = charger_carte()
        for p in sorted((ROOT / "scenarios").glob("*.toml")):
            o = charger(p)["origine"]
            if not o:
                continue
            carte = carte_pour(o["lat"], o["lon"], fine)
            self.assertTrue(carte.get("terres"), p.stem)

    def test_la_zone_fine_ne_couvre_pas_le_monde(self):
        fine = charger_carte()
        self.assertTrue(dans_la_zone(fine, 59.90, 25.25))
        self.assertFalse(dans_la_zone(fine, 36.0, -5.6))      # Gibraltar


if __name__ == "__main__":
    unittest.main()
