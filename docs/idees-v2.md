# Idées pour une v2

Pas planifié, pas dans le code : de quoi ne pas les perdre.

## Poser le porteur n'importe où sur une carte du monde

Ce qui existe déjà : la physique est en plan tangent local autour de
`[origine]` (`sim/geo.py` `Projection`), donc un scénario peut être posé à
n'importe quelle latitude/longitude sans toucher au cœur. Le simulateur n'a
pas besoin d'AIS pour tourner — les scénarios scriptés suffisent.

Ce qui manque :

1. **Fond de carte.** `web/coastline.json` est découpé à 300 NM autour du golfe
   de Finlande (`tools/coastline.py`). Ailleurs, la mer serait vide (et la vue
   passerelle sans côte). Options : générer un fichier par région
   (`tools/coastline.py --lat .. --lon ..`), ou embarquer Natural Earth 50 m
   monde entier (~quelques Mo) et découper côté serveur à la volée
   (`/coastline.json?lat=..&lon=..`).
2. **Sélecteur.** Une carte du monde dans la console (cliquer un point = origine),
   qui recharge le scénario courant avec cette origine ; les contacts restent
   relatifs (`brg`/`rng_nm`), ils suivent.
3. **Contrôle des eaux.** `tools/eaux.py` fait déjà le test terre/eaux libres ;
   le brancher sur le clic pour refuser (ou avertir sur) une origine à terre.
4. **Trafic** : sans AIS réel hors Finlande (digitraffic ne couvre que ses
   eaux), un générateur de trafic synthétique (densité par zone, routes de
   rail) donnerait de la vie à n'importe quel océan.

Effort : (1)+(2) restent modestes ; (4) est le vrai chantier.
