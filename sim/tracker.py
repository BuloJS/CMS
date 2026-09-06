"""Formation de pistes : association, filtrage, initiation, fusion.

C'est cette couche qui distingue un CMS d'un jeu vidéo. L'IHM n'affiche
jamais la vérité terrain : elle affiche le produit du pistage, avec son
incertitude, sa qualité et ses pertes.

Filtre de Kalman à vitesse constante, état [x, vx, y, vy]. Écrit à la main
en 4x4 : la matrice d'observation ne retient que la position, donc la
covariance d'innovation est 2x2 et s'inverse en une ligne.
"""
import math

from .geo import NM, bearing, rng

# Bruit de manœuvre, m²/s³. Une seule valeur ne peut pas servir à la fois
# un missile et un caboteur : voir `Track._adapte_q`. La borne haute est
# la valeur historique, celle sur laquelle les scénarios antinavires ont
# été réglés — les pistes rapides ne changent donc pas de comportement.
Q_MAX = 3.0
Q_MIN = 0.02
V_AGILE = 150.0          # m/s à partir de quoi on suppose un mobile agile

TRACK_INIT_HITS = 3      # M plots...
TRACK_INIT_SCANS = 5     # ...sur N tours d'antenne
TRACK_DROP_MISSES = 4    # tours sans plot avant suppression


def _zeros(n, m):
    return [[0.0] * m for _ in range(n)]


def _matmul(a, b):
    n, k, m = len(a), len(b), len(b[0])
    out = _zeros(n, m)
    for i in range(n):
        ai = a[i]
        for j in range(m):
            out[i][j] = sum(ai[t] * b[t][j] for t in range(k))
    return out


def _T(a):
    return [list(r) for r in zip(*a)]


def _add(a, b):
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


class Track:
    _seq = 0

    def __init__(self, x, y, t):
        Track._seq += 1
        self.num = "T%03d" % Track._seq
        self.s = [x, 0.0, y, 0.0]
        # Vitesse inconnue à l'initiation. La covariance doit couvrir tout
        # le domaine plausible — d'un chalutier à 6 m/s à un missile à
        # 300 m/s — sans quoi la piste prédit qu'elle n'a pas bougé avec une
        # confiance imméritée, et la fenêtre d'association rejette le plot
        # suivant. Sigma initial 350 m/s.
        self.P = [[400.0, 0, 0, 0], [0, 122500.0, 0, 0],
                  [0, 0, 400.0, 0], [0, 0, 0, 122500.0]]
        self.q = Q_MAX                  # bruit de manœuvre, m²/s³
        self.hits = 1
        self.misses = 0
        self.scans = 1
        self.confirmed = False
        self.born = t
        self.updated = t
        self.sources = {"RADAR"}
        self.emitter = ""               # dernier émetteur corrélé
        self.iff = ""
        self.ident = ""                 # nom AIS
        self.ais = {}                   # message AIS corrélé, tel que reçu
        self.ais_vu = 0.0               # dernière réception AIS, s
        self.anomalies = []             # doutes de vraisemblance en cours
        self.aff = "unknown"            # affiliation retenue par l'opérateur
        self.classified_by = ""
        self.history = []

    def _adapte_q(self):
        """Ajuste le bruit de manœuvre à la cinématique observée.

        Une seule valeur ne peut pas convenir à la fois à un missile et à un
        cargo. À `q` = 3, la variance de vitesse ajoutée en un tour
        d'antenne de quatre secondes vaut 12 m²/s², soit près de sept nœuds
        d'écart-type : le filtre s'autorise à croire qu'un porte-conteneurs
        change de vitesse de sept nœuds toutes les quatre secondes. Il suit
        alors le bruit de mesure au lieu de le moyenner, et rend une vitesse
        fausse de moitié sur une position pourtant juste.

        Le réglage se lit dans le monde physique : ce qu'un mobile peut
        changer à son vecteur vitesse est à peu près proportionnel à ce
        vecteur. Un missile à 270 m/s encaisse plusieurs g, un cargo à
        7 m/s pratiquement rien. On indexe donc `q` sur la vitesse estimée,
        borné aux deux bouts — la borne haute est l'ancienne valeur, donc
        les pistes rapides se comportent exactement comme avant.

        Contrepartie assumée : une vedette lente qui accélère brutalement
        sera suivie avec un tour de retard, le temps que sa vitesse estimée
        monte et desserre le filtre.
        """
        v = math.hypot(self.s[1], self.s[3])
        self.q = min(Q_MAX, max(Q_MIN, Q_MAX * (v / V_AGILE) ** 2))

    # -- prédiction ------------------------------------------------------
    def predict(self, dt):
        F = [[1, dt, 0, 0], [0, 1, 0, 0], [0, 0, 1, dt], [0, 0, 0, 1]]
        self.s = [sum(F[i][j] * self.s[j] for j in range(4)) for i in range(4)]
        self._adapte_q()
        q, d2, d3 = self.q, dt * dt, dt * dt * dt
        Q = [[q * d3 / 3, q * d2 / 2, 0, 0], [q * d2 / 2, q * dt, 0, 0],
             [0, 0, q * d3 / 3, q * d2 / 2], [0, 0, q * d2 / 2, q * dt]]
        self.P = _add(_matmul(_matmul(F, self.P), _T(F)), Q)

    # -- mise à jour -----------------------------------------------------
    def gate(self, zx, zy, R):
        """Distance de Mahalanobis² du plot à la piste prédite."""
        S = [[self.P[0][0] + R[0][0], self.P[0][2] + R[0][1]],
             [self.P[2][0] + R[1][0], self.P[2][2] + R[1][1]]]
        det = S[0][0] * S[1][1] - S[0][1] * S[1][0]
        if abs(det) < 1e-9:
            return 1e9, None
        Si = [[S[1][1] / det, -S[0][1] / det], [-S[1][0] / det, S[0][0] / det]]
        dx, dy = zx - self.s[0], zy - self.s[2]
        d2 = (dx * (Si[0][0] * dx + Si[0][1] * dy)
              + dy * (Si[1][0] * dx + Si[1][1] * dy))
        return d2, Si

    def update(self, zx, zy, R, t, Si):
        PHt = [[self.P[i][0], self.P[i][2]] for i in range(4)]     # 4x2
        K = _matmul(PHt, Si)                                       # 4x2
        dx, dy = zx - self.s[0], zy - self.s[2]
        self.s = [self.s[i] + K[i][0] * dx + K[i][1] * dy for i in range(4)]
        # P = (I - K H) P, H ne retenant que les lignes 0 et 2
        KH = _zeros(4, 4)
        for i in range(4):
            KH[i][0], KH[i][2] = K[i][0], K[i][1]
        IKH = [[(1.0 if i == j else 0.0) - KH[i][j] for j in range(4)] for i in range(4)]
        self.P = _matmul(IKH, self.P)
        self.hits += 1
        self.misses = 0
        self.updated = t
        if not self.confirmed and self.hits >= TRACK_INIT_HITS:
            self.confirmed = True

    # -- lecture ---------------------------------------------------------
    @property
    def pos(self):
        return self.s[0], self.s[2]

    @property
    def vel(self):
        return self.s[1], self.s[3]

    @property
    def course(self):
        return bearing(self.s[1], self.s[3])

    @property
    def speed(self):
        return math.hypot(self.s[1], self.s[3])

    @property
    def quality(self):
        """0..1 à partir de la trace de la covariance de position.
        Une piste qu'on ne voit plus se dégrade toute seule."""
        sig = math.sqrt(max(self.P[0][0] + self.P[2][2], 1.0))
        return max(0.0, min(1.0, 1.0 - (sig - 30.0) / 900.0))

    @property
    def vel_sigma(self):
        """Incertitude sur le vecteur vitesse, m/s.

        Distincte de `quality`, qui ne parle que de la position. Une piste
        peut être parfaitement localisée et sa vitesse encore inconnue —
        c'est même l'état normal des premiers tours d'antenne, et confondre
        les deux fait comparer une vitesse déclarée à une estimation qui
        n'existe pas encore.
        """
        return math.sqrt(max(self.P[1][1] + self.P[3][3], 0.0))

    def ellipse(self):
        """Demi-axes 1 sigma en x et y, pour l'affichage de l'incertitude."""
        return math.sqrt(max(self.P[0][0], 0.0)), math.sqrt(max(self.P[2][2], 0.0))


class Tracker:
    def __init__(self, gate_chi2=16.0):
        self.tracks = {}
        self.gate = gate_chi2          # ~4 sigma en 2D

    def polar_to_cart_cov(self, r, brg, sr, sb_deg):
        """Rotation de la covariance polaire dans le repère cartésien.

        L'erreur transverse vaut r*sigma_theta : elle grandit avec la
        distance. C'est ce qui fait « frétiller » les pistes lointaines,
        et c'est physique, pas cosmétique.
        """
        sb = math.radians(sb_deg) * r
        a = math.radians(brg)
        c, s = math.cos(a), math.sin(a)
        # axe radial = (sin a, cos a), axe transverse = (cos a, -sin a)
        vxx = (sr * s) ** 2 + (sb * c) ** 2
        vyy = (sr * c) ** 2 + (sb * s) ** 2
        vxy = sr * sr * s * c - sb * sb * c * s
        return [[vxx, vxy], [vxy, vyy]]

    def step(self, dt, own, plots, t, scan_done):
        for tr in self.tracks.values():
            tr.predict(dt)

        # Association au plus proche voisin sous fenêtre de Mahalanobis.
        for (r, brg, sr, sb) in plots:
            from .geo import to_xy
            dx, dy = to_xy(brg, r)
            zx, zy = own.x + dx, own.y + dy
            R = self.polar_to_cart_cov(r, brg, sr, sb)
            best, bd, bSi = None, self.gate, None
            for tr in self.tracks.values():
                d2, Si = tr.gate(zx, zy, R)
                if Si is not None and d2 < bd:
                    best, bd, bSi = tr, d2, Si
            if best:
                best.update(zx, zy, R, t, bSi)
            else:
                tr = Track(zx, zy, t)
                self.tracks[tr.num] = tr

        if scan_done:
            for tr in list(self.tracks.values()):
                tr.scans += 1
                if tr.updated < t - 0.1:
                    tr.misses += 1
                if tr.misses >= TRACK_DROP_MISSES:
                    del self.tracks[tr.num]
                elif not tr.confirmed and tr.scans > TRACK_INIT_SCANS:
                    del self.tracks[tr.num]
                else:
                    x, y = tr.pos
                    tr.history.append((x, y))
                    if len(tr.history) > 40:
                        tr.history.pop(0)

    def fuse_bearing(self, own, brg, label, kind="ESM", tol=6.0):
        """Fusionne une détection en gisement seul (ESM) avec la piste dont
        le gisement colle. C'est la fonction de fusion multi-senseurs dans
        sa forme la plus simple, et c'est déjà très démonstratif."""
        best, bd = None, tol
        for tr in self.tracks.values():
            x, y = tr.pos
            d = abs((bearing(x - own.x, y - own.y) - brg + 540) % 360 - 180)
            if d < bd:
                best, bd = tr, d
        if best:
            best.emitter = label
            best.sources.add(kind)
        return best

    def confirmed(self):
        return [t for t in self.tracks.values() if t.confirmed]
