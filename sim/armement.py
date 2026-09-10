"""Armement — vue sur le séquencement du lanceur tenu par l'automate.

Contrairement à `platform.py`, ce module n'a pas encore autorité sur la
simulation : `sim/tewa.py` continue de décider des munitions et canaux
disponibles en logiciel (`Effector.rounds`, `.busy`). C'est une lecture
seule du PLC armement pendant que son séquencement se met au point côté
OpenPLC — voir plc/modbus-map.md. La prochaine étape, une fois ce
séquencement validé, est de lui donner la même autorité que la pompe.
"""

EFFECTEURS = ("sam", "ciws", "gun", "ssm")


class Armement:
    def __init__(self):
        self.source = "SIMULÉ"
        self.munitions = {k: None for k in EFFECTEURS}
        self.pret = {k: None for k in EFFECTEURS}
        self.defaut = {k: None for k in EFFECTEURS}

    def ingest(self, regs, coils):
        """regs : 4 registres de maintien, munitions par effecteur.
        coils : 12 bobines à partir de %QX1.0 — les 4 premières sont
        « prêt », les 4 suivantes du padding (%QX1.4-1.7, inutilisées),
        les 4 dernières « défaut interlock »."""
        self.source = "MODBUS"
        for i, k in enumerate(EFFECTEURS):
            self.munitions[k] = regs[i]
            self.pret[k] = coils[i]
            self.defaut[k] = coils[8 + i]

    def snapshot(self):
        return {"source": self.source, "munitions": dict(self.munitions),
                "pret": dict(self.pret), "defaut": dict(self.defaut)}
