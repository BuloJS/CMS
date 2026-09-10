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

**Côté CMS, ce bloc n'est pas encore lu.** Le programme automate est
compilé et vérifié (voir `scenarios/06-plc-armement.toml`, un atelier sans
piste ni événement, pensé pour tester l'automate seul), mais `services/
server.py` et `sim/` ne consomment pas encore ces registres — la prochaine
étape, quand le séquencement PLC sera validé côté OpenPLC, est d'écrire un
pont symétrique à celui de la plateforme (`Platform.ingest`) qui fait
autorité sur les munitions et l'état prêt/occupé à la place du modèle
logiciel actuel.

## Sécurité

Modbus n'a ni authentification ni chiffrement. Ce n'est pas un défaut de
cette implémentation, c'est le protocole. Le bus reste sur le réseau interne
du compose ; seule l'interface web de l'automate est publiée. Toute machine
qui atteint le port 502 peut écrire n'importe quelle bobine — ce qui, dans un
lab, est précisément le sujet d'étude.
