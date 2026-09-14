"""Machine — lecture de la barre, du RPM et du tableau électrique tenus
par l'automate."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.machine import Machine  # noqa: E402

COILS_TOUT_EN_LIGNE = [True, True, True, True]
COILS_HORS_TENSION = [False, False, False, False]


class TestMachine(unittest.TestCase):
    def test_source_par_defaut(self):
        m = Machine()
        self.assertEqual(m.source, "SIMULÉ")
        self.assertIsNone(m.rpm)
        self.assertIsNone(m.barre)
        self.assertIsNone(m.gen_progres)
        self.assertIsNone(m.propulsion_dispo)
        self.assertFalse(m.cmd_batterie)
        self.assertFalse(m.cmd_generateur)
        self.assertFalse(m.cmd_disjoncteur)

    def test_ingest_bascule_en_modbus(self):
        m = Machine()
        m.ingest([180, 20, 100], COILS_TOUT_EN_LIGNE)
        self.assertEqual(m.source, "MODBUS")
        self.assertEqual(m.rpm, 180)
        self.assertEqual(m.barre, 20)
        self.assertEqual(m.gen_progres, 100)
        self.assertTrue(m.batterie)
        self.assertTrue(m.generateur_pret)
        self.assertTrue(m.disjoncteur)
        self.assertTrue(m.propulsion_dispo)

    def test_angle_de_barre_negatif_reconverti_depuis_le_non_signe(self):
        """%QW21 est un INT signé côté automate ; Modbus le transporte en
        mot non signé — -35° doit revenir en -35, pas en 65501."""
        m = Machine()
        m.ingest([0, 65536 - 35, 0], COILS_HORS_TENSION)
        self.assertEqual(m.barre, -35)

    def test_disjoncteur_ferme_sans_generateur_pret_est_impossible_a_representer(self):
        """Pas une règle de ce module — Machine relaie ce que l'automate
        décide, il ne le recalcule pas. Ce test documente juste que le
        cas « disjoncteur fermé, générateur pas prêt » n'arrive jamais en
        pratique : c'est PROGRAM machine qui l'empêche, pas ce module."""
        m = Machine()
        m.ingest([0, 0, 40], [True, False, False, False])
        self.assertTrue(m.batterie)
        self.assertFalse(m.generateur_pret)
        self.assertFalse(m.disjoncteur)
        self.assertFalse(m.propulsion_dispo)

    def test_snapshot_reflete_l_etat_courant(self):
        m = Machine()
        m.ingest([240, 65536 - 10, 100], COILS_TOUT_EN_LIGNE)
        self.assertEqual(m.snapshot(), {
            "source": "MODBUS", "rpm": 240, "barre": -10, "gen_progres": 100,
            "batterie": True, "generateur_pret": True, "disjoncteur": True,
            "propulsion_dispo": True,
        })

    def test_commandes_operateur_par_defaut_puis_modifiables(self):
        m = Machine()
        m.cmd_batterie = True
        m.cmd_generateur = True
        self.assertTrue(m.cmd_batterie)
        self.assertTrue(m.cmd_generateur)
        self.assertFalse(m.cmd_disjoncteur)


if __name__ == "__main__":
    unittest.main()
