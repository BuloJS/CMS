"""Signal K : conversion de l'instantané, REST et flux WebSocket.

    python3 -m unittest discover -s tests
"""
import base64
import io
import json
import math
import os
import socket
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import signalk as sk        # noqa: E402
from sim.engine import Engine             # noqa: E402
from sim.scenario import load             # noqa: E402


def _frame(scenario="10-batterie-cotiere.toml", pas=2500):
    e = Engine(load(ROOT / "scenarios" / scenario))
    e.doctrine["auto_id"] = True
    for _ in range(pas):
        e.step()
    return e, e.snapshot()


class TestConversion(unittest.TestCase):
    def test_le_porteur_est_en_unites_si(self):
        e, f = _frame()
        rel = sk.releves(f)
        own = dict(rel[sk.SELF_CTX])
        pos = own["navigation.position"]
        self.assertAlmostEqual(pos["latitude"], f["own"]["lat"], places=5)
        self.assertAlmostEqual(pos["longitude"], f["own"]["lon"], places=5)
        # cap en radians, vitesse en m/s
        self.assertAlmostEqual(own["navigation.courseOverGroundTrue"],
                               math.radians(f["own"]["crs"]), places=4)
        self.assertAlmostEqual(own["navigation.speedOverGround"],
                               f["own"]["spd"] * 1852 / 3600, places=2)

    def test_les_valeurs_absentes_ne_sont_pas_publiees(self):
        e, f = _frame("07-flotte-mixte.toml", 100)
        chemins = [p for p, _ in sk.releves(f)[sk.SELF_CTX]]
        self.assertNotIn("propulsion.main.revolutions", chemins)   # RPM : pas d'automate
        self.assertIn("propulsion.main.state", chemins)            # tableau électrique simulé

    def test_rpm_et_barre_de_l_automate(self):
        e, f = _frame("07-flotte-mixte.toml", 100)
        f["machine"].update(source="MODBUS", rpm=120, barre=-35)
        own = dict(sk.releves(f)[sk.SELF_CTX])
        self.assertAlmostEqual(own["propulsion.main.revolutions"], 2.0)       # 120 tr/min = 2 Hz
        self.assertAlmostEqual(own["steering.rudderAngle"], math.radians(-35), places=4)

    def test_une_piste_a_son_contexte_et_ses_extensions(self):
        e, f = _frame("07-flotte-mixte.toml", 2500)
        rel = sk.releves(f)
        pistes = [c for c in rel if c.startswith("vessels.urn:mrn:cms:track:")]
        self.assertTrue(pistes)
        d = dict(rel[pistes[0]])
        self.assertIn("navigation.position", d)
        self.assertIn(d["cms.affiliation"], ("friend", "hostile", "neutral", "unknown"))

    def test_un_site_connu_est_un_aton_avec_sa_zone(self):
        e, f = _frame()
        rel = sk.releves(f)
        d = dict(rel["atons.urn:mrn:cms:site:BAT-1"])
        self.assertEqual(d["name"], "Coastal battery")
        self.assertAlmostEqual(d["cms.zoneRadius"], 30 * 1852.0)

    def test_identite_ais_d_une_piste(self):
        e, f = _frame("07-flotte-mixte.toml", 4000)
        rel = sk.releves(f)
        ais = [dict(v) for c, v in rel.items() if "mmsi" in dict(v)]
        self.assertTrue(ais, "au moins une piste porte une identité AIS")
        self.assertRegex(dict(ais[0])["mmsi"], r"^\d{9}$")

    def test_alertes_en_notifications(self):
        f = {"events": [{"n": 3, "code": "verrouillage", "p": {"brg": 84}},
                        {"n": 2, "code": "depart_missile", "p": {"src": "Coastal battery"}},
                        {"n": 1, "code": "classee_operateur", "p": {}}],
             "crash": {"type": "missile", "t": 400.0}}
        n = dict(sk.notifications(f))
        self.assertEqual(n["notifications.cms.verrouillage"]["state"], "alarm")
        self.assertIn("bearing 084", n["notifications.cms.verrouillage"]["message"])
        self.assertEqual(n["notifications.cms.depart_missile"]["state"], "emergency")
        self.assertEqual(n["notifications.cms.crash"]["state"], "emergency")
        self.assertNotIn("notifications.cms.classee_operateur", n)     # pas une alerte
        # flux : seulement ce qui est plus récent que le dernier vu
        self.assertEqual([p for p, _ in sk.notifications(f, 2)][:1], ["notifications.cms.verrouillage"])

    def test_modele_et_alias_self(self):
        e, f = _frame()
        m = sk.modele(f)
        self.assertEqual(m["self"], sk.SELF_CTX)
        feuille = sk.resoudre(m, "/vessels/self/navigation/position")
        self.assertEqual(set(feuille), {"value", "timestamp", "$source"})
        self.assertIsNone(sk.resoudre(m, "/vessels/self/navigation/inexistant"))

    def test_delta_au_format_signal_k(self):
        e, f = _frame()
        d = sk.en_deltas(sk.releves(f), ts="2026-01-01T00:00:00.000Z", contextes={sk.SELF_CTX})
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["context"], sk.SELF_CTX)
        u = d[0]["updates"][0]
        self.assertEqual(u["source"]["label"], "cms-lab")
        self.assertTrue(all("path" in v and "value" in v for v in u["values"]))


class TestWebSocket(unittest.TestCase):
    def test_cle_d_acceptation_du_rfc_6455(self):
        self.assertEqual(sk.cle_acceptation("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_trame_texte_courte_et_longue_se_relisent(self):
        for n in (5, 200, 70000):
            texte = "é" * n
            brut = sk.trame_texte(texte)
            # côté client les trames sont masquées : on masque celle-ci pour la relire
            masque = bytes([1, 2, 3, 4])
            corps = brut[2:] if n < 126 else (brut[4:] if n < 65536 else brut[10:])
            charge = corps
            tete = brut[:len(brut) - len(charge)]
            tete = bytes([tete[0], tete[1] | 0x80]) + tete[2:]
            masquee = bytes(b ^ masque[i % 4] for i, b in enumerate(charge))
            op, data = sk.lire_trame(io.BytesIO(tete + masque + masquee))
            self.assertEqual(op, 1)
            self.assertEqual(data.decode("utf-8"), texte)


class TestServeurEnDirect(unittest.TestCase):
    """Le vrai serveur, sur un port éphémère : découverte, REST, flux."""

    @classmethod
    def setUpClass(cls):
        from services import server
        cls.server = server
        cls._ancien = server.SIGNALK
        server.SIGNALK = True
        server.SIM.load("10-batterie-cotiere.toml")
        server.SIM.running = True
        cls.boucle = threading.Thread(target=server.SIM.run, daemon=True)
        cls.boucle.start()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.6)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.server.SIM.running = False
        cls.server.SIGNALK = cls._ancien

    def _get(self, chemin):
        return json.load(urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, chemin)))

    def test_decouverte_et_rest(self):
        d = self._get("/signalk")
        self.assertIn("v1", d["endpoints"])
        api = self._get("/signalk/v1/api/")
        self.assertEqual(api["version"], "1.7.0")
        pos = self._get("/signalk/v1/api/vessels/self/navigation/position")
        self.assertIn("latitude", pos["value"])

    def test_chemin_inconnu_404(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self._get("/signalk/v1/api/vessels/self/nope")
        self.assertEqual(c.exception.code, 404)

    def test_flux_websocket(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        cle = base64.b64encode(os.urandom(16)).decode()
        s.sendall(("GET /signalk/v1/stream?subscribe=all&period=0.2 HTTP/1.1\r\nHost: x\r\n"
                   "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n" % cle).encode())
        f = s.makefile("rb")
        entete = b""
        while not entete.endswith(b"\r\n\r\n"):
            entete += f.read(1)
        self.assertIn(b"101", entete.split(b"\r\n")[0])
        self.assertIn(sk.cle_acceptation(cle).encode(), entete)
        op, data = sk.lire_trame(f)
        hello = json.loads(data)
        self.assertEqual(hello["self"], sk.SELF_CTX)
        contextes = set()
        for _ in range(4):
            op, data = sk.lire_trame(f)
            contextes.add(json.loads(data)["context"])
        s.close()
        self.assertIn(sk.SELF_CTX, contextes)
        self.assertIn("atons.urn:mrn:cms:site:BAT-1", contextes)


if __name__ == "__main__":
    unittest.main()
