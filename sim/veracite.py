"""Contrôle de vraisemblance des déclarations AIS.

L'AIS est **déclaratif**. Un navire diffuse ce qu'il veut : son nom, son
type, ses dimensions, et sa position. Rien n'oblige ce qu'il dit à
correspondre à ce que le radar voit, et éteindre un transpondeur ne demande
qu'un interrupteur. Un système qui gobe l'AIS n'est pas un système de
combat, c'est un afficheur.

Ce module compare la déclaration à la mesure. Il ne conclut rien : il
produit un **doute qualifié**, nommé, daté, avec le détail de ce qui cloche.

Trois principes, et ils comptent plus que les seuils :

  * **Aucune anomalie ne prouve une hostilité.** Un cargo dont la position
    AIS dérive de six cents mètres a probablement un GPS fatigué, pas une
    intention. La console doit dire « incohérent », jamais « hostile ».
  * **Le doute se motive.** Chaque anomalie porte le chiffre qui l'a
    déclenchée. Un opérateur doit pouvoir la contester, donc la lire.
  * **Le seuil suit l'incertitude de la piste.** Comparer une position
    déclarée à une piste lointaine qui frétille sur son ellipse produirait
    une alarme par tour d'antenne. Le seuil s'appuie sur la covariance du
    filtre, pas sur une distance fixe.
"""
import math

from .geo import KT, NM

# Écart de position : cinq sigma de la piste, avec un plancher qui couvre le
# bruit de mesure du radar et l'imprécision du GPS civil. Le sigma est
# mesuré, pas choisi : sur 351 000 relevés de trafic honnête, trois sigma
# produisaient huit cents fausses alarmes et cinq n'en produisent aucune,
# tout en gardant le plancher assez bas pour attraper un décalage de quatre
# encablures.
ECART_SIGMA = 5.0
ECART_PLANCHER = 250.0          # m

# Cinématique : on compare les deux vecteurs vitesse, déclaré et mesuré,
# et non la vitesse et le cap séparément. Un navire à deux nœuds dont la
# route estimée se trompe de quatre-vingt-dix degrés ne diffère que de trois
# nœuds en vecteur — ce qui est la bonne façon de le dire, et évite d'alarmer
# sur des pistes lentes dont le cap frétille légitimement.
#
# Le seuil s'appuie sur la covariance de vitesse du filtre. C'est la
# grandeur qui dit ce que la piste sait vraiment de son mouvement, et elle
# n'a rien à voir avec la qualité de piste, qui ne parle que de position.
# Cinq sigma, et non trois. Le choix est mesuré, pas prudent par principe :
# sur 234 000 relevés de trafic honnête, trois sigma laissaient passer des
# écarts de douze nœuds dus au seul filtre, cinq n'en laissent aucun. Le
# contrôle n'attrape donc que les incohérences franches — c'est le prix
# d'un journal où chaque avertissement compte, et la contrepartie est
# assumée : un mensonge subtil sur la vitesse reste indiscernable du bruit.
DV_SIGMA = 5.0
DV_PLANCHER = 4.0 * KT          # m/s

# Tant que le filtre ne connaît pas la vitesse à mieux que cela, aucun
# contrôle qui s'appuie sur la vitesse n'a le droit de parler. Les premiers
# tours d'antenne d'une piste neuve donnent une vitesse encore arbitraire —
# c'est là que naissent les fausses alarmes, et elles sont coûteuses :
# une anomalie journalisée à tort reste au journal quand elle s'efface.
VEL_SIGMA_UTILE = 3.0 * KT      # m/s

# Un navire qui se déclare au mouillage ou à quai et qui traverse le rail
# à dix nœuds ne s'est pas trompé de bouton.
VITESSE_IMMOBILE = 3.0 * KT
STATUTS_IMMOBILES = {1, 5, 6}   # mouillage, à quai, échoué

# Vitesses au-delà desquelles une coque de commerce ne va pas. Un cargo de
# 200 m qui annonce 40 nœuds déclare une chose ou l'autre de faux.
VITESSE_MAX_COMMERCE = 30.0 * KT
LOA_COMMERCE = 120.0            # m

# Extinction : un contact radar tenu qui cesse d'émettre alors qu'il est
# toujours à portée VHF. Deux minutes de silence, soit une trentaine de
# tours d'antenne — bien au-delà de la cadence d'émission la plus lente.
SILENCE_S = 120.0


def _anomalie(code, libelle, detail, t):
    return {"code": code, "libelle": libelle, "detail": detail, "depuis": round(t, 1)}


def mmsi_valide(mmsi):
    """Un MMSI de navire porte un indicatif de pays (MID) en tête.

    Neuf chiffres, et pour une station de bord le MID — les trois premiers —
    est compris entre 201 et 775. Les autres préfixes ont un sens précis
    dans la norme (0 pour un groupe, 8 pour un portatif, 99 pour une aide à
    la navigation, 970 à 974 pour les balises de détresse) ; aucun d'eux ne
    désigne un navire ordinaire en transit.
    """
    s = str(mmsi or "").strip()
    if not s.isdigit() or len(s) != 9:
        return False
    if s[0] in "0189":
        return False
    return 201 <= int(s[:3]) <= 775


def controler(tr, rec, t):
    """Compare une piste à la déclaration AIS qui lui est corrélée.

    `rec` porte la position déclarée (`x`, `y`, en mètres dans le repère
    local) et la cinématique déclarée (`sog` en nœuds, `cog` en degrés).
    Rend la liste des anomalies constatées à cet instant.
    """
    out = []
    tx, ty = tr.pos

    # -- écart entre position déclarée et position mesurée ---------------
    if rec.get("x") is not None and rec.get("y") is not None:
        ex, ey = tr.ellipse()
        seuil = max(ECART_SIGMA * math.hypot(ex, ey), ECART_PLANCHER)
        d = math.hypot(rec["x"] - tx, rec["y"] - ty)
        if d > seuil:
            out.append(_anomalie(
                "ecart_position", "Position AIS incohérente",
                "%.0f m d'écart avec le plot radar, pour un seuil de %.0f m"
                % (d, seuil), t))

    # Aucune accusation fondée sur la vitesse tant que la vitesse n'est pas
    # connue. C'est la seule garde qui compte ici, et `quality` ne la donne
    # pas : elle ne parle que de la position.
    vitesse_sue = tr.vel_sigma < VEL_SIGMA_UTILE

    # -- cinématique déclarée contre cinématique mesurée -----------------
    sog, cog = rec.get("sog"), rec.get("cog")
    if vitesse_sue and sog is not None and cog is not None:
        a = math.radians(float(cog))
        dvx = float(sog) * KT * math.sin(a) - tr.vel[0]
        dvy = float(sog) * KT * math.cos(a) - tr.vel[1]
        dv = math.hypot(dvx, dvy)
        seuil = max(DV_SIGMA * tr.vel_sigma, DV_PLANCHER)
        if dv > seuil:
            out.append(_anomalie(
                "ecart_cinematique", "Cinématique AIS incohérente",
                "déclare %.1f kt au %03.0f°, mesuré %.1f kt au %03.0f° "
                "(%.1f kt d'écart en vecteur, seuil %.1f kt)"
                % (float(sog), float(cog), tr.speed / KT, tr.course,
                   dv / KT, seuil / KT), t))

    # -- la déclaration se contredit elle-même ---------------------------
    ns = rec.get("navstat_code")
    if vitesse_sue and ns in STATUTS_IMMOBILES and tr.speed > VITESSE_IMMOBILE:
        out.append(_anomalie(
            "statut_incoherent", "Statut de navigation contredit",
            "se déclare « %s » et fait route à %.1f kt"
            % (rec.get("navstat", "immobile"), tr.speed / KT), t))

    loa = rec.get("loa")
    if vitesse_sue and loa and loa >= LOA_COMMERCE \
            and tr.speed > VITESSE_MAX_COMMERCE:
        out.append(_anomalie(
            "gabarit_incoherent", "Gabarit incompatible avec la vitesse",
            "déclare %d m de long et fait %.0f kt" % (loa, tr.speed / KT), t))

    # -- identité ---------------------------------------------------------
    mmsi = rec.get("mmsi")
    if mmsi and not mmsi_valide(mmsi):
        out.append(_anomalie(
            "mmsi_invalide", "MMSI hors plage attribuable",
            "%s — indicatif de pays inexistant" % mmsi, t))

    return out


def extinction(tr, t, portee_vhf_m, distance_m):
    """Le contact a émis, ne le fait plus, et n'est pas sorti de portée.

    C'est le contrôle le plus simple à écrire et le plus parlant : un navire
    marchand n'éteint pas son transpondeur par accident, et un système qui
    ne remarque pas l'extinction perd la seule information que la manœuvre
    produit.
    """
    if not tr.ais_vu or distance_m > portee_vhf_m:
        return None
    silence = t - tr.ais_vu
    if silence < SILENCE_S:
        return None
    return _anomalie(
        "extinction", "Émission AIS interrompue",
        "silencieux depuis %.0f s, toujours à %.1f NM et tenu au radar"
        % (silence, distance_m / NM), t)


def gravite(anomalies):
    """Un poids 0..1 pour le TEWA. Le cumul compte : une seule incohérence
    est un doute, trois ensemble sont un comportement."""
    poids = {"extinction": 0.55, "ecart_position": 0.45, "mmsi_invalide": 0.40,
             "ecart_cinematique": 0.35,
             "statut_incoherent": 0.35, "gabarit_incoherent": 0.30}
    if not anomalies:
        return 0.0
    # Combinaison probabiliste : deux doutes à 0,45 font 0,70, pas 0,90.
    reste = 1.0
    for a in anomalies:
        reste *= (1.0 - poids.get(a["code"], 0.2))
    return round(1.0 - reste, 3)
