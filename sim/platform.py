"""IPMS — conduite de la plateforme.

À bord, le CMS et l'IPMS sont deux systèmes distincts qui dialoguent. Ici
l'IPMS est soit modélisé en logiciel (mode autonome), soit lu sur un vrai
automate OpenPLC par Modbus TCP (mode `plc`).

Le couplage qui justifie tout le montage : la production d'eau glacée
refroidit les baies radar. Si elle tombe, la température monte, l'émission
est déclassée, le SNR chute — et l'horizon de détection rétrécit à l'écran.
Un défaut d'automate réduit physiquement la capacité de veille.
"""
from .geo import approach

T_NOMINAL = 42.0
T_DERATE = 70.0      # au-delà, déclassement progressif de l'émission
T_TRIP = 88.0        # arrêt d'urgence de l'émetteur


class Platform:
    def __init__(self):
        self.pump = True
        self.temp = T_NOMINAL
        self.press = 4.2
        self.rpm = 15.0
        self.source = "SIMULÉ"
        self.tripped = False

    def step(self, dt):
        if self.source != "SIMULÉ":
            return
        target_t = T_NOMINAL if self.pump else 96.0
        self.temp = approach(self.temp, target_t, 55.0 if self.pump else 90.0, dt)
        self.press = approach(self.press, 4.2 if self.pump else 0.6, 12.0, dt)

    def ingest(self, regs, coils):
        """Reprend l'état depuis les registres OpenPLC (voir plc/modbus-map.md)."""
        self.source = "MODBUS"
        self.temp = regs[0] / 10.0
        self.press = regs[1] / 100.0
        self.rpm = regs[2] / 10.0
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
