"""Boucle de simulation — déterministe, à pas fixe.

Le cœur ne connaît ni le réseau ni l'écran : il avance d'un tick et rend un
instantané. C'est ce qui permet de le faire tourner à 200x la vitesse réelle
pour du Monte-Carlo, puis de rejouer un run dans la console. Une graine RNG
fixée rend un scénario reproductible au tick près.
"""
import random

from . import tewa
from .armement import Armement
from .entities import Contact, Ownship
from .geo import KT, NM, Projection, bearing, rng
from .platform import Platform
from .scenario import WEAPONS
from . import veracite
from .sensors import Ais, Esm, Iff, Radar, radar_horizon
from .tracker import Tracker

DT = 0.05                 # 20 Hz
CIWS_RANGE = 2 * NM
AUTO_SAM_MARGIN = 45.0   # s avant la butée : de quoi observer et retirer
DECOY_RANGE = 5 * NM


class Interceptor:
    """Munition en vol. Sa seule raison d'être est le délai : on ne sait pas
    tout de suite si on a touché, et cette incertitude déclenche — ou non —
    le réengagement."""
    __slots__ = ("x", "y", "tgt", "v", "pk", "eta", "ef", "salve", "assessed")

    def __init__(self, x, y, tgt, v, pk, eta, ef, salve):
        self.x, self.y, self.tgt, self.v = x, y, tgt, v
        self.pk, self.eta, self.ef, self.salve = pk, eta, ef, salve
        self.assessed = False


class Engine:
    def __init__(self, sc):
        self.sc = sc
        self.rand = random.Random(sc["seed"])
        self.t = 0.0
        o = sc["ownship"]
        self.own = Ownship(course=float(o.get("course", 0)),
                           speed=float(o.get("speed_kt", 14)) * KT,
                           ordered_course=float(o.get("course", 0)),
                           ordered_speed=float(o.get("speed_kt", 14)) * KT,
                           mast_height=float(o.get("mast_m", 30)))
        og = sc.get("origine") or {}
        # Sans point de référence dans le scénario, la simulation reste
        # relative au porteur et la console n'affiche pas de position
        # géographique. Rien ne casse : c'est le cas de tous les
        # scénarios écrits avant que le monde réel entre dans le lab.
        self.proj = (Projection(og["lat"], og["lon"])
                     if "lat" in og and "lon" in og else None)
        self.world = {c.uid: c for c in sc["contacts"]}
        self.radar = Radar()
        self.esm, self.iff, self.ais = Esm(), Iff(), Ais()
        self.tracker = Tracker()
        self.platform = Platform()
        self.armement = Armement()
        self.effectors = tewa.default_effectors()
        self.doctrine = {"auto_ciws": True, "auto_sam": False, "auto_id": False}
        self.shots = []
        self.events = []
        self.solutions = []
        self.threats = []
        self.pending = list(sc["events"])
        self.decoys = 12
        self.hooked = None
        self.ais_orphelins = []          # déclarations sans écho radar
        self._anomalies_dites = {}       # pour ne journaliser qu'une fois
        self._seq = 0

    # -- journal ---------------------------------------------------------
    def log(self, sev, txt, code=None, **params):
        """Consigne un événement.

        `txt` est le rendu français, pour la ligne de commande et les
        enregistrements. `code` et `params` sont ce que la console utilise :
        elle reformate dans la langue de son opérateur. Le cœur ne fabrique
        pas de phrases pour un écran qu'il ne connaît pas — il l'a fait
        jusqu'ici par commodité, ce qui rendait toute traduction impossible.
        """
        self._seq += 1
        ev = {"n": self._seq, "t": round(self.t, 1), "sev": sev, "txt": txt}
        if code:
            ev["code"], ev["p"] = code, params
        self.events.insert(0, ev)
        del self.events[60:]

    # -- scénario --------------------------------------------------------
    def _fire_events(self):
        while self.pending and self.pending[0].get("at", 0) <= self.t:
            e = self.pending.pop(0)
            k = e.get("type")
            if k == "launch":
                self._launch(e)
            elif k == "platform":
                self.platform.pump = bool(e.get("pump", True))
                self.log("crit" if not self.platform.pump else "info",
                         "IPMS — pompe de refroidissement "
                         + ("à l'arrêt" if not self.platform.pump else "rétablie"),
                         code="pompe", marche=bool(self.platform.pump))
            elif k == "order":
                if "course" in e:
                    self.own.ordered_course = float(e["course"])
                if "speed_kt" in e:
                    self.own.ordered_speed = float(e["speed_kt"]) * KT
                self.log("info", "Manœuvre du porteur ordonnée", code="manoeuvre")
            elif k == "emitter":
                c = self.world.get(e.get("from"))
                if c:
                    c.emitters = list(e.get("emitters", []))
            elif k == "ais":
                # Extinction, allumage, ou début d'une position falsifiée.
                # Le journal ne dit rien : le système n'est pas censé savoir
                # qu'un navire vient de couper son transpondeur — il ne peut
                # que le constater ensuite, par le silence.
                c = self.world.get(e.get("from"))
                if c:
                    if "on" in e:
                        c.ais = bool(e["on"])
                    if "ecart_nm" in e and "ecart_brg" in e:
                        from .geo import to_xy
                        c.ais_ecart = to_xy(float(e["ecart_brg"]),
                                            float(e["ecart_nm"]) * NM)
                    if "declare" in e:
                        c.ais_declare = dict(e["declare"])

    def _launch(self, e):
        src = self.world.get(e.get("from"))
        if not src:
            return
        spec = WEAPONS.get(e.get("weapon", "asm"), WEAPONS["asm"])
        for i in range(int(e.get("count", 1))):
            uid = "%s-M%d" % (src.uid, i + 1)
            brg = bearing(self.own.x - src.x, self.own.y - src.y)
            # Dispersion de salve. Deux munitions tirées au même instant depuis
            # le même point volent en formation parfaite et ne forment qu'une
            # seule piste — ce qui est vrai, mais rend le tableau illisible et
            # ne correspond à aucune doctrine de tir réelle.
            from math import radians, cos, sin
            a = radians(brg)
            back, side = i * 4.0 * spec["speed"], (i - 0.5) * 600.0
            sx = src.x - back * sin(a) + side * cos(a)
            sy = src.y - back * cos(a) - side * sin(a)
            self.world[uid] = Contact(
                uid=uid, name=spec["name"], kind="missile",
                x=sx, y=sy, alt=spec["alt"], course=brg,
                speed=spec["speed"], rcs=spec["rcs"], intent="hostile",
                target="OWN", launched_at=self.t)
        self.log("crit", "Départ missile détecté par ESM — origine %s" % src.name,
                 code="depart_missile", src=src.name)

    # -- tick ------------------------------------------------------------
    def step(self):
        dt = DT
        self.t += dt
        self._fire_events()
        self.platform.step(dt)
        self.radar.power = self.platform.radar_power
        self.own.step(dt)

        tgt = {"OWN": self.own}
        for c in self.world.values():
            c.step(dt, tgt)

        prev = self.radar.step(dt)
        scan_done = self.radar.sweep < prev
        plots = []
        for c in list(self.world.values()):
            if not c.alive:
                continue
            b = bearing(c.x - self.own.x, c.y - self.own.y)
            if self.radar.crossed(prev, b):
                p = self.radar.detect(self.own, c, self.rand)
                if p:
                    plots.append((p[0], p[1], self.radar.sigma_r, self.radar.sigma_b))
        self.tracker.step(dt, self.own, plots, self.t, scan_done)

        if scan_done:
            self.ais_orphelins = []
            self._passive()
            self._extinctions()
            self._impacts()

        self._weapons(dt)
        self._assess()
        return scan_done

    def _passive(self):
        for c in self.world.values():
            if not c.alive:
                continue
            d = self.esm.detect(self.own, c, self.rand)
            if d:
                tr = self.tracker.fuse_bearing(self.own, d[0], d[1])
                if tr and d[1] == "fc" and tr.aff != "hostile":
                    self.log("crit", "%s — illumination conduite de tir" % tr.num,
                             code="illumination", piste=tr.num)
            rec = self.ais.receive(self.own, c)
            if rec:
                # Corrélation sur la position **déclarée**, pas sur la vérité
                # terrain. Le système ne reçoit que celle-là. Tant que l'AIS
                # est honnête cela ne change rien ; dès qu'il ment, c'est
                # toute la différence entre modéliser la tromperie et la
                # gommer.
                tr = self._correler_ais(rec)
                if tr is None:
                    # Une déclaration sans écho radar en face. Ce n'est pas
                    # une anomalie en soi — un petit mobile s'entend plus
                    # loin qu'il ne se voit — mais l'opérateur doit le savoir.
                    self.ais_orphelins.append(rec)
                    continue
                tr.ident = rec["name"]
                tr.ais = rec
                tr.ais_vu = self.t
                tr.sources = tr.sources | {"AIS"}
                tr.anomalies = veracite.controler(tr, rec, self.t)
                for a in tr.anomalies:
                    if a["code"] not in self._anomalies_dites.get(tr.num, ()):
                        self._anomalies_dites.setdefault(tr.num, set()).add(a["code"])
                        self.log("warn", "%s — %s : %s"
                                 % (tr.num, a["libelle"], a["detail"]),
                                 code="anomalie", piste=tr.num,
                                 anomalie=a["code"], **a["params"])
                # Une identité coopérative vaut classement en neutre — mais
                # seulement si elle est crédible. Un fraudeur ne doit pas
                # obtenir gratuitement le statut que sa fraude vise.
                if tr.aff == "unknown" and c.intent != "hostile" and not tr.anomalies:
                    tr.aff = "neutral"
            a = self.iff.interrogate(self.own, c)
            if a:
                for tr in self.tracker.confirmed():
                    x, y = tr.pos
                    if rng(x - c.x, y - c.y) < 900:
                        tr.iff = a
                        if a == "ami" and tr.aff == "unknown":
                            tr.aff = "friend"

    def _correler_ais(self, rec):
        """Rapproche une déclaration AIS de la piste radar la plus proche.

        Fenêtre volontairement large : le but n'est pas de rejeter les
        déclarations décalées — ce sont précisément les intéressantes — mais
        de ne pas coller une déclaration sur la mauvaise piste dans un rail
        dense. Au-delà, la déclaration reste orpheline et le dit.
        """
        best, bd = None, 1200.0
        for tr in self.tracker.confirmed():
            x, y = tr.pos
            d = rng(x - rec["x"], y - rec["y"])
            if d < bd:
                best, bd = tr, d
        return best

    def _extinctions(self):
        """Repère les transpondeurs qui se sont tus sur un contact tenu."""
        portee = radar_horizon(self.own.mast_height, 20)
        for tr in self.tracker.confirmed():
            if not tr.ais_vu or tr.ais_vu >= self.t - 0.1:
                continue
            x, y = tr.pos
            a = veracite.extinction(tr, self.t, portee,
                                    rng(x - self.own.x, y - self.own.y))
            if not a:
                continue
            if not any(z["code"] == "extinction" for z in tr.anomalies):
                tr.anomalies = tr.anomalies + [a]
            if "extinction" not in self._anomalies_dites.get(tr.num, ()):
                self._anomalies_dites.setdefault(tr.num, set()).add("extinction")
                self.log("warn", "%s — %s : %s" % (tr.num, a["libelle"], a["detail"]),
                         code="anomalie", piste=tr.num, anomalie=a["code"],
                         **a["params"])

    def _impacts(self):
        for c in list(self.world.values()):
            if c.kind == "missile" and c.alive:
                if rng(c.x - self.own.x, c.y - self.own.y) < 120:
                    c.alive = False
                    self.log("crit", "IMPACT sur le porteur — %s" % c.name,
                             code="impact", nom=c.name)

    # -- effecteurs ------------------------------------------------------
    def _weapons(self, dt):
        # CIWS automatique : le dernier rempart ne demande pas l'avis de
        # l'opérateur, il n'en a pas le temps.
        if self.doctrine.get("auto_ciws"):
            for tr in self.tracker.confirmed():
                if tr.aff != "hostile":
                    continue
                x, y = tr.pos
                if rng(x - self.own.x, y - self.own.y) < CIWS_RANGE:
                    if not self._awaiting_assessment(tr):
                        self.engage(tr.num, "ciws", auto=True)
        self.shots = [s for s in self.shots if not s.assessed or s.eta > -30.0]
        for s in list(self.shots):
            s.eta -= dt
            if s.eta <= 0 and not s.assessed:
                s.assessed = True
                self._resolve(s)

    def engage(self, track_num, ef_key, auto=False):
        tr = self.tracker.tracks.get(track_num)
        ef = next((e for e in self.effectors if e.key == ef_key), None)
        if not tr or not ef or ef.free_channels < 1 or ef.rounds < 1:
            return False
        # Un seul tir non évalué par couple (piste, effecteur) : le canal de
        # conduite de tir reste accroché jusqu'à l'évaluation du résultat.
        if any(s.tgt == track_num and s.ef == ef_key and not s.assessed
               for s in self.shots):
            return False
        x, y = tr.pos
        vx, vy = tr.vel
        ox, oy = self.own.vxy
        from .geo import intercept_time
        tof = intercept_time(x - self.own.x, y - self.own.y, vx - ox, vy - oy, ef.v)
        if tof is None:
            return False
        n = tewa.salvo_for(ef.pk)
        ef.busy += 1
        ef.rounds = max(0, ef.rounds - n)
        self.shots.append(Interceptor(self.own.x, self.own.y, track_num, ef.v,
                                      1 - (1 - ef.pk) ** n, tof, ef.key, n))
        self.log("warn", "%s — %s x%d sur %s%s"
                 % (ef.label, "tir", n, track_num, " (doctrine)" if auto else ""),
                 code="tir", ef=ef.key, n=n, piste=track_num, auto=bool(auto))
        return True

    def _resolve(self, s):
        ef = next((e for e in self.effectors if e.key == s.ef), None)
        if ef:
            ef.busy = max(0, ef.busy - 1)
        tr = self.tracker.tracks.get(s.tgt)
        if not tr:
            self.log("info", "%s — piste perdue avant interception" % s.tgt,
                     code="piste_perdue", piste=s.tgt)
            return
        # Fenêtre d'évaluation du résultat : tant qu'aucun plot frais n'est
        # revenu sur la piste, on ne sait pas si elle est morte ou si elle a
        # simplement disparu du faisceau. La doctrine suspend le tir plutôt
        # que de vider les râteliers sur une piste déjà détruite.
        tr.assessed_at = self.t
        x, y = tr.pos
        victim, best = None, 1500.0
        for c in self.world.values():
            if c.alive and rng(c.x - x, c.y - y) < best:
                victim, best = c, rng(c.x - x, c.y - y)
        if victim is None:
            self.log("info", "%s — plus de cible à l'interception" % s.tgt,
                     code="plus_de_cible", piste=s.tgt)
        elif self.rand.random() < s.pk:
            victim.alive = False
            self.log("info", "%s — destruction confirmée (%s)" % (s.tgt, s.ef.upper()),
                     code="destruction", piste=s.tgt, ef=s.ef)
        else:
            self.log("warn", "%s — échec d'interception, réengagement à évaluer" % s.tgt,
                     code="echec_interception", piste=s.tgt)

    def deploy_decoys(self):
        if self.decoys < 2:
            return False
        self.decoys -= 2
        n = 0
        for c in self.world.values():
            if c.kind == "missile" and c.alive and not c.seduced:
                if rng(c.x - self.own.x, c.y - self.own.y) < DECOY_RANGE:
                    if self.rand.random() < 0.45:
                        c.seduced, n = True, n + 1
                        c.course = (c.course + self.rand.choice((-35, 35))) % 360
        self.log("warn", "Leurres largués — %d missile(s) séduit(s)" % n,
                 code="leurres", n=n)
        return True

    # -- produit ---------------------------------------------------------
    def _assess(self):
        evals = []
        for tr in self.tracker.confirmed():
            evals.append({"track": tr, "eval": tewa.evaluate(tr, self.own, self.t)})
        evals.sort(key=lambda e: -e["eval"]["score"])
        self.threats = evals
        # Identification par doctrine. Dans un vrai CMS l'identification est un
        # acte d'opérateur ; la doctrine ne prend la main que sur des critères
        # explicites et armés à l'avance, et le journal dit toujours qui a
        # classé la piste.
        if self.doctrine.get("auto_id"):
            for e in evals:
                tr, ev = e["track"], e["eval"]
                if tr.aff != "unknown" or tr.iff == "ami":
                    continue
                # Le radar de veille est 2D : la piste n'a pas d'altitude, on
                # ne peut donc pas invoquer un « profil rasant ». Les critères
                # tenables sont la géométrie, la vitesse et l'absence de
                # réponse IFF — d'où l'importance d'interroger avant de classer.
                inbound = ev["tcpa"] > 0 and ev["cpa"] < 3 * NM
                fast_closer = inbound and tr.speed > 150 and tr.iff == "pas de réponse"
                if tr.emitter == "fc" or fast_closer:
                    tr.aff, tr.classified_by = "hostile", "doctrine"
                    raison = "fc" if tr.emitter == "fc" else "convergent"
                    self.log("crit", "%s — classée HOSTILE par doctrine (%s)"
                             % (tr.num, "illumination conduite de tir"
                                if raison == "fc"
                                else "convergent rapide sans réponse IFF"),
                             code="classee_doctrine", piste=tr.num, raison=raison)
        self.solutions = tewa.solutions(evals, self.own, self.effectors,
                                        self.t, self.doctrine)
        if self.doctrine.get("auto_sam"):
            for s in self.solutions:
                # Tirer à cinq secondes de la butée, c'est tirer au dernier
                # instant possible : aucune marge pour observer le résultat et
                # réengager. Une doctrine tenable ouvre le feu dès que la cible
                # est dans l'enveloppe, en gardant de quoi retirer une fois.
                if s["effecteur"] == "sam" and s["statut"] == "recommandé" \
                        and s["butee"] is not None and s["butee"] < AUTO_SAM_MARGIN:
                    tr = self.tracker.tracks.get(s["piste"])
                    if tr and self._awaiting_assessment(tr):
                        continue
                    self.engage(s["piste"], "sam", auto=True)

    def _awaiting_assessment(self, tr, window=15.0):
        at = getattr(tr, "assessed_at", None)
        return at is not None and tr.updated <= at and self.t - at < window

    def snapshot(self):
        tks = []
        for e in self.threats:
            tr, ev = e["track"], e["eval"]
            x, y = tr.pos
            ex, ey = tr.ellipse()
            tks.append({
                "id": tr.num,
                "x": round((x - self.own.x) / NM, 4),
                "y": round((y - self.own.y) / NM, 4),
                "brg": round(bearing(x - self.own.x, y - self.own.y), 1),
                "rng": round(rng(x - self.own.x, y - self.own.y) / NM, 2),
                "crs": round(tr.course, 1), "spd": round(tr.speed / KT, 1),
                "aff": tr.aff, "qual": round(tr.quality, 2),
                "ell": [round(ex / NM, 4), round(ey / NM, 4)],
                "src": sorted(tr.sources), "emitter": tr.emitter,
                "iff": tr.iff, "ident": tr.ident,
                # La déclaration AIS est renvoyée sans sa position : la
                # console affiche des pistes, pas des déclarations. L'écart
                # entre les deux est déjà résumé par l'anomalie.
                "ais": {k: v for k, v in tr.ais.items()
                        if k not in ("x", "y", "sog", "cog")},
                "anomalies": tr.anomalies,
                "score": round(ev["score"], 3), "fact": ev["facteurs"],
                "cpa": round(ev["cpa"] / NM, 2),
                "tcpa": round(ev["tcpa"], 0) if ev["tcpa"] > 0 else -1,
                # Une dizaine de tours d'antenne suffisent à lire le sillage.
                # Au-delà, la trace d'un mobile rapide traverse tout le scope
                # et masque l'image au lieu de la renseigner.
                "trail": [[round((hx - self.own.x) / NM, 3),
                           round((hy - self.own.y) / NM, 3)]
                          for hx, hy in tr.history[-10:]],
            })
        own = {"crs": round(self.own.course, 1),
               "spd": round(self.own.speed / KT, 1),
               "ord_crs": round(self.own.ordered_course, 1),
               "ord_spd": round(self.own.ordered_speed / KT, 1)}
        geo = None
        if self.proj:
            # La console a besoin du point de référence pour convertir la
            # position du curseur ; elle refait la projection côté client
            # plutôt que de demander au serveur à chaque mouvement de souris.
            lat, lon = self.proj.to_latlon(self.own.x, self.own.y)
            own["lat"], own["lon"] = round(lat, 6), round(lon, 6)
            geo = {"lat0": self.proj.lat0, "lon0": self.proj.lon0,
                   "m_lat": round(self.proj.m_per_lat, 4),
                   "m_lon": round(self.proj.m_per_lon, 4)}
        return {
            "t": round(self.t, 2),
            "scenario": self.sc["name"],
            "sweep": round(self.radar.sweep, 1),
            "geo": geo,
            "own": own,
            "tracks": tks,
            "solutions": self.solutions[:8],
            "platform": self.platform.snapshot(),
            "armement": self.armement.snapshot(),
            "effecteurs": [{"key": e.key, "label": e.label, "role": e.role,
                            "rounds": e.rounds, "libres": e.free_channels,
                            "canaux": e.channels} for e in self.effectors],
            "leurres": self.decoys,
            "doctrine": dict(self.doctrine),
            "tirs": [{"tgt": s.tgt, "ef": s.ef, "eta": round(s.eta, 1)}
                     for s in self.shots if not s.assessed],
            # Déclarations AIS sans écho radar en face. Un petit mobile
            # s'entend plus loin qu'il ne se voit, donc ce n'est pas une
            # anomalie — mais un opérateur doit savoir que quelque chose se
            # déclare là où son radar ne montre rien.
            "ais_orphelins": [
                {"x": round((o["x"] - self.own.x) / NM, 3),
                 "y": round((o["y"] - self.own.y) / NM, 3),
                 "nom": o.get("name", ""), "mmsi": o.get("mmsi", ""),
                 "type": o.get("type", "")}
                for o in self.ais_orphelins[:40]],
            "events": self.events[:14],
        }
