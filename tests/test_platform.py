"""IPMS — transition entre le modèle logiciel et la lecture Modbus.

Le bug que ce fichier garde : `ingest()` faisait une affectation brute des
registres lus, alors que `step()` (le modèle logiciel) fait dériver la
température avec de l'inertie. Résultat observé en pratique — une longue
coupure Modbus laisse la température grimper au maximum en mode dégradé ;
dès que l'automate revient, l'affichage sautait instantanément à la valeur
nominale au lieu d'y revenir progressivement, en contradiction avec le
principe qui vaut pour tout le reste du projet : une valeur qui saute d'un
coup se repère comme fausse.

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.platform import Platform, T_DERATE, T_TRIP  # noqa: E402


def poll(p, regs, coils, duree_s, pas=0.2):
    """Simule les appels périodiques du pont Modbus, comme PlcBridge.

    Force un pas régulier plutôt que de dépendre de l'horloge réelle, en
    retardant artificiellement le dernier horodatage avant chaque appel.
    """
    import time
    t = 0.0
    while t < duree_s:
        p._last_ingest = time.monotonic() - pas
        p.ingest(regs, coils)
        t += pas


class TestReconnexion(unittest.TestCase):
    """Le cas réel qui a révélé le bug : coupure prolongée, alarme au
    maximum, puis retour de l'automate."""

    REGS_NOMINAUX = [420, 420, 150, 0]   # 42,0 °C / 4,20 bar / 15,0 tr/min
    COILS_POMPE_OK = [True, True]

    def setUp(self):
        self.p = Platform()
        self.p.temp, self.p.press = 90.5, 0.6
        self.p.pump, self.p.source = False, "SIMULÉ"

    def test_pas_de_saut_instantane(self):
        """Le premier ingest() après une coupure ne doit pas ramener la
        température à sa valeur nominale d'un coup — c'était le bug."""
        avant = self.p.temp
        poll(self.p, self.REGS_NOMINAUX, self.COILS_POMPE_OK, duree_s=0.2)
        ecart = avant - self.p.temp
        self.assertGreater(ecart, 0, "la température doit commencer à baisser")
        self.assertLess(ecart, 5.0,
                        "un seul appel ne doit faire bouger la température "
                        "que d'un pas normal, pas la ramener au nominal")

    def test_convergence_vers_le_nominal(self):
        """Sur plusieurs minutes de lecture continue, la valeur affichée
        doit rejoindre la cible lue sur l'automate."""
        poll(self.p, self.REGS_NOMINAUX, self.COILS_POMPE_OK, duree_s=240)
        self.assertAlmostEqual(self.p.temp, 42.0, delta=1.5)
        self.assertAlmostEqual(self.p.press, 4.2, delta=0.1)

    def test_defauts_disparaissent_progressivement(self):
        """Les alarmes liées à la température doivent s'effacer au moment
        où la température franchit le seuil — pas toutes en même temps
        dès la reconnexion."""
        poll(self.p, self.REGS_NOMINAUX, self.COILS_POMPE_OK, duree_s=5)
        self.assertIn("TEMP. GUIDE", self.p.faults(),
                      "à 5 s, la température n'a pas encore rejoint le seuil")
        poll(self.p, self.REGS_NOMINAUX, self.COILS_POMPE_OK, duree_s=60)
        self.assertNotIn("TEMP. GUIDE", self.p.faults(),
                         "après convergence, l'alarme doit s'être effacée")

    def test_pompe_reste_instantanee(self):
        """Le contacteur de pompe est un signal tout-ou-rien : lui seul a
        le droit de changer d'état sans délai, contrairement à la
        température — c'est un relais, pas une masse thermique."""
        self.assertFalse(self.p.pump)
        self.p.ingest(self.REGS_NOMINAUX, self.COILS_POMPE_OK)
        self.assertTrue(self.p.pump, "la pompe doit basculer dès le premier ingest()")

    def test_coupure_longue_ne_produit_pas_un_dt_enorme(self):
        """Un `_last_ingest` très ancien (après une coupure de plusieurs
        minutes) ne doit pas produire un pas de temps énorme qui ferait
        sauter la valeur au premier appel qui suit — c'est tout le sens du
        plafond _DT_MAX."""
        import time
        self.p._last_ingest = time.monotonic() - 300.0   # coupure de 5 min
        avant = self.p.temp
        self.p.ingest(self.REGS_NOMINAUX, self.COILS_POMPE_OK)
        self.assertLess(avant - self.p.temp, 5.0,
                        "un dt non plafonné aurait fait sauter la valeur "
                        "quasiment à la cible dès le premier appel")


class TestModeSimuleInchange(unittest.TestCase):
    """Le correctif ne doit rien changer au modèle logiciel pur."""

    def test_regime_etabli(self):
        p = Platform()
        for _ in range(2000):
            p.step(0.05)
        self.assertAlmostEqual(p.temp, 42.0, delta=0.1)
        self.assertAlmostEqual(p.press, 4.2, delta=0.05)

    def test_pompe_coupee_monte_vers_96(self):
        p = Platform()
        p.pump = False
        for _ in range(6000):     # 300 s, largement au-delà de tau=90s
            p.step(0.05)
        self.assertGreater(p.temp, T_DERATE)
        self.assertAlmostEqual(p.temp, 96.0, delta=2.0)


if __name__ == "__main__":
    unittest.main()
