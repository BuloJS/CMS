# Cartographie Modbus

Ce document est le contrat entre les quatre services. Le programme automate,
le pont, la console et les capteurs simulés en découlent tous — c'est le
premier fichier à mettre à jour, et le seul qui fasse foi.

## Sens de lecture

Deux rôles Modbus distincts cohabitent, et les confondre coûte une soirée :

```
capteurs terrain            OpenPLC                      CMS
(esclaves Modbus)   --502-->  MAÎTRE : lit les capteurs
                              exécute le programme
                              ESCLAVE : expose son image  <--502--  pont (client)
```

L'automate **interrogé** par le pont expose ses variables `%Q` : le programme
écrit dedans. Les `%I` sont ses entrées, il ne peut pas y écrire — d'où la
lecture de **registres de maintien** (fonction 3) côté pont, et non de
registres d'entrée.

## Terrain → automate (OpenPLC maître, `field-sim` esclave)

Les points distants sont mappés à partir de l'adresse 100. **Vérifier le
mapping réellement attribué dans l'interface web d'OpenPLC, page Slave
Devices** : c'est elle qui fait foi, pas ce tableau.

| Automate | Modbus (field-sim:5020) | Unité | Description |
| --- | --- | --- | --- |
| `%IW100` | registre d'entrée 0 | 0,1 °C | Température baies radar |
| `%IW101` | registre d'entrée 1 | 0,01 bar | Pression circuit eau glacée |
| `%IW102` | registre d'entrée 2 | 0,1 tr/min | Rotation antenne |
| `%IX100.0` | entrée TOR 0 | — | Contact de rotation antenne |
| `%IX100.1` | entrée TOR 1 | — | Pompe en marche |

Seule la bobine pompe (adresse 1) accepte l'écriture (fonction 5) : c'est le
point que le F10 « ARRÊT POMPE » de la console commande directement, via
`FIELD_HOST`, pour rejouer une avarie de refroidissement même avec un
automate réel dans la boucle — sans quoi le pont réécrirait l'état lu sur
l'automate à chaque sondage (0,2 s) et écraserait l'ordre. Tout le reste
(température, pression, rotation) reste en lecture seule : ce sont des
mesures, pas des ordres.

## Automate → CMS (OpenPLC esclave, pont client)

| Automate | Modbus (openplc:502) | Unité | Description |
| --- | --- | --- | --- |
| `%QW0` | maintien 0 | 0,1 °C | Température validée |
| `%QW1` | maintien 1 | 0,01 bar | Pression validée |
| `%QW2` | maintien 2 | 0,1 tr/min | Rotation antenne |
| `%QW3` | maintien 3 | 0,1 % | Puissance d'émission autorisée |
| `%QX0.0` | bobine 0 | — | Antenne en rotation |
| `%QX0.1` | bobine 1 | — | Pompe en marche |
| `%QX0.2` | bobine 2 | — | Alarme température haute (temporisée) |
| `%QX0.3` | bobine 3 | — | Coupure d'émission |

## Ce que le CMS en fait

`sim/platform.py` convertit ces registres en un facteur de puissance appliqué
au rapport signal sur bruit du radar. Une chute de puissance réduit la portée
de détection selon la loi en R⁴ : **un défaut d'automate rétrécit
physiquement l'horizon de veille affiché sur le scope.** C'est le point de
couplage IPMS↔CMS, et il reste étroit — un vrai bâtiment ne mélange pas la
conduite de plateforme et la conduite du combat, il les fait dialoguer par
une interface fine. L'armement, ci-dessous, en ajoute un second, tout aussi
étroit et pour la même raison : le lanceur compte ses coups et refuse un
ordre occupé, il ne décide jamais quoi engager.

## CMS → automate (armement — `%MW0`, commande)

**Pas de bobine `%Q` pour la commande.** Une bobine `%Q` est réécrite à
chaque cycle par le programme lui-même (voir `cms_pompe` plus haut) — un
ordre écrit dessus par le CMS serait écrasé au tour suivant, même piège que
la pompe avant son correctif. `%MW0` est un mot mémoire : seul le CMS y
écrit, seul le programme armement le lit. Rien d'autre n'y touche.

**Adresse Modbus réelle : 1024, pas 0.** Dans le serveur Modbus d'OpenPLC,
les registres de maintien 0-1023 sont `%QW`, et 1024-2047 sont `%MW` — un
décalage fixe de +1024, vérifié dans `webserver/core/modbus.cpp`
(`mapUnusedIO()`) d'OpenPLC_v3, pas deviné. `%MW0` s'écrit donc avec la
fonction 6 (écriture registre unique) à l'adresse **1024**. `services/
server.py` (`commander_tir_armement`) écrit le code effecteur puis `255`
peu après, pour laisser le temps à `R_TRIG` de détecter le front avant que
la commande retombe au repos.

| Registre | Valeur | Effecteur |
| --- | --- | --- |
| `%MW0` | 0 | SAM courte portée |
| | 1 | CIWS |
| | 2 | Artillerie 76 mm |
| | 3 | Missile antinavire |
| | 255 | Aucun (repos) |

Un front montant sur `%MW0` déclenche un coup. Le CMS doit remettre `255`
puis reposer la valeur pour un second tir — c'est `R_TRIG` côté PLC qui
porte cette règle, un ordre qui reste affiché ne redéclenche rien.

## Automate → CMS (armement, lecture)

| Automate | Modbus (openplc:502) | Description |
| --- | --- | --- |
| `%QW10` | maintien 10 | Munitions restantes — SAM |
| `%QW11` | maintien 11 | Munitions restantes — CIWS |
| `%QW12` | maintien 12 | Munitions restantes — Artillerie |
| `%QW13` | maintien 13 | Munitions restantes — SSM |
| `%QX1.0`–`%QX1.3` | bobines | Prêt (canal libre, munitions, pas de défaut) — SAM/CIWS/Artillerie/SSM |
| `%QX2.0`–`%QX2.3` | bobines | Défaut interlock — SAM/CIWS/Artillerie/SSM |

Dotations et temporisations de recyclage codées en dur dans
`PROGRAM armement` (`plc/program.st`), alignées sur `sim/tewa.py
Effector.rounds` pour rester cohérentes avec le modèle logiciel qu'elles
remplacent — à ajuster librement, ce ne sont pas des valeurs du domaine.
Les bobines de défaut (`%QX2.x`) ne sont câblées sur rien pour l'instant :
c'est le point d'extension prévu pour un vrai interlock (porte de silo,
sécurité…).

**Consommation par engagement — une rafale au régime de tir réel, pas un
coup arbitraire, et qui se vide *progressivement* pour CIWS/artillerie.**
SAM et SSM partent à l'unité (`%MW0`, décompte instantané) : trop lourds
et trop chers pour en tirer plusieurs sur un seul engagement. CIWS et
artillerie sont des canons à tir continu : au front sur `%MW0`, `muni_*`
(accumulateur `REAL`) décroît à chaque scan (50 ms) pendant `BURST_S`
secondes (3 s, `sim/tewa.py`) au régime de tir de l'arme — pas un saut
instantané au total de la rafale, sinon la barre du panneau électrique
saute d'un coup au lieu de montrer un tir en cours :

| Effecteur | Régime de tir | Coups/scan (50 ms) | Total sur la rafale (3 s) |
| --- | --- | --- | --- |
| CIWS | 4500 c/min | 3,75 | 225 |
| Artillerie 76 mm | 120 c/min | 0,1 | 6 |
| SAM courte portée | — (à l'unité) | — | 1 |
| SSM | — (à l'unité) | — | 1 |

`pret_ciws`/`pret_gun` restent à `FALSE` toute la rafale (canal occupé),
pas seulement un délai de recyclage fixe. Voir `sim/tewa.py
rounds_per_shot()` côté logiciel pour le total — c'est cette valeur, pas
la taille de salve statistique `salvo_for()` (qui reste réservée au Pk
affiché), que `PROGRAM armement` décompte au total sur la rafale.

**Rechargement automatique**, continu lui aussi pendant que l'arme n'est
pas en rafale, jamais au-delà de la dotation initiale :

| Effecteur | Rechargement |
| --- | --- |
| CIWS | dotation pleine (999) en 5 s, en continu |
| Artillerie 76 mm | dotation pleine (120) en 5 s, en continu |
| SAM courte portée | +1 munition toutes les 15 s |
| SSM | +1 munition toutes les 20 s |

CIWS/artillerie rechargent par accumulateur `REAL` (même pas par scan que
la consommation, en sens inverse) — un magasin à moitié vide se remplit
donc proportionnellement plus vite qu'un magasin presque vide, la
dotation pleine revient toujours en 5 s depuis n'importe quel niveau,
pas 5 s par coup manquant. SAM/SSM gardent le motif `TON`+`R_TRIG`
classique (+1 munition à intervalle fixe) : ce sont des missiles à
l'unité, pas des canons, rien à rendre progressif.
Vérifié scan par scan en rejouant la même logique en Python avant
d'écrire le `.st`.

Bobines lues d'un seul bloc contigu, `read_coils(8, 12)` : indices 0-3
= `%QX1.0-1.3` (prêt), 4-7 = padding inutilisé (`%QX1.4-1.7`), 8-11 =
`%QX2.0-2.3` (défaut). Voir `sim/armement.Armement.ingest()`.

**Côté CMS, ce bloc est lu (`sim/armement.py`, panneau « Armement —
lanceur » de la console) et l'automate a maintenant le dernier mot sur un
tir réel.** `sim/tewa.py Effector.rounds`/`.busy` continue de décider si
un engagement est *proposé* (portée, canaux, munitions logicielles), mais
`Engine.engage()` refuse le tir si `armement.pret[effecteur]` est `False`
côté automate — canal occupé ou magasin vide pour de vrai, pas seulement
dans le modèle logiciel. Sans automate (`armement.source == "SIMULÉ"`),
`pret` reste à `None` pour tout effecteur et ne bloque donc jamais : le
logiciel décide seul, comme avant.

Chaque tir accepté (opérateur ou doctrine automatique) rejoue vers
l'automate un seul front sur `%MW0` — un engagement, un front, quel que
soit l'effecteur (`services/server.py`, `commander_tir_armement` — même
principe que le rejeu des événements « pompe » scriptés). Le bouton de
tir de test du panneau reste utile pour valider le séquencement PLC
seul, sans faire tourner tout un scénario de combat.

## CMS → automate (machine — `%MW1`-`%MW7`, commande)

Même raisonnement que l'armement : ce sont des mots mémoire, pas des
bobines `%Q` — une bobine serait réécrite par `PROGRAM machine` à chaque
cycle, un ordre du CMS dessus serait aussitôt perdu. Contrairement à
`%MW0`, ces mots ne sont **pas** des fronts : le cap et la vitesse
restent ordonnés en continu (l'opérateur garde la main tant qu'il ne
change pas d'avis), pas déclenchés un coup à la fois. Le programme les
relit à chaque cycle, sans `R_TRIG`.

**Adresses Modbus réelles : 1025 à 1031** (même décalage +1024 que
`%MW0`, voir plus haut).

| Registre | Modbus | Valeur | Description |
| --- | --- | --- | --- |
| `%MW1` | 1025 | 0-4 | Télégraphe : 0=stop 1=lente 2=demi 3=pleine 4=toute |
| `%MW2` | 1026 | -35..35 | Angle de barre ordonné, degrés |
| `%MW3` | 1027 | 0-359 | Cap ordonné, degrés, tel quel |
| `%MW4` | 1028 | 0-999 | Vitesse ordonnée, dixièmes de nœud, telle quelle |
| `%MW5` | 1029 | 0/1 | Batterie demandée en ligne |
| `%MW6` | 1030 | 0/1 | Générateur demandé en marche |
| `%MW7` | 1031 | 0/1 | Disjoncteur principal demandé fermé — **demandé, pas accordé** |

`%MW5`-`%MW7` sont des booléens transportés en `INT` (0/1), pas des
bobines `%MX` : l'adressage Modbus des bits mémoire n'est établi nulle
part dans ce projet, alors que celui des mots mémoire l'est (`%MW0`,
vérifié dans `webserver/core/modbus.cpp`). Plutôt que de deviner un
nouveau mécanisme, même schéma que le reste — un mot par commande.

Le télégraphe reprend les mêmes cinq crans que le poste machine de la
console (`web/index.html`, bloc `TELEGRAPHE`) et que `PROGRAM machine`
(`rpm_cible`) : un télégraphe réel ordonne un régime, pas un nombre de
nœuds, donc la vitesse ordonnée par l'opérateur (`Ownship.ordered_speed`)
est convertie au cran le plus proche avant d'être écrite — c'est
`services/server.py PlcBridge.run()` qui fait cette conversion
(`_cran_telegraphe()`) à chaque sondage, pas un composant à part que
l'opérateur piloterait directement.

`%MW3`/`%MW4` portent les mêmes ordres **non convertis** — cap en degrés,
vitesse en dixièmes de nœud, ce que l'opérateur a réellement demandé.
`PROGRAM machine` ne les lit pas : `cmd_telegraphe`/`cmd_barre` restent
les seules entrées de sa logique. Ils existent pour que l'automate lui-
même — sa page Monitoring, notamment — ait accès à la commande humaine
d'origine, pas seulement au cran/à l'angle qui en découlent. Sans eux,
quelqu'un qui inspecte l'automate en direct verrait « cran 3, barre 12° »
sans jamais savoir que l'ordre réel était « cap 270, 24 nœuds ».

L'angle de barre suit la même logique mais n'a pas d'équivalent
« ordonné » côté CMS : `Ownship` ne modélise pas de gouvernail, juste un
cap qui tourne à `turn_rate` constant. `PlcBridge` en dérive un angle
plausible, proportionnel à l'écart de cap restant (`ordered_course -
course`, plafonné à ±35°, gain 2 — un écart de 17,5° ou plus met la
barre à fond) : la mèche de l'automate est donc à fond tant que le
porteur est loin de son cap ordonné, et revient au neutre en approchant,
comme le ferait une vraie boucle de pilote automatique.

## Automate → CMS (machine, lecture)

| Automate | Modbus (openplc:502) | Description |
| --- | --- | --- |
| `%QW20` | maintien 20 | RPM arbre |
| `%QW21` | maintien 21 | Angle de barre réel, degrés (signé) |
| `%QW22` | maintien 22 | Progression du démarrage générateur, 0-100 % |
| `%QX3.0` | bobine 24 | Batterie en ligne |
| `%QX3.1` | bobine 25 | Générateur stable |
| `%QX3.2` | bobine 26 | Disjoncteur principal fermé |
| `%QX3.3` | bobine 27 | Propulsion disponible |

`PROGRAM machine` rejoue en façade l'ordre du CMS pour le cap et la
vitesse, avec une rampe réaliste (RPM : 0 à 240 en ~30 s ; barre : -35°
à 35° en ~14 s) au lieu d'un saut instantané — **le CMS garde la vérité
physique du mouvement du porteur** (`sim/entities.py Ownship`, piloté
par la commande `order` existante) : c'est `Ownship.turn_rate`/
`.accel_tau` qui décide combien de temps prend une manœuvre, pas la
rampe de l'automate.

**Le tableau électrique fait exception : l'automate y a une vraie
autorité**, comme `armement.pret` sur un tir. La batterie répond tout de
suite ; le générateur met 12 s à devenir stable (`out_gen_progres` monte
de 0 à 100, remis à zéro si la batterie ou l'ordre générateur retombe en
cours de route — pas de reprise à mi-chemin) ; le disjoncteur **refuse**
de se fermer tant que `generateur_pret` est faux, quoi que `%MW7`
demande ; et `rpm_cible` est forcé à zéro tant que `propulsion_dispo`
est faux, quel que soit le cran demandé sur `%MW1`. Un télégraphe
« pleine vitesse » sans générateur ne fait donc plus rien — le CMS peut
l'écrire, l'automate ne le suit pas.

`sim/machine.py` (`Machine.ingest()`) lit tout ce bloc, et le poste
machine de la console affiche le vrai RPM/angle de barre/état électrique
dès que la source passe en MODBUS, à la place de l'estimation
cosmétique.

Cette autorité électrique déborde sur la vérité physique : quand la
source est MODBUS et que `propulsion_dispo` est faux, `Engine.step()`
(`sim/engine.py`) fait chuter la vitesse réelle du porteur vers 0 —
sinon couper la batterie ralentirait le RPM affiché sans jamais
ralentir le bateau. L'ordre de vitesse affiché à l'opérateur (`ordered_speed`)
n'est pas modifié : dès que la propulsion revient, le porteur reprend
l'accélération vers l'ordre toujours en cours, sans qu'il faille le
redonner.

## Sécurité

Modbus n'a ni authentification ni chiffrement. Ce n'est pas un défaut de
cette implémentation, c'est le protocole. Le bus reste sur le réseau interne
du compose ; seule l'interface web de l'automate est publiée. Toute machine
qui atteint le port 502 peut écrire n'importe quelle bobine — ce qui, dans un
lab, est précisément le sujet d'étude.
