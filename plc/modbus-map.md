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
physiquement l'horizon de veille affiché sur le scope.** C'est le seul point
de couplage entre les deux systèmes, et il est volontairement unique — un
vrai bâtiment ne mélange pas la conduite de plateforme et la conduite du
combat, il les fait dialoguer par une interface étroite.

## Sécurité

Modbus n'a ni authentification ni chiffrement. Ce n'est pas un défaut de
cette implémentation, c'est le protocole. Le bus reste sur le réseau interne
du compose ; seule l'interface web de l'automate est publiée. Toute machine
qui atteint le port 502 peut écrire n'importe quelle bobine — ce qui, dans un
lab, est précisément le sujet d'étude.
