"""Machine — vue sur la barre et la propulsion tenues par l'automate.

Même principe de dégradation gracieuse que `platform.py`/`armement.py` :
SIMULÉ tant qu'aucun automate n'est branché, MODBUS une fois `ingest()`
appelé. Contrairement aux deux autres, ce module n'a encore AUCUNE
autorité sur la simulation — `sim/entities.py Ownship` reste seul à
décider du mouvement réel du porteur (choix fait en posant l'architecture
avant d'écrire `PROGRAM machine`, voir plc/modbus-map.md). C'est un
répétiteur pur : la console lit RPM et angle de barre réels de
l'automate, rien ne les renvoie encore vers la physique.
"""


class Machine:
    def __init__(self):
        self.source = "SIMULÉ"
        self.rpm = None
        self.barre = None

    def ingest(self, regs):
        """regs : 2 registres de maintien, %QW20 (RPM) puis %QW21 (angle
        de barre réel). %QW21 est un INT signé côté automate, mais
        Modbus transporte des mots non signés — sans reconversion, -35°
        arriverait en 65501."""
        self.source = "MODBUS"
        self.rpm = regs[0]
        brute = regs[1]
        self.barre = brute - 65536 if brute >= 32768 else brute

    def snapshot(self):
        return {"source": self.source, "rpm": self.rpm, "barre": self.barre}
