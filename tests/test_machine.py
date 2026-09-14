"""Machine — lecture de la barre et du RPM tenus par l'automate."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.machine import Machine  # noqa: E402


class TestMachine(unittest.TestCase):
    def test_source_par_defaut(self):
        m = Machine()
        self.assertEqual(m.source, "SIMULÉ")
        self.assertIsNone(m.rpm)
        self.assertIsNone(m.barre)

    def test_ingest_bascule_en_modbus(self):
        m = Machine()
        m.ingest([180, 20])
        self.assertEqual(m.source, "MODBUS")
        self.assertEqual(m.rpm, 180)
        self.assertEqual(m.barre, 20)

    def test_angle_de_barre_negatif_reconverti_depuis_le_non_signe(self):
        """%QW21 est un INT signé côté automate ; Modbus le transporte en
        mot non signé — -35° doit revenir en -35, pas en 65501."""
        m = Machine()
        m.ingest([0, 65536 - 35])
        self.assertEqual(m.barre, -35)

    def test_snapshot_reflete_l_etat_courant(self):
        m = Machine()
        m.ingest([240, 65536 - 10])
        self.assertEqual(m.snapshot(), {"source": "MODBUS", "rpm": 240, "barre": -10})


if __name__ == "__main__":
    unittest.main()
