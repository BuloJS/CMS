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

Sans automate (stack Docker par défaut), le tableau électrique n'est pas
masqué pour autant : `step()` en tient un modèle logiciel qui reproduit la
même chaîne que `PROGRAM machine` (batterie → générateur ~12 s → disjoncteur
→ propulsion). Il démarre « tout en ligne » pour que le comportement du
porteur ne change pas tant que l'opérateur n'a rien coupé — l'automate, lui,
part à froid.
"""

# Même durée de démarrage que PAS_GEN dans plc/program.st (0 → 100 % en 12 s).
GEN_DEMARRAGE_S = 12.0


class Machine:
    def __init__(self):
        self.source = "SIMULÉ"
        self.rpm = None
        self.barre = None
        # Modèle logiciel du tableau (SIMULÉ) : tout en ligne au départ.
        self.gen_progres = 100
        self.batterie = True
        self.generateur_pret = True
        self.disjoncteur = True
        self.propulsion_dispo = True
        self._gen = 100.0

        # Commandes opérateur pour le tableau électrique, écrites par le
        # CMS. Persistantes (pas des impulsions) : PlcBridge les réécrit
        # à chaque sondage, comme le cap/la vitesse ordonnés.
        self.cmd_batterie = True
        self.cmd_generateur = True
        self.cmd_disjoncteur = True

    def step(self, dt):
        """Modèle logiciel du tableau électrique — sans effet une fois un
        automate branché, c'est alors lui qui décide (voir `ingest`)."""
        if self.source == "MODBUS":
            return
        self.batterie = self.cmd_batterie
        if self.batterie and self.cmd_generateur:
            self._gen = min(100.0, self._gen + 100.0 * dt / GEN_DEMARRAGE_S)
        else:
            self._gen = 0.0
        self.gen_progres = int(self._gen)
        self.generateur_pret = self._gen >= 100.0
        self.disjoncteur = self.cmd_disjoncteur and self.generateur_pret
        self.propulsion_dispo = self.disjoncteur

    def ingest(self, regs, coils):
        """regs : 3 registres de maintien, %QW20 (RPM), %QW21 (angle de
        barre réel), %QW22 (progression démarrage générateur, 0-100).
        %QW21 est un INT signé côté automate, mais Modbus transporte des
        mots non signés — sans reconversion, -35° arriverait en 65501.

        coils : 4 bobines à partir de %QX3.0 — batterie en ligne,
        générateur prêt, disjoncteur fermé, propulsion disponible."""
        if self.source != "MODBUS":
            # Premier contact avec l'automate : il démarre à froid, ce qui
            # a été demandé au modèle logiciel ne le concerne pas.
            self.cmd_batterie = self.cmd_generateur = self.cmd_disjoncteur = False
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
