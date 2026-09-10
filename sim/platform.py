"""IPMS — conduite de la plateforme.

À bord, le CMS et l'IPMS sont deux systèmes distincts qui dialoguent. Ici
l'IPMS est soit modélisé en logiciel (mode autonome), soit lu sur un vrai
automate OpenPLC par Modbus TCP (mode `plc`).

Le couplage qui justifie tout le montage : la production d'eau glacée
refroidit les baies radar. Si elle tombe, la température monte, l'émission
est déclassée, le SNR chute — et l'horizon de détection rétrécit à l'écran.
Un défaut d'automate réduit physiquement la capacité de veille.
"""
import time

from .geo import approach

T_NOMINAL = 42.0
T_DERATE = 70.0      # au-delà, déclassement progressif de l'émission
T_TRIP = 88.0        # arrêt d'urgence de l'émetteur

# Plafond du pas de temps appliqué à un ingest(). Sans lui, le premier appel
# suivant une coupure Modbus verrait un dt énorme (le temps de la coupure),
# et approach() — dont le facteur est min(1, dt/tau) — referait sauter la
# valeur d'un coup. C'est exactement le saut qu'on cherche à éviter : le
# plafond ramène ce premier pas à la taille d'un pas normal.
_DT_MAX = 1.0


class Platform:
    def __init__(self, timescale=1.0):
        self.pump = True
        self.temp = T_NOMINAL
        self.press = 4.2
        self.rpm = 15.0
        self.source = "SIMULÉ"
        self.tripped = False
        self._last_ingest = None   # horodatage du dernier ingest() réussi
        # Levier de démo, pas de réalisme : accélère la dérive (step comme
        # ingest) sans changer les constantes de temps par défaut tant
        # qu'il vaut 1.0. Voir FIELD_TIMESCALE côté field_sim.py — les deux
        # doivent être réglés ensemble pour que l'effet se voie sur la
        # console en mode MODBUS.
        self.timescale = max(timescale, 1e-6)

    def step(self, dt):
        if self.source != "SIMULÉ":
            return
        target_t = T_NOMINAL if self.pump else 96.0
        self.temp = approach(self.temp, target_t, (55.0 if self.pump else 90.0) / self.timescale, dt)
        self.press = approach(self.press, 4.2 if self.pump else 0.6, 12.0 / self.timescale, dt)

    def ingest(self, regs, coils):
        """Reprend l'état depuis les registres OpenPLC (voir plc/modbus-map.md).

        Les registres lus sont des CIBLES, pas des valeurs à recopier
        telles quelles. Une affectation brute ferait sauter l'affichage
        instantanément de la dernière valeur du modèle logiciel — parfois
        une alarme au maximum, après une coupure prolongée — à la lecture
        réelle, en contradiction directe avec le principe qui vaut pour tout
        le reste du projet : une valeur qui saute d'un coup se repère
        comme fausse, une valeur qui dérive lentement est crédible. On
        applique donc la même inertie que le modèle logiciel (`step`), avec
        les mêmes constantes de temps — la coupure et le mode dégradé ne
        doivent pas se voir dans la manière dont la plateforme réagit,
        seulement dans le bandeau de source.
        """
        now = time.monotonic()
        dt = min(now - self._last_ingest, _DT_MAX) if self._last_ingest else 0.2
        self._last_ingest = now

        self.source = "MODBUS"
        cible_temp = regs[0] / 10.0
        cible_press = regs[1] / 100.0
        cible_rpm = regs[2] / 10.0
        tau_temp = (55.0 if self.pump else 90.0) / self.timescale
        self.temp = approach(self.temp, cible_temp, tau_temp, dt)
        self.press = approach(self.press, cible_press, 12.0 / self.timescale, dt)
        self.rpm = approach(self.rpm, cible_rpm, 3.0 / self.timescale, dt)
        # Le contacteur de pompe est un signal tout-ou-rien : rien à lisser,
        # une pompe est en marche ou non, instantanément — comme un vrai
        # relais. C'est délibérément la seule affectation brute qui reste.
        self.pump = bool(coils[1])

    @property
    def radar_power(self):
        """Facteur 0..1 appliqué au SNR du radar."""
        if self.temp >= T_TRIP:
            return 0.02
        if self.temp <= T_DERATE:
            return 1.0
        span = (self.temp - T_DERATE) / (T_TRIP - T_DERATE)
        return max(0.15, 1.0 - 0.85 * span)

    def faults(self):
        f = []
        if not self.pump:
            f.append("POMPE REFR.")
        if self.temp >= T_DERATE:
            f.append("TEMP. GUIDE")
        if self.temp >= T_TRIP:
            f.append("ÉMISSION COUPÉE")
        return f

    def snapshot(self):
        return {"pompe": self.pump, "temp": round(self.temp, 1),
                "press": round(self.press, 2), "rpm": round(self.rpm, 1),
                "puissance": round(self.radar_power * 100),
                "source": self.source, "defauts": self.faults()}
