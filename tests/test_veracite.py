"""Contrôle de vraisemblance des déclarations AIS.

Deux exigences opposées, et c'est tout le sujet :

  * attraper les incohérences franches — position déclarée qui ne tombe pas
    sur le plot, transpondeur éteint, MMSI inexistant ;
  * ne pas en fabriquer. Une fausse alarme sur un cargo honnête coûte plus
    cher qu'une détection manquée, parce qu'elle est journalisée, qu'elle
    reste au journal quand elle s'efface, et qu'elle apprend à l'opérateur
    à ignorer l'indicateur.

Les seuils du module ont été calés sur des mesures — 351 000 relevés de
position et 234 000 de cinématique sur du trafic honnête — et non choisis
au jugé. Les tests d'intégration en fin de fichier tiennent ce calage.

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.engine import Engine                                    # noqa: E402
from sim.geo import KT, NM                                       # noqa: E402
from sim.scenario import load                                    # noqa: E402
from sim.veracite import (SILENCE_S, controler, extinction,      # noqa: E402
                          gravite, mmsi_valide)


class FausseePiste:
    """Le minimum dont `controler` a besoin : une position, une vitesse,
    une ellipse et l'incertitude de vitesse du filtre."""

    def __init__(self, x=0.0, y=0.0, vx=0.0, vy=0.0,
                 ell=(40.0, 40.0), vel_sigma=0.2, ais_vu=0.0):
        self.pos = (x, y)
        self.vel = (vx, vy)
        self.speed = (vx * vx + vy * vy) ** 0.5
        self.course = __import__("math").degrees(
            __import__("math").atan2(vx, vy)) % 360.0
        self._ell = ell
        self.vel_sigma = vel_sigma
        self.quality = 0.95
        self.ais_vu = ais_vu

    def ellipse(self):
        return self._ell


def codes(anomalies):
    return sorted(a["code"] for a in anomalies)


class TestMmsi(unittest.TestCase):

    def test_indicatifs_valides(self):
        for m in ("636092811", "230982000", "276019200", "201000000", "775999999"):
            self.assertTrue(mmsi_valide(m), m)

    def test_indicatifs_invalides(self):
        # 199 n'est attribué à personne ; 776 est au-delà de la plage.
        for m in ("199000042", "776123456", "000000001", "999999999"):
            self.assertFalse(mmsi_valide(m), m)

    def test_prefixes_reserves(self):
        """0 groupe, 8 portatif, 9 service : aucun ne désigne un navire."""
        for m in ("098123456", "812345678", "970123456", "992123456"):
            self.assertFalse(mmsi_valide(m), m)

    def test_longueur_et_forme(self):
        for m in ("", None, "12345678", "1234567890", "23098200a"):
            self.assertFalse(mmsi_valide(m))


class TestEcartPosition(unittest.TestCase):

    def test_declaration_qui_tombe_sur_le_plot(self):
        tr = FausseePiste(x=1000.0, y=2000.0)
        self.assertEqual(controler(tr, {"x": 1010.0, "y": 2005.0}, 0.0), [])

    def test_declaration_decalee(self):
        tr = FausseePiste(x=0.0, y=0.0)
        an = controler(tr, {"x": 740.0, "y": 0.0}, 12.0)
        self.assertEqual(codes(an), ["ecart_position"])
        self.assertIn("740", an[0]["detail"])
        self.assertEqual(an[0]["depuis"], 12.0)

    def test_le_seuil_suit_l_incertitude(self):
        """Une piste qui frétille sur une grande ellipse ne doit pas
        déclencher : c'est le filtre qui est incertain, pas le navire qui
        ment."""
        floue = FausseePiste(ell=(400.0, 400.0))
        self.assertEqual(controler(floue, {"x": 900.0, "y": 0.0}, 0.0), [])
        nette = FausseePiste(ell=(20.0, 20.0))
        self.assertEqual(codes(controler(nette, {"x": 900.0, "y": 0.0}, 0.0)),
                         ["ecart_position"])

    def test_sans_position_declaree(self):
        self.assertEqual(controler(FausseePiste(), {"name": "X"}, 0.0), [])


class TestCinematique(unittest.TestCase):

    def test_coherente(self):
        tr = FausseePiste(vx=0.0, vy=10.0)          # 19,4 kt au nord
        self.assertEqual(controler(tr, {"sog": 19.4, "cog": 0.0}, 0.0), [])

    def test_franchement_incoherente(self):
        tr = FausseePiste(vx=0.0, vy=10.0)
        an = controler(tr, {"sog": 3.0, "cog": 180.0}, 0.0)
        self.assertEqual(codes(an), ["ecart_cinematique"])

    def test_muet_tant_que_la_vitesse_est_inconnue(self):
        """Les premiers tours d'antenne d'une piste neuve donnent une vitesse
        arbitraire. Aucun contrôle qui s'appuie dessus n'a le droit de
        parler — et `quality` ne le dit pas, elle ne parle que de position."""
        neuve = FausseePiste(vx=0.0, vy=10.0, vel_sigma=8.0 * KT)
        self.assertEqual(controler(neuve, {"sog": 3.0, "cog": 180.0}, 0.0), [])

    def test_ecart_de_cap_a_faible_vitesse_tolere(self):
        """Deux nœuds au 090 contre deux nœuds au 000 : quatre-vingt-dix
        degrés d'écart, mais moins de trois nœuds en vecteur. C'est du bruit
        de filtre, pas une déclaration fausse."""
        tr = FausseePiste(vx=0.0, vy=2.0 * KT)
        self.assertEqual(controler(tr, {"sog": 2.0, "cog": 90.0}, 0.0), [])


class TestDeclarationContradictoire(unittest.TestCase):

    def test_au_mouillage_et_en_route(self):
        tr = FausseePiste(vx=0.0, vy=10.0 * KT)
        an = controler(tr, {"navstat_code": 1, "navstat": "Au mouillage"}, 0.0)
        self.assertIn("statut_incoherent", codes(an))

    def test_au_mouillage_et_immobile(self):
        tr = FausseePiste(vx=0.0, vy=0.3 * KT)
        self.assertEqual(controler(tr, {"navstat_code": 1}, 0.0), [])

    def test_gabarit_contre_vitesse(self):
        tr = FausseePiste(vx=0.0, vy=40.0 * KT)
        an = controler(tr, {"loa": 220}, 0.0)
        self.assertIn("gabarit_incoherent", codes(an))

    def test_petit_mobile_rapide_normal(self):
        """Une vedette de dix-huit mètres à quarante nœuds n'a rien
        d'anormal — la règle ne vise que les coques de commerce."""
        tr = FausseePiste(vx=0.0, vy=40.0 * KT)
        self.assertEqual(controler(tr, {"loa": 18}, 0.0), [])


class TestExtinction(unittest.TestCase):

    def test_silence_prolonge_a_portee(self):
        tr = FausseePiste(ais_vu=100.0)
        a = extinction(tr, 100.0 + SILENCE_S + 1, 30 * NM, 10 * NM)
        self.assertIsNotNone(a)
        self.assertEqual(a["code"], "extinction")

    def test_silence_court_tolere(self):
        """Un navire lent n'émet que toutes les trois minutes : un silence
        bref est le cas normal, pas une extinction."""
        tr = FausseePiste(ais_vu=100.0)
        self.assertIsNone(extinction(tr, 100.0 + SILENCE_S - 5, 30 * NM, 10 * NM))

    def test_hors_portee_vhf_ne_compte_pas(self):
        """Sorti de portée, un navire cesse d'être entendu sans rien avoir
        éteint. L'accuser serait une faute."""
        tr = FausseePiste(ais_vu=100.0)
        self.assertIsNone(extinction(tr, 100.0 + SILENCE_S + 1, 30 * NM, 45 * NM))

    def test_jamais_entendu_nest_pas_une_extinction(self):
        tr = FausseePiste(ais_vu=0.0)
        self.assertIsNone(extinction(tr, 900.0, 30 * NM, 5 * NM))


class TestGravite(unittest.TestCase):

    def test_aucune_anomalie(self):
        self.assertEqual(gravite([]), 0.0)

    def test_cumul_sous_additif(self):
        """Deux doutes ne font pas la somme des deux : ils se combinent en
        probabilité, sinon trois broutilles vaudraient une certitude."""
        un = gravite([{"code": "ecart_position"}])
        deux = gravite([{"code": "ecart_position"}, {"code": "mmsi_invalide"}])
        self.assertGreater(deux, un)
        self.assertLess(deux, un + gravite([{"code": "mmsi_invalide"}]))
        self.assertLessEqual(deux, 1.0)


class TestScenario(unittest.TestCase):
    """Le scénario 05 met en scène quatre cas. Il doit les produire tous et
    n'en inventer aucun."""

    @classmethod
    def setUpClass(cls):
        e = Engine(load(ROOT / "scenarios" / "05-identite-douteuse.toml"))
        e.doctrine["auto_id"] = True
        while e.t < 700:
            e.step()
        cls.eng = e
        cls.par_nom = {}
        for tr in e.tracker.confirmed():
            cls.par_nom[(tr.ais or {}).get("name", tr.num)] = tr

    def test_les_quatre_cas_sont_detectes(self):
        attendu = {"Embarcation déclarée": "mmsi_invalide",
                   "MV ARGO": "extinction",
                   "MV KAIRA": "ecart_position"}
        for nom, code in attendu.items():
            tr = self.par_nom.get(nom)
            self.assertIsNotNone(tr, "piste %s absente" % nom)
            self.assertIn(code, codes(tr.anomalies), nom)

    def test_le_contact_muet_reste_inconnu(self):
        """Il n'émet rien : il n'y a rien à contrôler, et rien ne le classe.
        C'est le bon comportement, pas une lacune."""
        muet = [tr for tr in self.eng.tracker.confirmed() if not tr.ais]
        self.assertTrue(muet)
        for tr in muet:
            self.assertEqual(tr.aff, "unknown")
            self.assertEqual(tr.anomalies, [])

    def test_le_trafic_honnete_reste_indemne(self):
        for nom in ("MV SAIMAA", "MV NEVA"):
            tr = self.par_nom.get(nom)
            self.assertIsNotNone(tr, nom)
            self.assertEqual(tr.anomalies, [], nom)
            self.assertEqual(tr.aff, "neutral", nom)

    def test_une_declaration_douteuse_ne_vaut_pas_classement(self):
        """Le cœur de l'affaire : un fraudeur ne doit pas obtenir
        gratuitement le statut de neutre que sa fraude vise."""
        tr = self.par_nom["Embarcation déclarée"]
        self.assertIn("AIS", tr.sources)
        self.assertEqual(tr.aff, "unknown")

    def test_le_doute_pese_sur_le_score_sans_le_dominer(self):
        par = {th["track"].num: th for th in self.eng.threats}
        tr = self.par_nom["Embarcation déclarée"]
        f = par[tr.num]["eval"]["facteurs"]
        self.assertGreater(f.get("veracite", 0), 0)
        self.assertLess(f["veracite"], 0.16)   # plafonné : jamais décisif


class TestPasDeFauxPositifs(unittest.TestCase):
    """Le calage des seuils tient sur du trafic entièrement honnête."""

    # Le scénario du détroit porte l'essentiel du trafic AIS, donc plusieurs
    # graines ; les deux scénarios antinavires n'en ont qu'un ou deux navires
    # coopératifs et coûtent cher à simuler — une graine suffit à couvrir le
    # chemin. Un garde-fou qu'on n'a pas envie de lancer ne sert à rien.
    CAS = [("01-detroit-approche.toml", 4),
           ("02-saturation-asm.toml", 1),
           ("03-avarie-refroidissement.toml", 1)]

    def test_scenarios_honnetes(self):
        for f, graines in self.CAS:
            for graine in range(graines):
                sc = load(ROOT / "scenarios" / f)
                sc["seed"] += graine
                e = Engine(sc)
                e.doctrine["auto_id"] = True
                while e.t < 400:
                    e.step()
                for tr in e.tracker.confirmed():
                    self.assertEqual(
                        tr.anomalies, [],
                        "fausse alarme sur %s / %s graine+%d : %s"
                        % (f, tr.num, graine, codes(tr.anomalies)))


if __name__ == "__main__":
    unittest.main()
