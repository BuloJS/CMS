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

**Rechargement automatique**, +1 munition à intervalle fixe tant que le
magasin n'est pas plein, jamais au-delà de la dotation initiale — un délai
par type d'arme, pas une valeur unique :

| Effecteur | Délai de rechargement |
| --- | --- |
| CIWS | 5 s |
| Artillerie 76 mm | 10 s |
| SAM courte portée | 15 s |
| SSM | 20 s |

Un seul bloc `TON` par effecteur, qui se réarme lui-même sur le front de
son propre passage à vrai (motif d'oscillateur classique, un seul appel du
bloc par cycle — deux appels du même `TON` dans un même scan avec des `IN`
différents fonctionnerait aussi mais complique la lecture pour rien).
Vérifié scan par scan en rejouant la même logique en Python avant
d'écrire le `.st` : le rythme est bien d'une munition toutes les *n*
secondes, sans double-incrément ni dépassement du plafond.

Bobines lues d'un seul bloc contigu, `read_coils(8, 12)` : indices 0-3
= `%QX1.0-1.3` (prêt), 4-7 = padding inutilisé (`%QX1.4-1.7`), 8-11 =
`%QX2.0-2.3` (défaut). Voir `sim/armement.Armement.ingest()`.

**Côté CMS, ce bloc est lu (`sim/armement.py`, panneau « Armement —
lanceur » de la console) mais n'a pas encore autorité sur la simulation.**
`sim/tewa.py Effector.rounds`/`.busy` continue de décider ce qu'un
engagement consomme réellement ; le panneau et les boutons de tir de test
de la console (un par effecteur) ne servent aujourd'hui qu'à valider le
séquencement PLC lui-même, en circuit fermé sur l'automate. La prochaine
étape, une fois ce séquencement validé, est de donner à ce pont la même
autorité que `Platform.ingest` sur la pompe — remplacer le modèle
logiciel plutôt que le doubler.

## Sécurité

Modbus n'a ni authentification ni chiffrement. Ce n'est pas un défaut de
cette implémentation, c'est le protocole. Le bus reste sur le réseau interne
du compose ; seule l'interface web de l'automate est publiée. Toute machine
qui atteint le port 502 peut écrire n'importe quelle bobine — ce qui, dans un
lab, est précisément le sujet d'étude.
