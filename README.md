# CMS-Lab — simulateur de système de gestion de combat naval

Un lab autonome qui simule un bâtiment de surface, sa veille radar, son
pistage, son évaluation de menace et ses solutions d'engagement — et qui
couple le tout à un automate industriel pour la conduite de la plateforme.

![console](docs/console.png)

Le parti pris central, emprunté aux modèles de CMS utilisés en analyse
opérationnelle : **le cœur de simulation ne connaît ni le réseau ni
l'écran.** Il avance d'un pas fixe et rend un instantané. C'est ce qui
permet de le faire tourner à vingt fois la vitesse réelle pour produire des
statistiques, puis de rejouer un run dans la console — avec le même code.

```
sim/  (headless, déterministe, zéro E/S)
  │
  ├─► services/server.py ─ SSE ─► web/  la console d'opérateur
  ├─► tools/record.py    ─────►  un enregistrement JSONL rejouable
  └─► tools/montecarlo.py ────►  N runs, statistiques de doctrine
```

## Démarrer

```bash
docker compose up --build          # puis http://localhost:8000
```

C'est tout. Aucune dépendance tierce : le cœur de simulation, le serveur, le
client Modbus et les capteurs de terrain sont écrits en bibliothèque
standard Python. L'image se construit sans `pip install` et sans réseau.

Sans Docker :

```bash
python3 services/server.py
```

### Avec un vrai automate

```bash
docker compose -f docker-compose.yml -f docker-compose.plc.yml \
  --profile plc up --build
```

Ajoute OpenPLC (interface web sur `:8080`, identifiants `openplc`/`openplc`,
**à changer**) et les capteurs de terrain. Charger `plc/program.st` depuis
l'interface, le compiler, démarrer l'automate.

Si l'automate n'est pas joignable, **rien ne casse** : le pont dégrade, l'IPMS
repasse sur son modèle logiciel et la console affiche `SIMULÉ` au lieu de
`MODBUS`. La stack par défaut est donc utilisable immédiatement.

## Ce qui est modélisé

### Senseurs

Deux lois portent tout le réalisme du domaine.

**L'équation radar en R⁴.** Le rapport signal sur bruit décroît comme la
puissance quatrième de la distance, ce qui crée l'asymétrie qui structure
tout : un missile de 0,09 m² est détecté dix-sept fois plus près qu'une
frégate de 10 000 m². Ce n'est pas un réglage, c'est de la physique.

**L'horizon radio**, `d(NM) ≈ 2,23 (√h_antenne + √h_cible)`. Antenne à 30 m,
missile rasant à 5 m : **17 NM**. À 525 nœuds, cela laisse moins de deux
minutes. Le missile n'approche pas, il *surgit* — et c'est tout le tempo de
la défense antiaérienne navale.

S'y ajoutent l'ESM (trajet passif en R², donc on entend un radar bien avant
de voir la plateforme qui le porte), l'IFF, l'AIS, et un bruit de mesure en
polaire — l'erreur transverse grandit avec la distance, ce qui fait
« frétiller » les pistes lointaines.

**L'AIS est décodé au format de la norme** (UIT-R M.1371) : MMSI, indicatif,
numéro OMI, type de navire, statut de navigation, dimensions hors tout,
tirant d'eau, destination. `sim/ais.py` traduit les codes en libellés, et la
console affiche le tout sous un titre qui dit l'essentiel — *déclaré par le
navire*. Un navire diffuse ce qu'il veut : l'AIS ne prouve rien, il propose
une identité que le radar est libre de contredire.

C'est pour cette raison que **l'absence d'AIS est affichée comme une
information**, et non comme un champ vide. Un mobile de surface qui ne se
déclare pas — transpondeur éteint, hors zone VHF, ou navire non soumis à
obligation — est exactement le contact qui mérite un opérateur. Dans le
scénario du détroit, les deux vedettes sont les seules à ne rien émettre.

### Pistage

Filtre de Kalman à vitesse constante, association au plus proche voisin sous
fenêtre de Mahalanobis, initiation 3 plots sur 5 tours, suppression après 4
tours sans plot, fusion des détections en gisement seul. Écrit à la main en
4×4 ; la matrice d'observation ne retient que la position, donc la covariance
d'innovation est 2×2 et s'inverse en une ligne.

**La console n'affiche jamais la vérité terrain.** Elle affiche le produit du
pistage, avec son ellipse d'incertitude et sa qualité de piste — qui se
dégrade toute seule quand les plots manquent.

### TEWA

L'évaluation de menace produit un score **et le détail de ses facteurs** :
géométrie, temps avant CPA, vitesse, réponse IFF, corrélation ESM,
identification. Un opérateur doit pouvoir contester un classement, donc le
système doit dire pourquoi il classe.

L'affectation d'effecteur produit des **solutions d'engagement**, chacune
portant sa **butée de tir** — l'instant au-delà duquel il est trop tard. C'est
ce compte à rebours qui pilote réellement une console de défense aérienne.
L'ordonnancement tient compte des canaux de conduite de tir, qui sont la
ressource rare : c'est elle qui crée le problème, pas le stock de munitions.

Hors pression de temps, les solutions sont classées par **effet gradué** : le
moyen le plus économique qui fait le travail passe devant. Le système ne
propose pas un missile antinavire contre une vedette à 9 NM.

**Le système propose, l'opérateur dispose.** Rien ne part au tir sans action
explicite, sauf doctrine armée à l'avance — et le journal dit toujours qui a
classé une piste et qui a ouvert le feu.

### Position géographique

![console avec trait de côte et bloc AIS](docs/console-ais.png)

Le cœur travaille en mètres dans un plan tangent local, et n'a jamais vu un
degré de latitude. `sim/geo.Projection` fait la conversion à l'entrée et à la
sortie, avec les vrais rayons de courbure de l'ellipsoïde au point de
référence : sur une centaine de milles, l'écart avec un rayon sphérique moyen
dépasse largement une ellipse d'incertitude.

Un scénario s'ancre sur la carte avec un bloc `[origine]`. Sans lui il reste
purement relatif, et la console n'affiche simplement pas de position — aucun
scénario écrit avant l'ancrage ne casse.

```toml
[origine]
lat = 59.85
lon = 24.85
```

Le trait de côte est du Natural Earth 10 m découpé pour la zone
d'opérations. Il est **réel mais toujours transparent** : le radar voit à
travers.

```bash
curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson
python3 tools/coastline.py ne_10m_coastline.geojson --lat 59.85 --lon 24.85 --rayon 120 -o web/coastline.json
```

### Plateforme

`sim/platform.py` modélise la production d'eau glacée qui refroidit les baies
radar, soit en logiciel, soit depuis un vrai automate. Une perte de pompe fait
monter la température, déclasse l'émission, réduit le SNR — et **rétrécit
l'horizon de détection à l'écran**. Un défaut d'automate diminue physiquement
la capacité de veille. C'est le seul point de couplage entre conduite de
plateforme et conduite du combat, et il est volontairement unique.

Voir [`plc/modbus-map.md`](plc/modbus-map.md).

## Scénarios

| Fichier | Situation | Solution attendue |
| --- | --- | --- |
| `01-detroit-approche` | Trafic marchand dense, deux vedettes non coopératives | Identification. Celle qui illumine en conduite de tir bascule hostile → **artillerie**, pas SAM |
| `02-saturation-asm` | Six missiles rasants en quatre secondes | Détection à l'horizon, SAM sur la butée, CIWS en ultime, leurres |
| `03-avarie-refroidissement` | Même attaque, pompe arrêtée à 60 s | Détection tardive, décrochage de pistes — la bonne réaction est côté IPMS autant que côté CMS |

Les scénarios sont en TOML, en unités du domaine (milles nautiques, nœuds,
pieds), convertis en SI à l'entrée. Une graine fixée les rend reproductibles
au tick près.

## Mode analyse

```bash
python3 tools/montecarlo.py scenarios/02-saturation-asm.toml -n 24
python3 tools/montecarlo.py scenarios/02-saturation-asm.toml -n 24 --no-auto-sam
```

Un résultat mesuré, et il mérite d'être dit tel quel : **sur ce scénario, les
deux doctrines se valent** (25 % de fuite dans les deux cas). Six arrivées
étalées sur une vingtaine de secondes restent traitables en séquence par un
canal CIWS unique — le scénario ne sature donc pas réellement la défense,
malgré son nom. Ce n'est pas un défaut du simulateur, c'est ce que le mode
analyse existe pour révéler. Pour qu'il discrimine, il faut resserrer les
arrivées à quelques secondes, augmenter leur nombre, ou revoir le Pk du CIWS
— tous des paramètres du scénario et de `sim/tewa.py`.

## Tests

```bash
python3 -m unittest discover -s tests
```

Volontairement courts, et sans dépendance. Ils portent sur ce qui casse en
silence : la projection géographique, l'horizon radio, la décroissance du SNR
en R⁴, le CPA et le temps d'interception. Une constante mal placée dans l'une
de ces formules ne lève aucune exception — elle décale simplement tout.

## Aperçu hors ligne

```bash
python3 tools/record.py scenarios/02-saturation-asm.toml \
  -o fixtures/02-saturation-asm.jsonl --hz 1 --from 245 --until 500 --slim
node tools/build-artifact.mjs
```

Produit `dist/console-artifact.html` : la console complète avec un
enregistrement embarqué, en un seul fichier, sans backend. La console détecte
la présence de l'enregistrement et bascule seule en source « rejeu » — aucune
duplication de code entre la version connectée et la version hors ligne.

## Console

| Touche | Action |
| --- | --- |
| Clic sur un plot / une menace | Accrocher la piste |
| `F1` | Décrocher |
| `F5` | Ligne de gisement et cercle de distance |
| `F6` / `F7` | Traces / ellipses d'incertitude |
| `F8` | Larguer les leurres |
| `F9` | Armer la doctrine SAM automatique |
| `F10` | Arrêter la pompe (poste instructeur) |

Échelles 5 / 10 / 25 / 50 / 100 NM. Le bandeau de curseur donne gisement,
distance **et position géographique** en degrés et minutes décimales — la
façon dont une position se dicte à la passerelle — dès que le scénario porte
une `[origine]`.

Symbologie : cercle = ami, losange = hostile, carré = neutre, quatre-feuilles
= inconnu. Barre au-dessus = piste aérienne. Vecteur = trois minutes de route.

## Limites connues

- Le radar est **2D** : les pistes n'ont pas d'altitude. La doctrine
  d'identification ne peut donc pas invoquer un « profil rasant », seulement
  la géométrie, la vitesse et l'absence de réponse IFF.
- Le vol des intercepteurs est réduit à un temps de vol et une probabilité de
  destruction. Pas de navigation proportionnelle, pas d'enveloppe de manœuvre.
- Pas de fouillis de mer, de multitrajet ni de conduits de propagation.
- Le trait de côte est réel (Natural Earth 10 m) mais ne masque rien : pas
  de zone d'ombre, pas de diffraction, un contact derrière une île reste
  visible.

## Sécurité

Réseau interne au compose, port Modbus jamais publié, identifiants OpenPLC par
défaut à changer. Le lab est conçu pour tourner isolé sur une machine de test.
