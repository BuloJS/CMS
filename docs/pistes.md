# Pistes pour la suite

Classées par rapport entre ce que ça apporte et ce que ça coûte. Chaque
entrée dit par où attaquer dans le code — c'est ce qui manque le plus quand
on rouvre un projet trois semaines plus tard.

---

## 1. Des données réelles — ADS-B

**Une soirée. Le meilleur rapport plaisir/effort du lot.**

Un CMS avec des pistes inventées se démode en dix minutes. Un CMS qui affiche
les avions réels au-dessus de chez toi, non.

Le format de piste est déjà le bon : `Contact` porte position, route, vitesse,
altitude et surface équivalente radar. Il n'y a rien à changer au cœur.

- **Avec une clé RTL-SDR (~25 €)** : `dump1090-fa` expose un `aircraft.json`
  rafraîchi à la seconde sur `http://localhost:8080/data/aircraft.json`.
- **Sans matériel** : l'API OpenSky Network, en HTTP, avec quelques secondes
  de retard.

**Par où commencer** — un `services/adsb.py` qui lit le JSON et fabrique des
`Contact`. Il manque une conversion géographique : ajouter à `sim/geo.py` une
projection tangente locale (`lat/lon → x/y` en mètres autour d'un point de
référence). Une quinzaine de lignes, formule de la projection équirectangulaire
suffisante sur quelques dizaines de milles.

Ensuite les senseurs s'appliquent tels quels : un avion de ligne à 33 000 pieds
sort à 235 NM d'horizon, un drone à 400 pieds à 30 NM. **La physique déjà
écrite devient un filtre sur du trafic réel** — et c'est là que ça devient
vraiment intéressant.

Garder les pistes simulées injectables par-dessus : le front ne fait pas la
différence, `src` distingue déjà l'origine.

---

## 2. Des tests automatisés

**Deux heures. C'est le vrai trou du projet.**

Il n'y en a aucun aujourd'hui. Or la physique se casse en silence : une
constante mal placée dans l'équation radar ne lève pas d'exception, elle
change juste toutes les portées.

`unittest` de la bibliothèque standard suffit — pas de dépendance à ajouter.
Ce qui mérite un test, par ordre :

- `sim/geo.py` — `cpa()` sur des géométries connues (route de collision,
  contact qui s'ouvre, vitesse relative nulle), `intercept_time()` quand la
  cible fuit plus vite que l'intercepteur, le passage de 359° à 001°.
- `sim/sensors.py` — l'horizon rend bien 17,2 NM pour 30 m et 5 m ; le SNR
  décroît de 12 dB quand la distance double.
- `sim/tracker.py` — convergence sur une trajectoire connue. Je l'ai fait à la
  main pendant le développement (301 m/s estimés pour 300 réels) ; ce contrôle
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
docker compose up --build                       # la stack conteneurisée
python3 tools/montecarlo.py scenarios/X.toml -n 24
python3 tools/record.py scenarios/X.toml -o fixtures/X.jsonl --hz 1 --slim
node tools/build-artifact.mjs                   # l'artefact autonome
```

Le build Docker n'a jamais été exécuté — pas de démon disponible au moment de
l'écriture. Le Dockerfile ne fait que copier des fichiers dans
`python:3.11-slim` sans installation, mais c'est à vérifier au premier
lancement.
