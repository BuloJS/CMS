"""Machine — vue sur la barre, la propulsion et le tableau électrique
tenus par l'automate.

Même principe de dégradation gracieuse que `platform.py`/`armement.py` :
SIMULÉ tant qu'aucun automate n'est branché, MODBUS une fois `ingest()`
appelé. Cap et vitesse restent un pur répétiteur — `sim/entities.py
Ownship` seul décide du mouvement réel du porteur (voir
plc/modbus-map.md). Le tableau électrique fait exception : c'est
`PROGRAM machine` qui décide si la propulsion est réellement disponible
(`propulsion_dispo`), pas ce module — il ne fait que relayer ce que
l'automate a décidé, comme `armement.pret` pour un tir.

`cmd_batterie`/`cmd_generateur`/`cmd_disjoncteur` sont l'inverse d'un
`ingest()` : ce que l'opérateur demande, tenu ici entre deux sondages du
pont Modbus pour que `PlcBridge` ait quelque chose à écrire à chaque
passage (voir `services/server.py`).
"""


class Machine:
    def __init__(self):
        self.source = "SIMULÉ"
        self.rpm = None
        self.barre = None
        self.gen_progres = None
        self.batterie = None
        self.generateur_pret = None
        self.disjoncteur = None
        self.propulsion_dispo = None

        # Commandes opérateur pour le tableau électrique, écrites par le
        # CMS. Persistantes (pas des impulsions) : PlcBridge les réécrit
        # à chaque sondage, comme le cap/la vitesse ordonnés.
        self.cmd_batterie = False
        self.cmd_generateur = False
        self.cmd_disjoncteur = False

    def ingest(self, regs, coils):
        """regs : 3 registres de maintien, %QW20 (RPM), %QW21 (angle de
        barre réel), %QW22 (progression démarrage générateur, 0-100).
        %QW21 est un INT signé côté automate, mais Modbus transporte des
        mots non signés — sans reconversion, -35° arriverait en 65501.

        coils : 4 bobines à partir de %QX3.0 — batterie en ligne,
        générateur prêt, disjoncteur fermé, propulsion disponible."""
        self.source = "MODBUS"
        self.rpm = regs[0]
        brute = regs[1]
        self.barre = brute - 65536 if brute >= 32768 else brute
        self.gen_progres = regs[2]
        self.batterie = coils[0]
        self.generateur_pret = coils[1]
        self.disjoncteur = coils[2]
        self.propulsion_dispo = coils[3]

    def snapshot(self):
        return {"source": self.source, "rpm": self.rpm, "barre": self.barre,
                "gen_progres": self.gen_progres, "batterie": self.batterie,
                "generateur_pret": self.generateur_pret,
                "disjoncteur": self.disjoncteur,
                "propulsion_dispo": self.propulsion_dispo}
