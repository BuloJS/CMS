"""Scénario 10 : une batterie côtière connue (site fixe à terre) qui verrouille
une zone et tire un missile toutes les deux minutes.

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.engine import Engine                        # noqa: E402
from sim.geo import KT, NM, rng                      # noqa: E402
from sim.scenario import load                        # noqa: E402
from sim.terre import Terre                          # noqa: E402
from tools.eaux import carte_pour, verifier_placement    # noqa: E402

SC = ROOT / "scenarios" / "10-batterie-cotiere.toml"


def _engine():
    e = Engine(load(SC))
    e.terre = Terre.depuis_carte(carte_pour(e.proj.lat0, e.proj.lon0), e.proj)
    e.fin_sur_impact = False
    return e


def _en_zone(e, dist_nm=25.0):
    """Porteur immobile à `dist_nm` de la batterie, dans sa zone (30 NM)."""
    bat = e.world["BAT-1"]
    e.own.speed = e.own.ordered_speed = 0.0
    # Sur la ligne batterie -> point de départ du scénario, qui est en mer.
    d0 = rng(bat.x, bat.y)
    k = dist_nm * NM / d0
    e.own.x, e.own.y = bat.x - bat.x * k, bat.y - bat.y * k
    return bat


class TestBatterie(unittest.TestCase):
    def test_site_fixe_a_terre_et_connu(self):
        e = _engine()
        bat = e.world["BAT-1"]
        self.assertEqual(bat.kind, "land")
        self.assertTrue(bat.connu)
        self.assertEqual(bat.speed, 0.0)
        self.assertTrue(e.terre.a_terre(bat.x, bat.y))

    def test_une_batterie_ne_peut_pas_etre_deplacee_en_mer(self):
        pb = verifier_placement(SC, 25.5, -90.0)          # golfe du Mexique
        self.assertIn("batterie_en_mer", [p["code"] for p in pb])

    def test_le_scenario_tel_quel_tient_dans_l_eau(self):
        self.assertEqual(verifier_placement(SC, 25.83, 56.74), [])

    def test_ce_n_est_pas_une_piste_radar(self):
        """Un repère, pas une piste : ni vecteur, ni sillage, ni bruit de
        pistage — la batterie n'entre jamais dans le pistage."""
        e = _engine()
        bat = _en_zone(e, 20.0)
        for _ in range(4000):
            e.step()
        for tr in e.tracker.confirmed():
            x, y = tr.pos
            self.assertGreater(rng(x - bat.x, y - bat.y), 3 * NM, tr.num)

    def test_le_snapshot_porte_le_site_et_sa_zone(self):
        e = _engine()
        _en_zone(e, 25.0)
        for _ in range(60):
            e.step()
        sites = e.snapshot()["sites"]
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0]["zone_nm"], 30.0)
        self.assertEqual(sites[0]["etat"], "verrou")

    def test_un_missile_toutes_les_deux_minutes(self):
        e = _engine()
        _en_zone(e, 25.0)
        while e.t < 400:
            e.step()
        lancers = sorted(c.launched_at for u, c in e.world.items() if u.startswith("BAT-1-M"))
        self.assertGreaterEqual(len(lancers), 3)
        self.assertLessEqual(len(lancers), 4)
        for a, b in zip(lancers, lancers[1:]):
            self.assertAlmostEqual(b - a, 120.0, delta=3.0)
        # un seul missile à chaque tir, pas une salve
        self.assertEqual(len([1 for c in e.world.values() if c.kind == "missile"
                              and abs(c.launched_at - lancers[0]) < 1.0]), 1)

    def test_le_missile_est_suivi_des_son_depart_de_la_batterie(self):
        """À 25 NM le radar ne verrait pas un missile rasant (horizon ~17 NM) :
        c'est le pistage précoce depuis un site connu qui donne le vol de A à Z."""
        e = _engine()
        bat = _en_zone(e, 25.0)
        for _ in range(20 * 200):
            e.step()
            if any(u.startswith("BAT-1-M") for u in e.world):
                break
        self.assertTrue(any(u.startswith("BAT-1-M") for u in e.world), "aucun tir")
        for _ in range(20 * 30):                              # 30 s de vol
            e.step()
        rapides = [tr for tr in e.tracker.confirmed() if tr.speed > 400 * KT]
        self.assertTrue(rapides, "le missile doit être pisté depuis la batterie")
        x, y = rapides[0].pos
        d_bat = rng(x - bat.x, y - bat.y) / NM
        self.assertLess(d_bat, 12.0)                          # encore près de son point de départ

    def test_la_batterie_tire_a_tour_de_role_sur_la_fregate_et_le_porteur(self):
        e = _engine()
        bat = _en_zone(e, 25.0)
        fr = e.world["FFG-ESCORTE"]
        fr.speed = 0.0                                       # immobile, dans la zone
        fr.x, fr.y = bat.x * 0.3, bat.y * 0.3                # ~ 10 NM de la batterie
        while e.t < 620:
            e.step()
        tirs = sorted((c.launched_at, c.target) for u, c in e.world.items() if u.startswith("BAT-1-M"))
        cibles = [t for _, t in tirs]
        self.assertGreaterEqual(len(cibles), 3, cibles)
        self.assertEqual(cibles[0], "FFG-ESCORTE")           # la frégate d'abord
        self.assertEqual(cibles[1], "OWN")                   # puis le porteur, à tour de rôle
        self.assertNotIn("MT-GULF-STAR", cibles)             # le civil n'est jamais visé

    def test_le_missile_sur_la_fregate_la_detruit_et_ne_touche_pas_le_porteur(self):
        e = _engine()
        e.fin_sur_impact = True
        bat = _en_zone(e, 25.0)
        fr = e.world["FFG-ESCORTE"]
        fr.speed = 0.0
        fr.x, fr.y = bat.x * 0.3, bat.y * 0.3
        # le porteur reste dans la zone ; on n'observe que le premier tir
        for _ in range(20 * 200):
            e.step()
            if not fr.alive:
                break
        self.assertFalse(fr.alive, "le premier missile vise la frégate")
        self.assertIsNone(e.crash)                           # le porteur n'est pas touché
        codes = [ev.get("code") for ev in e.events]
        self.assertIn("cible_detruite", codes)

    def test_les_leurres_ne_seduisent_pas_un_missile_vise_sur_un_autre(self):
        e = _engine()
        bat = _en_zone(e, 25.0)
        fr = e.world["FFG-ESCORTE"]
        fr.speed = 0.0
        fr.x, fr.y = e.own.x + 500.0, e.own.y                # à côté du porteur
        while not any(u.startswith("BAT-1-M") for u in e.world):
            e.step()
        m = next(c for u, c in e.world.items() if u.startswith("BAT-1-M"))
        self.assertEqual(m.target, "FFG-ESCORTE")
        m.x, m.y = e.own.x + 1000.0, e.own.y                 # dans les 5 NM du porteur
        e.deploy_decoys()
        self.assertFalse(m.seduced)

    def test_sortir_de_la_zone_arrete_les_tirs(self):
        e = _engine()
        bat = _en_zone(e, 25.0)
        while e.t < 300:
            e.step()
        n1 = len([u for u in e.world if u.startswith("BAT-1-M")])
        e.own.x, e.own.y = bat.x - bat.x * 45 * NM / rng(bat.x, bat.y), bat.y - bat.y * 45 * NM / rng(bat.x, bat.y)     # hors zone
        while e.t < 700:
            e.step()
        n2 = len([u for u in e.world if u.startswith("BAT-1-M")])
        self.assertEqual(n1, n2)


if __name__ == "__main__":
    unittest.main()
