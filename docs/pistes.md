# Pistes pour la suite

Classées par rapport entre ce que ça apporte et ce que ça coûte. Chaque
entrée dit par où attaquer dans le code — c'est ce qui manque le plus quand
on rouvre un projet trois semaines plus tard.

---

## Déjà fait

- **Projection géographique** (`sim/geo.Projection`) et ancrage des scénarios
  par un bloc `[origine]`. Les scénarios sans ancrage restent relatifs.
- **Décodage AIS normalisé** (`sim/ais.py`) : types de navire, statuts de
  navigation, dimensions, tirant d'eau. Porté jusqu'à la console.
- **Console adaptée aux données réelles** : échelle 100 NM, position
  géographique du curseur, bloc AIS dans le panneau de piste, marquage
  explicite des contacts de surface qui n'émettent pas.
- **Trait de côte réel**, Natural Earth 10 m découpé par `tools/coastline.py`.
- **Garde-fou de tests** sur la projection et les deux lois de senseur.

---

## 1. Le flux AIS réel — l'étape suivante

**Une soirée. Tout le raccord est déjà en place.**

Le format est le bon, le décodage est écrit, la console sait l'afficher. Il
manque le service qui va chercher les messages et fabrique des `Contact`.

- **Digitraffic (Fintraffic)**, sans clé ni inscription, eaux finlandaises :
  `https://meri.digitraffic.fi/api/ais/v1/locations` et `/api/ais/v1/vessels`,
  du JSON sur HTTPS — donc `urllib` suffit et la règle zéro-dépendance tient.
  Un flux MQTT existe (`wss://meri.digitraffic.fi:443/mqtt`) mais imposerait
  une bibliothèque.
- **Kystverket (Norvège)**, TCP brut sur `153.44.253.27:5631`, sans
  inscription, 40 à 60 NM des côtes, licence NLOD. Trames AIVDM : il faut
  écrire le désarmurage ASCII 6 bits et le réassemblage multi-trames, une
  centaine de lignes très agréables à écrire.

**Par où commencer** — un `services/ais.py` qui interroge Digitraffic et
fabrique des `Contact(kind="surf", ais=True, ais_static=decode(...))`. Deux
points à traiter :

- **La surface équivalente radar.** L'AIS donne les dimensions, pas la RCS.
  Le déplacement s'estime par `L × B × tirant d'eau × coefficient de bloc`,
  et la formule empirique de Skolnik (`σ ≈ 52 √f D^1,5`, f en MHz, D en
  kilotonnes) donne un ordre de grandeur — connu pour majorer, et donné au
  travers du navire. À calibrer contre les RCS des scénarios écrits à la
  main (9 000 m² pour un cargo de 180 m) plutôt qu'à croire sur parole.
- **La cadence.** Un navire au mouillage émet toutes les trois minutes, un
  navire rapide toutes les deux secondes. Le pistage attend des plots
  réguliers ; il faudra soit extrapoler entre deux messages, soit accepter
  des pistes qui se dégradent — et c'est probablement plus intéressant de
  les laisser se dégrader.

Garder les contacts simulés injectables par-dessus le trafic réel : le front
ne fait pas la différence, `src` distingue déjà l'origine. **C'est là que le
sujet devient vraiment naval** — une vedette simulée sans AIS au milieu d'un
rail marchand réel, et le problème d'identification se pose tout seul.

---

## 2. Compléter les tests

**Le socle est posé, il manque deux morceaux.**

`tests/test_geo.py` couvre la projection, l'horizon radio, la loi en R⁴, le
CPA et l'interception. Restent :

- `sim/tracker.py` — convergence sur une trajectoire connue. Fait à la main
  pendant le développement (301 m/s estimés pour 300 réels) ; ce contrôle
  devrait être un test, pas un souvenir.
- `sim/tewa.py` — `salvo_for()`, le rejet hors enveloppe, le signe de la butée.

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
docker compose up --build                       # la stack conteneurisée
python3 tools/montecarlo.py scenarios/X.toml -n 24
python3 tools/record.py scenarios/X.toml -o fixtures/X.jsonl --hz 1 --slim
node tools/build-artifact.mjs                   # l'artefact autonome

# Refaire le trait de côte pour une autre zone d'opérations
curl -O https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson
python3 tools/coastline.py ne_10m_coastline.geojson --lat 59.85 --lon 24.85 --rayon 120 -o web/coastline.json
```

Le build Docker n'a jamais été exécuté — pas de démon disponible au moment de
l'écriture. Le Dockerfile ne fait que copier des fichiers dans
`python:3.11-slim` sans installation, mais c'est à vérifier au premier
lancement.
