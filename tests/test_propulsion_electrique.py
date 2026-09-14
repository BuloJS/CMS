"""Coupure réelle de la propulsion — pas seulement le RPM affiché.

Une fois qu'un automate machine est branché (source MODBUS), couper la
propulsion électrique (batterie/générateur/disjoncteur) doit ralentir le
porteur pour de vrai, pas seulement l'aiguille RPM du poste machine :
sinon le tableau électrique n'est qu'un gadget visuel. Voir
sim/engine.py Engine.step() et sim/entities.py Ownship.step().

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.engine import Engine        # noqa: E402
from sim.geo import KT               # noqa: E402
from sim.scenario import load        # noqa: E402

COILS_TOUT_EN_LIGNE = [True, True, True, True]
COILS_PAS_PRET = [True, False, False, False]


def _engine():
    """Événements scriptés désactivés — le scénario réel peut ordonner ses
    propres changements de cap/vitesse en cours de route, ce qui n'a rien
    à voir avec ce que ces tests vérifient (interlock électrique)."""
    e = Engine(load(ROOT / "scenarios" / "02-saturation-asm.toml"))
    e.pending = []
    return e


class TestPropulsionElectrique(unittest.TestCase):
    def test_simule_ignore_le_tableau_electrique(self):
        """Sans automate branché (source SIMULÉ), rien à couper : le
        porteur avance normalement vers son ordre de vitesse."""
        e = _engine()
        self.assertEqual(e.machine.source, "SIMULÉ")
        e.own.ordered_speed = 15 * KT
        for _ in range(40):
            e.step()
        self.assertGreater(e.own.speed, 0.0)

    def test_propulsion_indisponible_ramene_la_vitesse_a_zero(self):
        e = _engine()
        e.own.speed = 18 * KT
        e.own.ordered_speed = 18 * KT
        e.machine.ingest([0, 0, 0], COILS_PAS_PRET)   # batterie seule, pas de propulsion
        # accel_tau = 45s (sim/entities.py) : 300s simulées laissent la
        # décroissance exponentielle s'installer très largement (~6.7 tau).
        for _ in range(6000):
            e.step()
        self.assertAlmostEqual(e.own.speed, 0.0, delta=0.1)
        # L'ordre reste affiché tel quel — l'opérateur n'a rien changé.
        self.assertEqual(e.own.ordered_speed, 18 * KT)

    def test_propulsion_disponible_laisse_le_porteur_accelerer(self):
        e = _engine()
        e.own.speed = 0.0
        e.own.ordered_speed = 18 * KT
        e.machine.ingest([0, 0, 100], COILS_TOUT_EN_LIGNE)
        for _ in range(6000):
            e.step()
        self.assertGreater(e.own.speed, 17 * KT)

    def test_retour_de_propulsion_reprend_l_ordre_en_cours(self):
        """Coupure puis retour : le porteur repart vers l'ordre toujours
        affiché, sans que l'opérateur ait dû le redonner."""
        e = _engine()
        e.own.speed = 18 * KT
        e.own.ordered_speed = 18 * KT
        e.machine.ingest([0, 0, 0], COILS_PAS_PRET)
        for _ in range(2000):
            e.step()
        self.assertLess(e.own.speed, 18 * KT)
        e.machine.ingest([0, 0, 100], COILS_TOUT_EN_LIGNE)
        for _ in range(6000):
            e.step()
        self.assertGreater(e.own.speed, 17 * KT)


if __name__ == "__main__":
    unittest.main()
