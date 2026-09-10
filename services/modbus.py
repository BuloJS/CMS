"""Client Modbus TCP minimal, sans dépendance.

Quatre codes fonction suffisent au pont : lecture de bobines (1), de
registres d'entrée (4) et de maintien (3), écriture d'une bobine (5) et
d'un registre de maintien (6). Écrire ces quelques lignes évite d'épingler
une bibliothèque dont l'API bouge d'une version mineure à l'autre — et
rend l'image Docker installable sans réseau.

Modbus n'a ni authentification ni chiffrement : c'est une propriété du
protocole, pas un défaut de cette implémentation. Le bus reste sur un
réseau Docker interne, jamais exposé.
"""
import socket
import struct


class ModbusError(Exception):
    pass


class ModbusTcp:
    def __init__(self, host, port=502, unit=1, timeout=2.0):
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self.sock = None
        self.tid = 0

    def connect(self):
        self.close()
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.settimeout(self.timeout)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _tx(self, fc, payload):
        if self.sock is None:
            self.connect()
        self.tid = (self.tid + 1) & 0xFFFF
        body = struct.pack(">BB", self.unit, fc) + payload
        self.sock.sendall(struct.pack(">HHH", self.tid, 0, len(body)) + body)

        head = self._recv(6)
        _, _, ln = struct.unpack(">HHH", head)
        rest = self._recv(ln)
        if rest[1] & 0x80:
            raise ModbusError("exception %d sur fc %d" % (rest[2], fc))
        return rest[2:]

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ModbusError("connexion fermée par l'automate")
            buf += chunk
        return buf

    def read_coils(self, addr, count):
        data = self._tx(1, struct.pack(">HH", addr, count))[1:]
        bits = []
        for i in range(count):
            bits.append(bool(data[i // 8] >> (i % 8) & 1))
        return bits

    def read_input_registers(self, addr, count):
        data = self._tx(4, struct.pack(">HH", addr, count))[1:]
        return list(struct.unpack(">%dH" % count, data))

    def read_holding_registers(self, addr, count):
        data = self._tx(3, struct.pack(">HH", addr, count))[1:]
        return list(struct.unpack(">%dH" % count, data))

    def write_coil(self, addr, value):
        self._tx(5, struct.pack(">HH", addr, 0xFF00 if value else 0x0000))

    def write_register(self, addr, value):
        self._tx(6, struct.pack(">HH", addr, value & 0xFFFF))
