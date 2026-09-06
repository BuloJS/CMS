"""Décodage des champs AIS normalisés (UIT-R M.1371).

L'AIS ne transmet pas du texte, il transmet des codes. `type: 35` et
`navStat: 3` ne veulent rien dire à l'écran ; « bâtiment militaire » et
« capacité de manœuvre restreinte » veulent dire quelque chose à un
opérateur. La traduction est ici, une fois, et le reste du système ne
manipule que des libellés.

Deux remarques de domaine, parce qu'elles décident de l'affichage :

  * Certains statuts et certains types ne sont pas des informations
    neutres. « Non maître de sa manœuvre », « capacité restreinte »,
    « contrainte par son tirant d'eau » disent qu'un navire ne pourra
    *pas* s'écarter — c'est une contrainte de manœuvre pour le porteur,
    pas une étiquette. « Bâtiment militaire », « police », « SAR » et une
    balise de détresse active relèvent de la même logique.

  * Tout cela est **déclaratif**. Un navire dit ce qu'il veut. L'AIS ne
    prouve rien, il propose une identité — que le radar est libre de
    contredire. C'est pour cette raison que la console marque la source.
"""

# Types de navire. Les décennies 20/40/60/70/80/90 sont des familles dont
# le second chiffre code la catégorie de marchandise dangereuse ; on ne
# retient que la famille, la catégorie n'intéresse pas un CMS.
_FAMILIES = {
    2: "Aéroglisseur",
    4: "Navire à grande vitesse",
    6: "Navire à passagers",
    7: "Cargo",
    8: "Pétrolier / chimiquier",
    9: "Autre",
}

_SPECIFIC = {
    30: "Pêche",
    31: "Remorquage",
    32: "Remorquage lourd",
    33: "Drague / travaux sous-marins",
    34: "Plongée",
    35: "Bâtiment militaire",
    36: "Voilier",
    37: "Navire de plaisance",
    50: "Pilote",
    51: "Recherche et sauvetage",
    52: "Remorqueur",
    53: "Servitude portuaire",
    54: "Lutte antipollution",
    55: "Police / autorité",
    58: "Transport sanitaire",
    59: "Navire non combattant",
}

# Types qui ne sont pas du trafic marchand ordinaire : ils changent la
# lecture qu'un opérateur fait du contact, donc la console les marque.
NOTABLE_TYPES = {35, 51, 55, 58, 59}

_NAV_STATUS = {
    0: "En route au moteur",
    1: "Au mouillage",
    2: "Non maître de sa manœuvre",
    3: "Capacité de manœuvre restreinte",
    4: "Contraint par son tirant d'eau",
    5: "À quai",
    6: "Échoué",
    7: "En pêche",
    8: "En route à la voile",
    9: "Réservé (NGV)",
    10: "Réservé (aéroglisseur)",
    11: "Remorquage arrière",
    12: "Poussage / remorquage à couple",
    13: "Réservé",
    14: "Balise de détresse active",
    15: "Non défini",
}

# Statuts qui disent « ce navire ne pourra pas s'écarter » — ou qui
# appellent une réaction. Ce ne sont pas des étiquettes, ce sont des
# contraintes de manœuvre, et à ce titre elles remontent à l'écran.
CONSTRAINED_STATUS = {2, 3, 4, 6, 7, 11, 12, 14}


def ship_type_label(code):
    """Code UIT -> libellé lisible. Rend "" si le code n'est pas exploitable."""
    if code is None:
        return ""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ""
    if code in _SPECIFIC:
        return _SPECIFIC[code]
    fam = _FAMILIES.get(code // 10)
    return fam or ""


def nav_status_label(code):
    """Code de statut de navigation -> libellé. "" si non défini."""
    if code is None:
        return ""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ""
    if code == 15:
        return ""
    return _NAV_STATUS.get(code, "")


def dimensions(ref_a, ref_b, ref_c, ref_d):
    """Dimensions hors tout à partir des quatre distances AIS.

    L'AIS ne diffuse pas une longueur : il diffuse la position de l'antenne
    dans le navire — A vers l'avant, B vers l'arrière, C bâbord, D tribord.
    La longueur est la somme, et c'est aussi ce qui permet de savoir de quel
    côté du navire se trouve le point rapporté.
    """
    def n(v):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0
    a, b, c, d = n(ref_a), n(ref_b), n(ref_c), n(ref_d)
    return {"loa": a + b, "beam": c + d} if (a + b) or (c + d) else {}


def decode(msg):
    """Message AIS brut (champs Digitraffic / UIT) -> dictionnaire d'affichage.

    Tolérant par construction : un message AIS réel est souvent incomplet,
    et l'absence d'un champ n'est pas une erreur. Ce qui manque est absent
    du résultat, jamais rempli d'une valeur par défaut trompeuse — un
    tirant d'eau inventé serait pire qu'un tirant d'eau inconnu.
    """
    out = {}
    if msg.get("name"):
        out["name"] = str(msg["name"]).strip()
    if msg.get("mmsi"):
        out["mmsi"] = str(msg["mmsi"])
    if msg.get("callSign"):
        out["callsign"] = str(msg["callSign"]).strip()
    if msg.get("imo"):
        out["imo"] = str(msg["imo"])
    if msg.get("destination"):
        out["dest"] = str(msg["destination"]).strip()

    t = msg.get("type")
    if ship_type_label(t):
        out["type"] = ship_type_label(t)
        out["type_code"] = int(t)
        out["notable"] = int(t) in NOTABLE_TYPES

    ns = msg.get("navStat")
    if nav_status_label(ns):
        out["navstat"] = nav_status_label(ns)
        out["navstat_code"] = int(ns)
        out["contraint"] = int(ns) in CONSTRAINED_STATUS

    out.update(dimensions(msg.get("refA"), msg.get("refB"),
                          msg.get("refC"), msg.get("refD")))

    # Le tirant d'eau AIS est en décimètres. Le lire en mètres donne un
    # porte-conteneurs à 68 m de tirant d'eau, ce qui ne surprend personne
    # tant qu'on ne le regarde pas.
    if msg.get("draught"):
        try:
            out["draught"] = round(int(msg["draught"]) / 10.0, 1)
        except (TypeError, ValueError):
            pass
    return out


# --------------------------------------------------------------------- #
# Surface équivalente radar estimée
# --------------------------------------------------------------------- #

# L'AIS diffuse des dimensions, jamais une surface équivalente radar. Il
# faut donc en fabriquer une, et le choix de la loi mérite d'être expliqué.
#
# La référence physique usuelle est la formule empirique de Skolnik,
# sigma ≈ 52 √f · D^1,5 (f en MHz, D en kilotonnes de déplacement). En bande
# X elle donne près d'un million de m² pour un cargo de 180 m — une valeur
# de travers, connue pour majorer, et deux ordres de grandeur au-dessus de
# l'échelle que ce simulateur utilise depuis le début.
#
# On calibre donc sur les valeurs écrites à la main dans les scénarios,
# parce que c'est la cohérence interne qui compte ici : un navire réel doit
# être exactement aussi détectable qu'un navire inventé de même taille,
# sinon le trafic réel et les scénarios ne se comportent pas pareil sur le
# même scope. La loi ci-dessous passe par deux de ces points :
#
#     embarcation rapide  25 m -> 38 m²    (scénario : 40)
#     cargo              180 m -> 8 990 m² (scénario : 9 000)
#
# Elle donne 21 000 m² pour un pétrolier de 245 m là où le scénario écrit
# 12 000. L'écart est sans conséquence : au-delà d'un millier de m², la
# détection est limitée par l'horizon radio, pas par le bilan de liaison.
# C'est en bas de l'échelle que la précision compte, et c'est là que la loi
# est ancrée.
RCS_K = 0.0051
RCS_EXP = 2.77

# Coques en composite, faible franc-bord : un voilier ou un bateau de
# plaisance rend nettement moins qu'un navire de travail de même longueur.
_RCS_FACTOR = {36: 0.35, 37: 0.35}


def rcs_from_length(loa, type_code=None):
    """Longueur hors tout (m) -> surface équivalente radar estimée (m²).

    Rend None si la longueur est inexploitable : mieux vaut laisser
    l'appelant choisir sa valeur par défaut que d'en inventer une ici.
    """
    try:
        loa = float(loa)
    except (TypeError, ValueError):
        return None
    if loa <= 0:
        return None
    # Au-delà de 400 m on est hors du domaine d'étalonnage (le plus grand
    # navire en service en fait 400). On borne plutôt que d'extrapoler.
    loa = min(loa, 400.0)
    sigma = RCS_K * loa ** RCS_EXP
    try:
        sigma *= _RCS_FACTOR.get(int(type_code), 1.0)
    except (TypeError, ValueError):
        pass
    return round(max(sigma, 0.5), 1)
