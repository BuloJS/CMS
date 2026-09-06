"""Décodage AIS et ingestion de trafic réel.

Un message AIS réel est incomplet, bruité, et parfois faux. Les champs
« non disponible » de la norme ne sont pas des trous : ce sont des valeurs
précises (102,3 nœuds, 360°, latitude 91) qui, laissées passer, produisent
des navires à cent nœuds au pôle Nord — et qui ne se voient qu'une fois la
piste construite.

    python3 -m unittest discover -s tests
"""
import gzip
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ais import (AisBridge, Digitraffic, Fichier,      # noqa: E402
                          capturer, en_contact, _features, _flatten,
                          normalise)
from sim.ais import (decode, nav_status_label, rcs_from_length,  # noqa: E402
                     ship_type_label)
from sim.geo import KT, NM, Projection                          # noqa: E402


# Exemple donné par la documentation Digitraffic, mot pour mot.
STATIQUE_REEL = {
    "timestamp": 1668075026035, "destination": "UST LUGA", "name": "ARUNA CIHAN",
    "draught": 68, "eta": 733376, "posType": 15, "refA": 160, "refB": 33,
    "refC": 20, "refD": 12, "callSign": "V7WW7", "imo": 9543756, "type": 70,
}
POSITION_REELLE = {
    "time": 1668075025, "sog": 10.7, "cog": 326.6, "navStat": 0, "rot": 0,
    "posAcc": True, "raim": False, "heading": 325, "lon": 20.345818, "lat": 60.03802,
}


class TestDecodage(unittest.TestCase):

    def test_exemple_de_la_documentation(self):
        d = decode(dict(STATIQUE_REEL, mmsi=271044104))
        self.assertEqual(d["name"], "ARUNA CIHAN")
        self.assertEqual(d["type"], "Cargo")
        self.assertEqual(d["loa"], 193)          # refA + refB
        self.assertEqual(d["beam"], 32)          # refC + refD
        self.assertEqual(d["draught"], 6.8)      # décimètres -> mètres
        self.assertEqual(d["dest"], "UST LUGA")

    def test_tirant_deau_en_decimetres(self):
        """68 lu en mètres donne un cargo à 68 m de tirant d'eau, ce qui ne
        surprend personne tant qu'on ne le regarde pas."""
        self.assertEqual(decode({"draught": 68})["draught"], 6.8)

    def test_champs_absents_restent_absents(self):
        """Jamais de valeur par défaut : un tirant d'eau inventé est pire
        qu'un tirant d'eau inconnu."""
        d = decode({"name": "INCONNU"})
        for cle in ("draught", "loa", "type", "imo", "dest"):
            self.assertNotIn(cle, d)

    def test_types_et_statuts_notables(self):
        self.assertEqual(ship_type_label(35), "Bâtiment militaire")
        self.assertEqual(ship_type_label(70), "Cargo")       # famille 7x
        self.assertEqual(ship_type_label(84), "Pétrolier / chimiquier")
        self.assertTrue(decode({"type": 55})["notable"])     # police
        self.assertTrue(decode({"navStat": 3})["contraint"])  # manœuvre restreinte
        self.assertFalse(decode({"navStat": 0})["contraint"])

    def test_statut_non_defini_est_muet(self):
        self.assertEqual(nav_status_label(15), "")
        self.assertNotIn("navstat", decode({"navStat": 15}))


class TestRcs(unittest.TestCase):
    """La loi est étalonnée sur les valeurs écrites à la main dans les
    scénarios : un navire réel doit être exactement aussi détectable qu'un
    navire inventé de même taille."""

    def test_ancrages(self):
        self.assertAlmostEqual(rcs_from_length(25), 40, delta=6)      # embarcation
        self.assertAlmostEqual(rcs_from_length(180), 9000, delta=400)  # cargo

    def test_croissante(self):
        vals = [rcs_from_length(l) for l in (10, 25, 60, 120, 250)]
        self.assertEqual(vals, sorted(vals))

    def test_coque_composite_rend_moins(self):
        self.assertLess(rcs_from_length(14, 36), rcs_from_length(14, 70))

    def test_longueur_inexploitable(self):
        for v in (None, 0, -3, "", "abc"):
            self.assertIsNone(rcs_from_length(v))


class TestNormalisation(unittest.TestCase):

    def test_position_reelle(self):
        p = normalise(dict(POSITION_REELLE, mmsi=271044104))
        self.assertEqual(p["mmsi"], "271044104")
        self.assertAlmostEqual(p["lat"], 60.03802)
        self.assertAlmostEqual(p["sog"], 10.7)
        self.assertAlmostEqual(p["cog"], 326.6)

    def test_vitesse_non_disponible(self):
        """102,3 nœuds est la sentinelle de la norme, pas une mesure."""
        p = normalise({"mmsi": 1, "lat": 60.0, "lon": 25.0, "sog": 102.3, "cog": 90})
        self.assertEqual(p["sog"], 0.0)

    def test_route_non_disponible_retombe_sur_le_cap(self):
        p = normalise({"mmsi": 1, "lat": 60.0, "lon": 25.0, "sog": 5,
                       "cog": 360.0, "heading": 214})
        self.assertEqual(p["cog"], 214.0)

    def test_route_totalement_absente(self):
        p = normalise({"mmsi": 1, "lat": 60.0, "lon": 25.0, "sog": 5,
                       "cog": 360.0, "heading": 511})
        self.assertIsNone(p["cog"])

    def test_position_hors_domaine_rejetee(self):
        """91 / 181 : « position inconnue » de la norme."""
        self.assertIsNone(normalise({"mmsi": 1, "lat": 91.0, "lon": 181.0}))

    def test_sans_mmsi_ou_sans_position(self):
        self.assertIsNone(normalise({"lat": 60.0, "lon": 25.0}))
        self.assertIsNone(normalise({"mmsi": 1}))
        self.assertIsNone(normalise("pas un dictionnaire"))

    def test_enveloppes_acceptees(self):
        """GeoJSON, tableau nu, objet portant une liste : les trois."""
        feat = {"mmsi": 7, "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [25.0, 60.0]},
                "properties": {"sog": 8.0, "cog": 120.0}}
        self.assertEqual(len(_features({"type": "FeatureCollection",
                                        "features": [feat]})), 1)
        self.assertEqual(len(_features([feat])), 1)
        self.assertEqual(len(_features({"vessels": [feat]})), 1)
        self.assertEqual(_features({"rien": 1}), [])
        p = normalise(feat)
        self.assertEqual((p["lat"], p["lon"]), (60.0, 25.0))

    def test_aplatissement_des_proprietes(self):
        r = _flatten({"mmsi": 3, "properties": {"sog": 9.0},
                      "geometry": {"type": "Point", "coordinates": [24.5, 59.5]}})
        self.assertEqual((r["mmsi"], r["sog"], r["lat"], r["lon"]),
                         (3, 9.0, 59.5, 24.5))


class TestFlattenType(unittest.TestCase):
    """`type` veut dire deux choses selon le message, et les confondre fait
    disparaître le type de tous les navires du flux réel — sans que cela se
    voie sur un instantané dont le statique est déjà décodé."""

    def test_enveloppe_geojson_retiree(self):
        r = _flatten({"mmsi": 1, "type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [25.0, 60.0]}})
        self.assertNotIn("type", r)

    def test_type_de_navire_conserve(self):
        r = _flatten({"mmsi": 1, "type": 70, "name": "X"})
        self.assertEqual(r["type"], 70)
        self.assertEqual(decode(r)["type"], "Cargo")


class _Strict(BaseHTTPRequestHandler):
    """Imite un serveur qui négocie sérieusement le type de contenu, et qui
    compresse sans qu'on le lui demande."""
    ACCEPTE = "geo+json"
    CORPS = {"type": "FeatureCollection", "features": [
        {"mmsi": 230982000, "type": "Feature",
         "geometry": {"type": "Point", "coordinates": [25.10, 59.95]},
         "properties": {"sog": 12.5, "cog": 88.0, "navStat": 0}}]}

    def log_message(self, *a):
        pass

    def do_GET(self):
        acc = self.headers.get("Accept", "")
        if self.ACCEPTE not in acc and "*/*" not in acc:
            self.send_response(406)
            self.end_headers()
            return
        body = gzip.compress(json.dumps(self.CORPS).encode())
        self.send_response(200)
        self.send_header("Content-Type", "application/geo+json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _JsonSeulement(_Strict):
    """Cas plus dur : même `application/geo+json` est refusé."""
    ACCEPTE = "\x00rien"


class _CompressionObligatoire(_Strict):
    """La règle réelle de Digitraffic : sans Accept-Encoding: gzip, 406.

    C'est ce qui a fait échouer le premier branchement au flux public. Le
    client demandait `identity` — délibérément, pour n'avoir rien à
    décompresser — ce qui est précisément ce que le service interdit.
    """

    def do_GET(self):
        if "gzip" not in (self.headers.get("Accept-Encoding") or ""):
            self.send_response(406)
            self.end_headers()
            return
        _Strict.do_GET(self)


class TestNegociation(unittest.TestCase):
    """Le flux public a répondu 406 sur le premier jet de ce client :
    l'endpoint rend du GeoJSON, dont le type est `application/geo+json`, et
    demander strictement `application/json` se fait refuser."""

    def _servir(self, handler):
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d/api/ais/v1" % srv.server_address[1]

    def test_geojson_accepte_du_premier_coup(self):
        d = Digitraffic(base=self._servir(_Strict))
        self.assertEqual(len(d.positions(59.95, 25.10, 60)), 1)

    def test_repli_sur_406(self):
        """Un désaccord d'en-tête ne doit pas coûter le flux : on redemande
        sans rien exiger plutôt que d'abandonner."""
        d = Digitraffic(base=self._servir(_JsonSeulement))
        self.assertEqual(len(d.positions(59.95, 25.10, 60)), 1)

    def test_compression_obligatoire(self):
        """Digitraffic impose la compression sur toutes ses interfaces.
        Demander `identity` fait répondre 406 quels que soient les autres
        en-têtes — c'est la panne qu'a rencontrée le premier branchement."""
        d = Digitraffic(base=self._servir(_CompressionObligatoire))
        self.assertEqual(len(d.positions(59.95, 25.10, 60)), 1)

    def test_compression_non_demandee(self):
        """urllib ne décompresse pas seul. Un serveur qui gzippe quand même
        rendrait des octets illisibles au lieu d'un JSON."""
        d = Digitraffic(base=self._servir(_Strict))
        p = d.positions(59.95, 25.10, 60)[0]
        self.assertEqual(p["mmsi"], "230982000")
        self.assertAlmostEqual(p["sog"], 12.5)


class TestCapture(unittest.TestCase):
    """La capture est ce qui rend le lab indépendant du réseau : une fois
    faite, le scénario rejoue du trafic réel hors ligne, à l'identique."""

    def _servir(self, handler=_Strict):
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d/api/ais/v1" % srv.server_address[1]

    def test_ecrit_un_instantane_rejouable(self):
        rep = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, rep, True)
        cible = rep / "sous" / "dossier" / "capture.json"
        r = capturer(59.95, 25.10, 60, cible,
                     source=Digitraffic(base=self._servir()))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["n"], 1)
        self.assertTrue(cible.exists(), "le dossier parent doit être créé")

        # Ce qui a été écrit doit se relire par la source hors ligne, et se
        # déclarer comme une capture — pas comme l'instantané synthétique.
        src = Fichier(cible)
        self.assertEqual(len(src.positions()), 1)
        self.assertFalse(src.synthetique)
        self.assertEqual(src.etiquette, "CAPTURE")

    def test_ne_laisse_pas_de_fichier_tronque(self):
        """Une capture qui échoue ne doit pas abîmer l'instantané en place :
        le lab le charge au démarrage, et un JSON tronqué le bloquerait."""
        rep = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, rep, True)
        cible = rep / "capture.json"
        cible.write_text(json.dumps({"positions": [], "statique": {},
                                     "source": "capture Digitraffic"}),
                         encoding="utf-8")
        avant = cible.read_text(encoding="utf-8")
        r = capturer(59.95, 25.10, 60, cible,
                     source=Digitraffic(base="http://127.0.0.1:1/api"))
        self.assertFalse(r["ok"])
        self.assertIn("erreur", r)
        self.assertEqual(cible.read_text(encoding="utf-8"), avant)

    def test_un_echec_ne_leve_pas(self):
        """L'appelant peut être un fil de service qui ne doit pas mourir
        parce que le flux est momentanément indisponible."""
        r = capturer(59.95, 25.10, 60, "/dev/null/impossible",
                     source=Digitraffic(base="http://127.0.0.1:1/api"))
        self.assertFalse(r["ok"])


class TestContact(unittest.TestCase):

    def setUp(self):
        self.proj = Projection(60.10, 24.90)

    def test_fabrication(self):
        pos = normalise(dict(POSITION_REELLE, mmsi=271044104,
                             lat=60.20, lon=25.00))
        c = en_contact(pos, {"271044104": decode(STATIQUE_REEL)}, self.proj)
        self.assertEqual(c.uid, "AIS-271044104")
        self.assertEqual(c.name, "ARUNA CIHAN")
        self.assertEqual(c.kind, "surf")
        self.assertTrue(c.ais)
        self.assertAlmostEqual(c.speed, 10.7 * KT, places=6)
        self.assertGreater(c.rcs, 5000)                  # 193 m
        self.assertEqual(c.intent, "neutral")

    def test_sans_statique(self):
        """Position reçue avant le message 5 : le contact existe quand même,
        avec une RCS par défaut et un nom de circonstance."""
        pos = normalise({"mmsi": 999, "lat": 60.11, "lon": 24.91, "sog": 6, "cog": 90})
        c = en_contact(pos, {}, self.proj)
        self.assertEqual(c.name, "MMSI 999")
        self.assertGreater(c.rcs, 0)

    def test_position_geographique_respectee(self):
        pos = normalise({"mmsi": 1, "lat": 60.20, "lon": 24.90, "sog": 0, "cog": 0})
        c = en_contact(pos, {}, self.proj)
        self.assertAlmostEqual(c.x, 0.0, delta=1.0)      # même longitude
        self.assertGreater(c.y, 10000)                   # 0,1° au nord


class _FauxSim:
    """Le strict minimum dont `AisBridge` a besoin."""
    def __init__(self, engine):
        self.lock = threading.Lock()
        self.engine = engine


class _FauxEngine:
    def __init__(self, proj):
        self.proj = proj
        self.world = {}
        self.t = 0.0
        self.own = type("O", (), {"x": 0.0, "y": 0.0})()


class TestPont(unittest.TestCase):
    """Le pont ne doit jamais rembobiner un navire.

    Digitraffic rend la dernière position connue de chaque navire. Pour un
    navire lent, c'est le même message pendant trois minutes : le réappliquer
    à chaque interrogation replacerait le contact là où il était, et le
    filtre lirait une vitesse divisée par deux sur une position juste.
    """

    def setUp(self):
        self.proj = Projection(60.10, 24.90)
        self.eng = _FauxEngine(self.proj)
        self.sim = _FauxSim(self.eng)
        self.src = Fichier(ROOT / "fixtures" / "ais-golfe-finlande.json")
        self.pont = AisBridge(self.sim, self.src, rayon_nm=60.0)
        self.sim.ais_cfg = None

    def test_premier_cycle_peuple_le_monde(self):
        self.pont._cycle()
        self.assertGreater(len(self.eng.world), 10)
        self.assertTrue(all(uid.startswith("AIS-") for uid in self.eng.world))

    def test_message_inchange_ne_recale_pas(self):
        self.pont._cycle()
        uid = next(u for u, c in self.eng.world.items() if c.speed > 5 * KT)
        c = self.eng.world[uid]
        # Le contact navigue entre deux interrogations.
        c.step(60.0, self.eng.world)
        avance = (c.x, c.y)
        self.eng.t = 60.0
        self.pont._cycle()                     # même instantané, message identique
        self.assertEqual((c.x, c.y), avance, "le pont a rembobiné le navire")

    def test_message_neuf_recale(self):
        self.pont._cycle()
        uid = next(iter(self.eng.world))
        c = self.eng.world[uid]
        c.x += 5000.0
        # On truque l'horodatage : le pont doit alors reprendre la main.
        self.pont.applique[uid] = (-1.0, 0.0, 0.0)
        self.pont._cycle()
        self.assertNotAlmostEqual(c.x, self.eng.world[uid].x + 5000.0)

    def test_navire_oublie_apres_silence(self):
        self.pont._cycle()
        n = len(self.eng.world)
        self.assertGreater(n, 0)
        self.pont.source = Fichier.__new__(Fichier)      # source devenue muette
        self.pont.source._pos, self.pont.source.statique = [], {}
        self.pont.source.etat = "muette"
        self.eng.t = self.pont.oubli + 1.0
        self.pont._cycle()
        self.assertEqual(len(self.eng.world), 0)

    def test_changer_de_source_efface_les_navires_precedents(self):
        """Après une capture, l'ancien instantané ne doit rien laisser.

        Vider le suivi sans retirer les contacts les rendait orphelins : la
        boucle d'oubli n'itère que sur ce qui est suivi, donc ils restaient
        pour toujours. On voyait alors les navires de l'ancien instantané et
        ceux du nouveau, superposés.
        """
        from sim.entities import Contact
        self.eng.world["MV-SCENARIO"] = Contact(
            uid="MV-SCENARIO", name="scénario", kind="surf", x=0.0, y=0.0)
        self.pont.cfg = {"source": "fichier", "fichier": "x",
                         "rayon_nm": 60.0, "periode": 6.0}
        self.pont._cycle()
        self.assertGreater(len(self.eng.world), 10)

        rep = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, rep, True)
        autre = rep / "capture.json"
        autre.write_text(json.dumps({
            "source": "capture Digitraffic", "statique": {},
            "positions": [{"mmsi": 111111111, "lat": 59.95, "lon": 25.30,
                           "sog": 10, "cog": 90}]}), encoding="utf-8")
        self.sim.ais_cfg = {"source": "fichier", "fichier": str(autre),
                            "rayon_nm": 60.0, "periode": 6.0}
        self.pont._suivre_scenario()
        self.pont._cycle()

        ais = sorted(u for u in self.eng.world if u.startswith("AIS-"))
        self.assertEqual(ais, ["AIS-111111111"])
        # Le trafic réel s'ajoute au scénario, il ne le remplace pas.
        self.assertIn("MV-SCENARIO", self.eng.world)

    def test_navires_a_quai_ecartes(self):
        """Un grand port en tient des dizaines à quai en permanence, et
        Natural Earth ne modélise pas les bassins portuaires : ils se
        dessinent donc sur la terre. Le filtre porte sur le statut déclaré,
        pas sur une géométrie — un test point-dans-polygone pour cinq cents
        navires toutes les six secondes coûterait plus que tout le pont.
        """
        from services.ais import A_QUAI
        rep = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, rep, True)
        f = rep / "c.json"
        f.write_text(json.dumps({"statique": {}, "positions": [
            {"mmsi": 1, "lat": 60.10, "lon": 24.91, "sog": 0.0, "navStat": 5},
            {"mmsi": 2, "lat": 60.10, "lon": 24.92, "sog": 0.0, "navStat": 6},
            {"mmsi": 3, "lat": 60.10, "lon": 24.93, "sog": 0.2, "navStat": 1},
            {"mmsi": 4, "lat": 60.10, "lon": 24.94, "sog": 12.0, "navStat": 0},
        ]}), encoding="utf-8")

        pont = AisBridge(self.sim, Fichier(f), rayon_nm=60.0)
        pont.cfg = {"deja": True}
        pont._cycle()
        # Au mouillage est conservé : un navire sur rade est en mer, et
        # c'est souvent un contact intéressant.
        self.assertEqual(sorted(self.eng.world), ["AIS-3", "AIS-4"])
        self.assertEqual(A_QUAI, {5, 6})

    def test_navires_a_quai_conserves_sur_demande(self):
        rep = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, rep, True)
        f = rep / "c.json"
        f.write_text(json.dumps({"statique": {}, "positions": [
            {"mmsi": 1, "lat": 60.10, "lon": 24.91, "sog": 0.0, "navStat": 5},
        ]}), encoding="utf-8")
        pont = AisBridge(self.sim, Fichier(f), rayon_nm=60.0, inclure_a_quai=True)
        pont.cfg = {"deja": True}
        pont._cycle()
        self.assertIn("AIS-1", self.eng.world)

    def test_sans_origine_le_pont_se_tait(self):
        """Un scénario non ancré n'a nulle part où poser une position AIS."""
        self.eng.proj = None
        self.pont._cycle()
        self.assertEqual(self.eng.world, {})
        self.assertIn("origine", self.pont.etat)


class TestFixture(unittest.TestCase):

    def test_instantane_lisible(self):
        doc = json.loads((ROOT / "fixtures" / "ais-golfe-finlande.json")
                         .read_text(encoding="utf-8"))
        self.assertIn("note", doc)
        self.assertGreater(len(doc["positions"]), 10)
        src = Fichier(ROOT / "fixtures" / "ais-golfe-finlande.json")
        self.assertTrue(all(p["mmsi"] for p in src.positions()))


if __name__ == "__main__":
    unittest.main()
