# Idées pour une v2

Pas planifié, pas dans le code : de quoi ne pas les perdre.

## Poser le porteur n'importe où sur une carte du monde

**Fait** (scénario `08-monde-gibraltar`) : contacts en lat/lon, fond de carte
monde 50 m découpé autour du porteur, radar dessiné à sa vraie portée et à son
horizon. Voir le README, section carte.

Reste à faire :

1. **Sélecteur.** Une carte du monde dans la console (cliquer un point = origine)
   qui recharge le scénario courant avec cette origine ; les contacts restent
   relatifs (`brg`/`rng_nm`), ils suivent. Le contrôle `tools/eaux.py` refuserait
   (ou avertirait sur) une origine à terre.
2. **Fond de carte plus fin.** Le 50 m est grossier près des ports et des
   archipels. Le 10 m mondial (20 Mo) demanderait un découpage en tuiles servies
   à la demande.
3. **Trafic** : sans AIS réel hors Finlande (digitraffic ne couvre que ses
   eaux), un générateur de trafic synthétique (densité par zone, routes de
   rail) donnerait de la vie à n'importe quel océan.
4. **Longues traversées.** Le plan tangent local se dégrade au-delà de ~150 NM
   de l'origine ; un porteur qui fait route plusieurs heures devrait
   ré-ancrer sa projection en cours de route.
