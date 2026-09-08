#!/usr/bin/env python3
"""Capteurs de terrain simulés, vus par l'automate comme un esclave Modbus.

C'est le sens de lecture d'un vrai automate : il interroge ses capteurs. On
expose donc des entrées TOR (fonction 2) et des registres d'entrée
(fonction 4), qu'OpenPLC vient chercher en tant que maître.

Le modèle physique tient en trois lignes, mais il a de l'inertie et du
bruit. C'est ce qui compte : une valeur qui saute d'un coup se repère
immédiatement comme fausse, une valeur qui dérive lentement est
indiscernable d'un vrai capteur — et rend la logique de seuil intéressante
à écrire (hystérésis, temporisation anti-rebond).
"""
import os
import random
import socketserver
import struct
import threading
import time

HOST, PORT = "0.0.0.0", int(os.environ.get("FIELD_PORT", "5020"))

# FIELD_PUMP permet de forcer la pompe à l'arrêt dès le démarrage du
# capteur — pratique pour rejouer une avarie de refroidissement sans
# toucher au reste de la chaîne (le contacteur n'a pas d'écriture Modbus
# côté OpenPLC, ce n'est qu'une entrée lue par l'automate maître).
_pump_defaut = os.environ.get("FIELD_PUMP", "1").strip().lower() not in ("0", "false", "off")

state = {"temp": 420, "press": 420, "rpm": 150, "pump": _pump_defaut, "rot": True}
lock = threading.Lock()


def physics():
    rnd = random.Random(11)
    while True:
        with lock:
            target = 420 if state["pump"] else 960
            tau = 55.0 if state["pump"] else 90.0
            state["temp"] += (target - state["temp"]) * (0.5 / tau) + rnd.gauss(0, 1.2)
            pt = 420 if state["pump"] else 60
            state["press"] += (pt - state["press"]) * (0.5 / 12.0) + rnd.gauss(0, 2)
            state["rpm"] += (150 - state["rpm"]) * 0.05 + rnd.gauss(0, 0.4)
        time.sleep(0.5)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            head = self._recv(6)
            if not head:
                return
            tid, _, ln = struct.unpack(">HHH", head)
            body = self._recv(ln)
            if not body or len(body) < 2:
                return
            unit, fc = body[0], body[1]
            addr, count = struct.unpack(">HH", body[2:6])
            if fc == 4:
                with lock:
                    regs = [max(0, min(65535, int(v))) for v in
                            (state["temp"], state["press"], state["rpm"], 0)]
                vals = (regs + [0] * 16)[addr:addr + count]
                payload = struct.pack(">BB", unit, 4) + bytes([count * 2]) \
                    + struct.pack(">%dH" % count, *vals)
            elif fc == 2:
                with lock:
                    bits = [state["rot"], state["pump"]] + [False] * 14
                n = (count + 7) // 8
                by = bytearray(n)
                for i in range(count):
                    if bits[addr + i]:
                        by[i // 8] |= 1 << (i % 8)
                payload = struct.pack(">BB", unit, 2) + bytes([n]) + bytes(by)
            elif fc == 5 and addr == 1:
                # Écriture bobine unique sur l'adresse pompe : le seul point
                # commandable de ce capteur, pour rejouer une avarie de
                # refroidissement en direct sans relancer le conteneur. Le
                # reste (temp/press/rpm/rot) reste en lecture seule — ce sont
                # des mesures, pas des ordres.
                with lock:
                    state["pump"] = (count == 0xFF00)
                payload = struct.pack(">BB", unit, 5) + body[2:6]  # écho requis
            else:
                payload = struct.pack(">BBB", unit, fc | 0x80, 1)
            self.request.sendall(struct.pack(">HHH", tid, 0, len(payload)) + payload)

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            try:
                c = self.request.recv(n - len(buf))
            except OSError:
                return b""
            if not c:
                return b""
            buf += c
        return buf


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    threading.Thread(target=physics, daemon=True).start()
    print("Capteurs de terrain — esclave Modbus sur %s:%d" % (HOST, PORT), flush=True)
    Server((HOST, PORT), Handler).serve_forever()
