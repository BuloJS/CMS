"""Armement — lecture du séquencement lanceur tenu par l'automate."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.armement import Armement, EFFECTEURS  # noqa: E402


class TestArmement(unittest.TestCase):
    def test_source_par_defaut(self):
        a = Armement()
        self.assertEqual(a.source, "SIMULÉ")
        self.assertTrue(all(v is None for v in a.munitions.values()))

    def test_ingest_bascule_en_modbus(self):
        a = Armement()
        regs = [16, 999, 120, 8]
        coils = [True, True, False, True] + [False] * 4 + [False, False, True, False]
        a.ingest(regs, coils)
        self.assertEqual(a.source, "MODBUS")
        self.assertEqual(a.munitions, dict(zip(EFFECTEURS, regs)))
        self.assertEqual(a.pret, {"sam": True, "ciws": True, "gun": False, "ssm": True})
        self.assertEqual(a.defaut, {"sam": False, "ciws": False, "gun": True, "ssm": False})

    def test_snapshot_ne_partage_pas_l_etat_interne(self):
        a = Armement()
        a.ingest([1, 2, 3, 4], [True] * 12)
        snap = a.snapshot()
        snap["munitions"]["sam"] = 999
        self.assertEqual(a.munitions["sam"], 1, "snapshot() doit renvoyer une copie")


if __name__ == "__main__":
    unittest.main()
