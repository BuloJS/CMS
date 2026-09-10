"""Armement — lecture du séquencement lanceur tenu par l'automate."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.armement import Armement, EFFECTEURS  # noqa: E402
from sim.engine import Engine                  # noqa: E402
from sim.scenario import load                  # noqa: E402
from sim.tracker import Track                  # noqa: E402

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "06-plc-armement.toml"


def moteur_avec_piste():
    """Un moteur sur l'atelier vide, avec une piste stationnaire injectée
    directement dans le pisteur — inutile de faire tourner un vrai radar
    pour tester le seul comportement d'engage() qui nous intéresse ici."""
    e = Engine(load(SCENARIO))
    tr = Track(3000.0, 3000.0, e.t)
    e.tracker.tracks[tr.num] = tr
    return e, tr.num


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


class TestAutoriteDuLanceurSurEngage(unittest.TestCase):
    """Le cas qui rend l'automate intéressant plutôt que décoratif : une
    fois connecté, c'est lui qui a le dernier mot sur un tir, pas le seul
    modèle logiciel de sim/tewa.py."""

    def test_sans_automate_le_logiciel_decide_seul(self):
        e, piste = moteur_avec_piste()
        self.assertEqual(e.armement.source, "SIMULÉ")
        self.assertTrue(e.engage(piste, "sam"),
                        "sans automate, armement.pret (toujours None) ne doit jamais bloquer")

    def test_canal_occupe_cote_automate_refuse_le_tir(self):
        e, piste = moteur_avec_piste()
        e.armement.ingest([16, 999, 120, 8], [False, True, True, True] + [False] * 8)
        self.assertEqual(e.armement.source, "MODBUS")
        self.assertFalse(e.engage(piste, "sam"),
                         "le lanceur dit ne pas être prêt : le tir doit être refusé")

    def test_automate_pret_autorise_le_tir(self):
        e, piste = moteur_avec_piste()
        e.armement.ingest([16, 999, 120, 8], [True, True, True, True] + [False] * 8)
        self.assertTrue(e.engage(piste, "sam"))


if __name__ == "__main__":
    unittest.main()
