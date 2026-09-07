# Pistes pour la suite

Classées par rapport entre ce que ça apporte et ce que ça coûte. Chaque
entrée dit par où attaquer dans le code — c'est ce qui manque le plus quand
on rouvre un projet trois semaines plus tard.

---

## Déjà fait

- **Projection géographique** (`sim/geo.Projection`) et ancrage des scénarios
  par un bloc `[origine]`. Les scénarios sans ancrage restent relatifs.
- **Décodage AIS normalisé** (`sim/ais.py`) : types de navire, statuts de
  navigation, dimensions, tirant d'eau, surface équivalente radar estimée.
- **Ingestion de trafic réel** (`services/ais.py`) : Digitraffic ou instantané
  rejoué, dégradation propre, scénario `04-veille-trafic-reel`. Le scénario
  déclare sa source par un bloc `[ais]` et le pont suit le scénario courant,
  donc basculer depuis le menu de la console suffit.
- **Console adaptée aux données réelles** : échelle 100 NM, position
  géographique du curseur, bloc AIS dans le panneau de piste, marquage des
  contacts de surface qui n'émettent pas, état de la source dans le bandeau.
- **Fond de carte réel**, Natural Earth 10 m découpé par `tools/coastline.py` :
  trait de côte et polygones de terre remplis. `tools/eaux.py` vérifie que
  chaque scénario est posé au large — deux l'étaient à 2,5 NM d'un port.
- **Bruit de manœuvre adaptatif** dans le filtre — voir ci-dessous.
- **Contrôle de vraisemblance AIS** (`sim/veracite.py`) : écart de position,
  écart de cinématique, extinction de transpondeur, statut contredit, gabarit
  incompatible, MMSI hors plage. Corrélation refaite sur la position
  déclarée. Scénario `05-identite-douteuse`.
- **Console bilingue** français / anglais, bouton dans le bandeau et
  détection de la langue du navigateur. Le moteur n'envoie plus de phrases
  mais des codes et des paramètres — le journal, les anomalies et les
  libellés AIS se mettent en mots côté console.
- **72 tests** : projection, senseurs, convergence du filtre, décodage AIS,
  pont d'ingestion, vraisemblance — dont un test d'intégration qui vérifie
  l'absence de fausse alarme sur du trafic honnête.

---

## Ce que le trafic réel a révélé

Ça valait la peine d'être noté, parce que c'est exactement ce pour quoi on
branche des données réelles.

Le pistage était réglé pour des missiles. Bruit de manœuvre `q = 3 m²/s³` :
en un tour d'antenne de quatre secondes, cela autorise sept nœuds d'écart-type
sur la vitesse. Pour un missile qui encaisse plusieurs g, c'est juste. Pour un
porte-conteneurs, le filtre suivait le bruit de mesure au lieu de le moyenner
et rendait **une vitesse fausse de moitié sur une position parfaitement
juste** — erreurs mesurées de +50 à +120 % sur du trafic marchand.

Invisible à l'écran : la position était bonne, la piste bien formée, la
qualité à 0,98. Mais le TEWA en tire le temps avant CPA et la butée de tir.

`Track._adapte_q` indexe désormais `q` sur la vitesse estimée, borné par
l'ancienne valeur en haut. Erreurs ramenées à ±10 %, Monte-Carlo du scénario
antinavire strictement inchangé (25,0 %). Le test `test_caboteur` échoue si on
revient en arrière.

---

## Comment les seuils ont été choisis

À noter pour la suite, parce que la méthode se réutilise. Les deux premiers
jets de seuils étaient au jugé, et les deux ont produit des fausses alarmes
sur du trafic parfaitement honnête — d'abord parce qu'ils s'appuyaient sur
`quality`, qui ne mesure que la position et ne dit rien de la vitesse, puis
parce que trois sigma ne couvrent pas la queue de distribution du filtre.

La méthode qui a marché : instrumenter les scénarios honnêtes, récolter
351 000 relevés de position et 234 000 de cinématique, et balayer la grille
de seuils en comptant les fausses alarmes. Cinq sigma en position, cinq en
cinématique avec une garde à trois nœuds d'incertitude de vitesse : zéro.

Le principe derrière : **une fausse alarme coûte plus cher qu'une détection
manquée.** Elle est journalisée, elle reste au journal quand elle s'efface,
et elle apprend à l'opérateur à ignorer l'indicateur. Le prix payé est
assumé — un mensonge subtil sur la cinématique reste indiscernable du bruit.

---

## 1. L'usurpation cohérente

**Ce que les contrôles actuels ne voient pas.**

Un fraudeur qui déclare une position, une cinématique et une identité toutes
plausibles et mutuellement compatibles passe sans rien déclencher. C'est le
cas difficile, et il demande autre chose que des contrôles instantanés :

- **Corrélation dans la durée.** Un MMSI qui apparaît là où un autre vient de
  disparaître. Une piste tenue au radar dont l'identité AIS change en cours
  de route. Il faut garder un historique par piste, ce que `Track` ne fait
  pas aujourd'hui.
- **Deux navires, un MMSI.** Deux déclarations au même MMSI en des points
  différents : l'une des deux ment, et le système peut le dire sans savoir
  laquelle.
- **Cohérence avec le trafic.** Un navire qui se déclare cargo mais ne suit
  aucun rail, ou qui manœuvre comme rien de ce qui porte ce type.

---

## 2. Compléter les tests

**Le socle est posé, il manque deux morceaux.**

Projection, senseurs, convergence du filtre, décodage AIS et vraisemblance
sont couverts. Reste `sim/tewa.py` : `salvo_for()`, le rejet hors enveloppe,
le signe de la butée. C'est la dernière couche qui produit un chiffre affiché
sans filet — et la butée de tir est le chiffre sur lequel un opérateur
décide.

---

## 3. L'altitude des pistes

**Limitation documentée, et la plus gênante.**

Le radar est 2D : les pistes n'ont pas d'altitude. La doctrine
d'identification ne peut donc pas invoquer un « profil rasant », alors que
c'est le critère le plus spécifiquement naval qui soit — un mobile à 5 m
au-dessus de l'eau n'a qu'une seule raison d'être là.

Deux voies :
- **Ajouter z au filtre** : l'état passe de 4 à 6 dimensions. Les helpers
  matriciels de `tracker.py` sont déjà génériques, mais `gate()` et `update()`
  supposent une observation 2D.
- **Plus simple et plus réaliste** : un second senseur 3D (radar
  multifonction) à portée réduite, qui fournit une altitude aux pistes déjà
  formées. C'est comme ça qu'un vrai bâtiment est équipé, et ça évite de
  toucher au filtre.

Ça débloque la règle de menace « rasant convergent », qui est aujourd'hui
absente de `tewa.evaluate()` faute de donnée.

---

## 4. Un scénario qui sature vraiment

**Une demi-heure.**

Constat mesuré : sur `02-saturation-asm`, doctrine SAM armée et désarmée
donnent le même résultat, 25 % de fuite. Six arrivées étalées sur une
vingtaine de secondes restent traitables en séquence par un canal CIWS unique.
Le scénario ne sature pas, malgré son nom.

**Préalable : afficher un intervalle de confiance.** `tools/montecarlo.py`
sort une proportion brute, affichée avec une décimale. Sur 6 fuites en 24
runs, l'intervalle de Wilson à 95 % va de 12 % à 45 % — deux doctrines qui
diffèrent de vingt points afficheraient toutes deux « 25 % ». Tant que l'IC
n'est pas là, l'outil ne peut pas répondre à la question pour laquelle il
existe, et ce scénario est invérifiable par construction. Une dizaine de
lignes, et il faudra n≈200 plutôt que 24 (2,6 s par run, donc 9 minutes).

Trois leviers, dans `scenarios/*.toml` et `sim/tewa.py` :
- resserrer les tirs à quelques secondes d'écart, ou tirer depuis deux
  gisements très différents pour forcer la rotation d'antenne ;
- augmenter le nombre de munitions ;
- revoir le Pk du CIWS, aujourd'hui à 0,55 par coup et 0,91 en rafale de trois.

Le but n'est pas d'obtenir un joli chiffre, c'est d'obtenir un scénario où le
choix de doctrine **change quelque chose de mesurable**. Sans ça, le mode
Monte-Carlo ne sert à rien.

---

## 5. Une barre de rejeu dans la console

**Deux heures, et ça se rentabilise immédiatement.**

`ReplaySource` sait déjà lire un enregistrement et le rejouer. Il lui manque
un curseur : sauter à t=250 sans attendre, revenir en arrière, avancer image
par image. Indispensable pour déboguer un comportement de pistage, et
très agréable à démontrer.

Point d'entrée : `web/index.html`, classe `ReplaySource` — l'index `i` est
déjà là, il suffit de l'exposer.

---

## 6. Le poste sécurité — là où l'automate devient la vedette

**Un week-end.**

C'est la partie où OpenPLC cesse d'être un figurant. Un schéma de coque, des
compartiments, des détecteurs incendie et voie d'eau, des pompes, des vannes
d'isolement. Détecteur haut → démarrage de pompe → si le niveau ne baisse pas
en 30 s → alarme majeure.

C'est exactement ce pour quoi un automate existe, et ça s'écrit en ladder avec
un vrai plaisir. Prolonger `plc/program.st` et `plc/modbus-map.md` ; ajouter un
panneau à la console.

---

## 7. Le vol des intercepteurs

**Aujourd'hui réduit à un temps de vol et un tirage de probabilité.**

Une navigation proportionnelle donnerait un missile qu'on voit partir,
manœuvrer et parfois manquer. Gain surtout visuel, mais c'est le genre de
détail qui fait basculer la perception du simulateur.

Point d'entrée : classe `Interceptor` dans `sim/engine.py`, qui ne porte
qu'un `eta` — lui donner une position et une loi de guidage.

---

## 8. Liaison de données tactique

**Le vrai sujet d'un CMS moderne, et le plus gros morceau.**

Deux instances partageant une image tactique : numéros de piste distants,
corrélation entre piste locale et piste reçue, report de piste. C'est ce qui
distingue un système de combat d'un radar avec un bel écran.

À ne tenter qu'une fois les tests en place — c'est le genre de fonction qui
casse tout le reste en silence.

---

## 9. Le volet sécurité

**Chantier parallèle, indépendant du reste.**

Modbus n'a ni authentification ni chiffrement. Le lab est donc un banc d'essai
naturel : Wireshark et Suricata sur le segment, des règles de détection maison
(écriture de bobine depuis une source non autorisée, rafale de function code
5), et le poste instructeur qui joue l'attaquant. On voit l'effet côté IHM
*et* côté détection, en même temps.

Réseau isolé impératif — c'est déjà le cas dans le compose, le port 502 n'est
jamais publié.

---

## Rappels d'exploitation

```bash
python3 services/server.py                      # tester, sans rien installer
python3 -m unittest discover -s tests           # le garde-fou

# Trafic réel
AIS_SOURCE=fichier python3 services/server.py                  # sans réseau
CMS_SCENARIO=04-veille-trafic-reel.toml AIS_SOURCE=digitraffic python3 services/server.py
python3 services/ais.py --capture fixtures/ais-golfe-finlande.json   # ou touche F2
python3 services/ais.py --fichier fixtures/ais-golfe-finlande.json   # inspecter
CMS_SCENARIO=05-identite-douteuse.toml python3 services/server.py    # les doutes
docker compose up --build                       # la stack conteneurisée
python3 tools/montecarlo.py scenarios/X.toml -n 24
python3 tools/record.py scenarios/X.toml -o fixtures/X.jsonl --hz 1 --slim
node tools/build-artifact.mjs                   # l'artefact autonome

# Refaire le trait de côte pour une autre zone d'opérations
curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson
curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson
python3 tools/coastline.py ne_10m_coastline.geojson --terres ne_10m_land.geojson \
    --lat 59.85 --lon 24.85 --rayon 120 -o web/coastline.json
python3 tools/eaux.py                           # origines, contacts et leurs routes
python3 tools/eaux.py --capture fixtures/ais-golfe-finlande.json
```

Le build Docker n'a jamais été exécuté — pas de démon disponible au moment de
l'écriture. Le Dockerfile ne fait que copier des fichiers dans
`python:3.11-slim` sans installation, mais c'est à vérifier au premier
lancement.

**Confirmé en pratique** : `plc/Dockerfile`, qui construit OpenPLC depuis les
sources, plantait au démarrage avec `ModuleNotFoundError: No module named
'serial'`. Cause probable : sur Debian bookworm, `pip3 install` sans
`--break-system-packages` échoue (PEP 668, environnement « externally
managed ») ; `install.sh` d'OpenPLC_v3 installe ses dépendances Python par
pip sans cette option et sans `set -e`, donc l'échec passe inaperçu au build
et n'apparaît qu'au lancement du serveur web. Corrigé en installant
`python3-serial` par `apt` (contourne pip entièrement pour ce module) et en
rejouant `pip3 install --break-system-packages -r requirements.txt` en
filet de sécurité pour le reste. Confirmé fonctionnel côté build par
quelqu'un ayant un démon Docker : l'image se construit.

**Deuxième plantage rencontré au lancement, résolu** :
`ImportError: cannot import name 'Markup' from 'jinja2'`. Même famille de
cause que le premier — `requirements.txt` d'OpenPLC_v3 ne plafonne pas
Jinja2, donc pip installe la dernière version (3.1.6 au moment de
l'écriture) ; le Flask ancien embarqué par ce projet fait encore
`from jinja2 import Markup, escape`, un ré-export retiré de Jinja2 en
3.1.0 (mars 2023). Vérifié dans un environnement isolé ici (pas dans
l'image OpenPLC elle-même, faute de démon Docker) : `jinja2==3.0.3` importe
sans erreur, `3.1.6` échoue à l'identique de la trace observée. Corrigé en
forçant `pip3 install "jinja2<3.1"` après le reste des installations, pour
que la dernière version *compatible* l'emporte sur la dernière tout court.
**Le correctif lui-même reste à confirmer par une reconstruction réelle**
(`docker compose --profile plc build --no-cache openplc`) — la vérification
n'a porté que sur la compatibilité Flask/Jinja2 en dehors de l'image.
