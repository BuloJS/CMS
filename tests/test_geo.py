"""Contrôles sur la géométrie et les deux lois de senseur.

Volontairement court. Ces valeurs ne sont pas des inventions de test : ce
sont les repères du domaine, ceux qu'on vérifie de tête. Une constante mal
placée dans la projection ou dans l'équation radar ne lève aucune
exception — elle décale simplement tout, en silence. C'est le seul type de
panne que ce fichier existe pour attraper.

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.geo import NM, Projection, cpa, intercept_time  # noqa: E402
from sim.sensors import Radar, radar_horizon             # noqa: E402


class TestProjection(unittest.TestCase):
    """La conversion lat/lon <-> plan tangent. Tout le branchement AIS
    repose dessus, et une erreur de 2 % y est invisible à l'œil."""

    def setUp(self):
        self.p = Projection(60.10, 24.90)          # golfe de Finlande

    def test_origine_est_nulle(self):
        x, y = self.p.to_xy(60.10, 24.90)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)

    def test_un_degre_de_latitude(self):
        """~111,3 km à cette latitude. Repère universel."""
        _, y = self.p.to_xy(61.10, 24.90)
        self.assertAlmostEqual(y / 1000.0, 111.3, delta=0.6)

    def test_longitude_resserree_par_le_cosinus(self):
        """À 60°N un degré de longitude vaut la moitié d'un degré à
        l'équateur. Oublier le cos(lat) double toutes les distances est-ouest."""
        x, _ = self.p.to_xy(60.10, 25.90)
        self.assertAlmostEqual(x / 1000.0, 111.3 * 0.4985, delta=0.8)

    def test_aller_retour(self):
        for lat, lon in [(60.30, 25.40), (59.80, 24.10), (60.10, 24.90)]:
            x, y = self.p.to_xy(lat, lon)
            rlat, rlon = self.p.to_latlon(x, y)
            self.assertAlmostEqual(rlat, lat, places=9)
            self.assertAlmostEqual(rlon, lon, places=9)

    def test_passage_antimeridien(self):
        """179°E et 179°W sont à 2° l'un de l'autre, pas à 358°."""
        p = Projection(0.0, 179.5)
        x_est, _ = p.to_xy(0.0, -179.5)
        self.assertAlmostEqual(x_est / 1000.0, 111.3, delta=0.5)

    def test_axes_dans_le_bon_sens(self):
        x, y = self.p.to_xy(60.20, 25.00)      # au nord-est
        self.assertGreater(x, 0)               # x vers l'est
        self.assertGreater(y, 0)               # y vers le nord


class TestSenseurs(unittest.TestCase):

    def test_horizon_radio(self):
        """Antenne à 30 m, missile rasant à 5 m : 17,2 NM. C'est le chiffre
        qui donne tout le tempo de la défense antiaérienne navale."""
        self.assertAlmostEqual(radar_horizon(30.0, 5.0) / NM, 17.2, delta=0.1)

    def test_snr_en_puissance_quatrieme(self):
        """Distance doublée -> 12 dB de moins. C'est la loi en R^4, et c'est
        elle qui rend un missile détectable dix-sept fois plus près qu'une
        frégate."""
        r = Radar()
        self.assertAlmostEqual(r.snr_db(1.0, 10 * NM) - r.snr_db(1.0, 20 * NM),
                               12.04, delta=0.05)


class TestGeometrie(unittest.TestCase):

    def test_cpa_route_de_collision(self):
        """Contact plein nord à 10 NM qui descend droit sur le porteur."""
        d, t = cpa(0.0, 10 * NM, 0.0, -100.0)
        self.assertAlmostEqual(d, 0.0, delta=1.0)
        self.assertAlmostEqual(t, 10 * NM / 100.0, delta=0.5)

    def test_cpa_contact_qui_souvre(self):
        """TCPA négatif : le CPA est derrière nous, il n'y a rien à calculer."""
        _, t = cpa(0.0, 10 * NM, 0.0, +100.0)
        self.assertLess(t, 0)

    def test_intercept_impossible_si_la_cible_est_plus_rapide(self):
        """Cible qui fuit à 300 m/s, intercepteur à 200 : pas de solution."""
        self.assertIsNone(intercept_time(0.0, 10 * NM, 0.0, 300.0, 200.0))

    def test_intercept_frontal(self):
        """Cible à 10 NM fonçant à 250 m/s, intercepteur à 750 : la
        fermeture se fait à 1000 m/s."""
        t = intercept_time(0.0, 10 * NM, 0.0, -250.0, 750.0)
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t, 10 * NM / 1000.0, delta=0.3)


if __name__ == "__main__":
    unittest.main()
