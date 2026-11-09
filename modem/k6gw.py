"""GROUNDWAVE (c/k6/mod/gw.c) on the PC: its packets and its rules.

The packets are 64 bytes, PN9-whitened as the pager's, sent by the FD6818's FSK modem at PHY F
(FFSK 1200/1800 Hz, 1800 baud). De-whitened:

  0 0xA6   1 type   2-3 game id (LE)   4 seq   5 from (station 0/1)   6 round   7 turn
  8-11 the sender's game hash before its action (LE; 0: don't check)
  12..  HELLO: seed (4, LE), rounds | SHOP: shield, inv[14], money (2, LE) |
        MOVE: angle, power, item | ACK: the seq and type it answers
  46-51 the call ("AG7EW")   62-63 CRC-16/CCITT-FALSE of 0-61, big-endian

Game(): the rules themselves, c/k6/mod/gw_game.h built for this machine (c/k6/gw_host.c), so a
PC opponent stays in step with the radio's game to the bit.

Aimer: a PC opponent worth playing. It tries every angle and power on a copy of the game (the
same rules, so what it sees is what will happen), then each weapon it holds, and misses on
purpose by a skill-dependent amount. buy(): its shopping. Link: when the PC's packets go on air
(tools/k6_gw.py play). The same "send moves, not state" pattern (insight 47) fits any turn game:
both sides run the same deterministic rules and only the moves cross the air.
SPDX-License-Identifier: MIT
"""
import random
import ctypes
import os
import struct
import subprocess
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
MAGIC = 0xA6
HELLO, JOIN, SHOP, MOVE, ACK, BYE = range(1, 7)
NAMES = {HELLO: "HELLO", JOIN: "JOIN", SHOP: "SHOP", MOVE: "MOVE", ACK: "ACK", BYE: "BYE"}
CALL = b"AG7EW"
NI = 14


def pn9(n=64):
    s, out = 0x1FF, []
    for _ in range(n):
        out.append(s & 0xFF)
        for _ in range(8):
            s = (s >> 1) | (((s ^ (s >> 5)) & 1) << 8)
    return bytes(out)


PN9 = pn9()


def crc16(b):
    c = 0xFFFF
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else c << 1
        c &= 0xFFFF
    return c


def whiten(b):
    return bytes(x ^ y for x, y in zip(b, PN9))


def build(type_, gid, seq, frm, rnd=0, turn=0, h=0, body=b""):
    """a packet as it goes on air (whitened)"""
    p = bytearray(64)
    p[0:12] = struct.pack("<BBHBBBBI", MAGIC, type_, gid, seq, frm, rnd, turn, h)
    p[12:12 + len(body)] = body
    p[46:46 + len(CALL)] = CALL
    p[62:64] = struct.pack(">H", crc16(p[:62]))
    return whiten(p)


def parse(pkt):
    """a received packet (whitened) -> dict, or None if it doesn't check"""
    p = whiten(pkt)
    if len(p) != 64 or p[0] != MAGIC or crc16(p[:62]) != struct.unpack(">H", p[62:64])[0]:
        return None
    _, t, gid, seq, frm, rnd, turn, h = struct.unpack("<BBHBBBBI", p[:12])
    return {"type": t, "gid": gid, "seq": seq, "from": frm, "round": rnd, "turn": turn, "hash": h,
            "body": bytes(p[12:46]), "call": p[46:52].rstrip(b"\0").decode("ascii", "replace")}


_lib = None


def lib():
    global _lib
    if _lib is None:
        so = os.path.join(tempfile.mkdtemp(), "libgw.so")
        k6 = os.path.join(ROOT, "c", "k6")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-I" + k6,
                        os.path.join(k6, "gw_host.c"), "-o", so], check=True)
        _lib = ctypes.CDLL(so)
        _lib.gw_h_hash.restype = ctypes.c_uint32
    return _lib


class Game:
    """gw_t on the host. Offsets follow gw_game.h's struct layout (checked by the tests)."""
    TOP, P0 = 4, None

    def __init__(self, seed, rounds=3):
        self.L = lib()
        self.g = ctypes.create_string_buffer(self.L.gw_size())
        self.L.gw_h_new(self.g, ctypes.c_uint32(seed), rounds)

    def new_round(self):
        self.L.gw_h_round(self.g)

    def buy(self, j, item):
        return self.L.gw_h_buy(self.g, j, item)

    def hash(self):
        return self.L.gw_h_hash(self.g) & 0xFFFFFFFF

    def shot(self, ang, pw, item):
        """fire, fly and end the turn: the steps the flight took"""
        n = ctypes.c_int()
        return self.L.gw_h_shot(self.g, ang, pw, item, None, 0, ctypes.byref(n))

    def state(self):
        return GwState.from_buffer_copy(self.g.raw[:ctypes.sizeof(GwState)])

    def clone(self):
        """a copy to try a shot on (the real game is left alone)"""
        c = Game.__new__(Game)
        c.L = self.L
        c.g = ctypes.create_string_buffer(self.g.raw, len(self.g.raw))
        return c

    def set_shop(self, j, sh, inv, money):
        self.L.gw_h_set_shop(self.g, j, sh, (ctypes.c_uint8 * NI)(*inv), money)

    def shop_of(self, j):
        p = self.state().p[j]
        return p.sh, list(p.inv), p.money


class Pl(ctypes.Structure):
    _fields_ = [("x", ctypes.c_uint8), ("y", ctypes.c_uint8), ("hp", ctypes.c_uint8),
                ("sh", ctypes.c_uint8), ("she", ctypes.c_uint8), ("wins", ctypes.c_uint8),
                ("dealt", ctypes.c_uint8), ("pad", ctypes.c_uint8), ("money", ctypes.c_uint16),
                ("inv", ctypes.c_uint8 * NI), ("ang", ctypes.c_uint8), ("pow", ctypes.c_uint8),
                ("wpn", ctypes.c_uint8)]


class GwState(ctypes.Structure):
    _fields_ = [("rng", ctypes.c_uint32), ("top", ctypes.c_uint8 * 129), ("round", ctypes.c_uint8),
                ("rounds", ctypes.c_uint8), ("mover", ctypes.c_uint8), ("first", ctypes.c_uint8),
                ("turn", ctypes.c_uint8), ("over", ctypes.c_uint8), ("winner", ctypes.c_uint8),
                ("wind", ctypes.c_int8), ("nb", ctypes.c_uint8), ("bx", ctypes.c_uint8 * 8),
                ("by", ctypes.c_uint8 * 8), ("br", ctypes.c_uint8 * 8), ("p", Pl * 2)]


def shop_body(sh, inv, money):
    return bytes([sh]) + bytes(inv) + struct.pack("<H", money)


def body_shop(body):
    return body[0], list(body[1:1 + NI]), struct.unpack("<H", body[15:17])[0]


# ---- a PC opponent worth playing ----

KILOWATT, FCC, CHARGER, CW_STRIKE, BEVERAGE, LID, SKIP, REPEATER, YAGI, COAX, TROPO = range(1, 12)
GROUND_ROD, FARADAY = 12, 13
SHOPPING = [
    FARADAY,
    KILOWATT,
    REPEATER,
    CHARGER,
    BEVERAGE,
    CW_STRIKE,
    GROUND_ROD,
    TROPO,
]
SKILL_ERR = {"easy": (5.0, 6.0), "fair": (2.0, 2.0), "hard": (0.0, 0.0)}


def buy(game, me, plan=SHOPPING, keep=0):
    """spend station `me`'s money down the shopping list, one lot of each it can afford, the
    shields only when it has none; returns what it bought"""
    got = []
    for item in plan:
        sh, inv, money = game.shop_of(me)
        if item >= GROUND_ROD and sh:
            continue
        if money - keep >= ITEM_PRICES[item] and game.buy(me, item) == 0:
            got.append(item)
    return got


ITEM_PRICES = [0, 60, 35, 45, 30, 40, 15, 30, 55, 35, 20, 25, 30, 70]


def score(before, after, me):
    """what a shot did, from `me`'s side: damage to the other station less twice our own, and a
    big bonus for taking it off the air while we stay on"""
    them = 1 - me
    dealt = before.p[them].hp - after.p[them].hp
    took = before.p[me].hp - after.p[me].hp
    s = dealt - 2 * took
    if after.p[them].hp == 0 and after.p[me].hp > 0:
        s += 1000
    return s


class Aimer:
    """aim(game, me) -> (angle, power, item). It searches the angles toward the other station
    and every power on copies of the game, refines the best few, and of those keeps the aim that
    still hits when it is off by its own error (the mean over the angles and powers around it).
    Then it tries each weapon it holds near that aim. skill easy / fair / hard sets how far it
    misses on purpose (degrees and power points, Gaussian); seed makes it repeatable."""

    def __init__(self, skill="fair", seed=None, coarse=(4, 5)):
        self.err = SKILL_ERR[skill]
        self.rng = random.Random(seed)
        self.coarse = coarse
        self.last = None

    def _try(self, game, me, a, pw, w, memo):
        a, pw = max(0, min(180, a)), max(0, min(100, pw))
        k = (a, pw, w)
        if k not in memo:
            c = game.clone()
            before = c.state()
            c.shot(a, pw, w)
            memo[k] = score(before, c.state(), me)
        return memo[k]

    def _robust(self, game, me, a, pw, w, memo):
        ka, kp = (max(1, int(round(e))) for e in self.err)
        vals = [self._try(game, me, a + i, pw + j, w, memo)
                for i in range(-ka, ka + 1, max(1, ka // 2)) for j in range(-kp, kp + 1, max(1, kp // 2))]
        return sum(vals) / len(vals)

    def best(self, game, me):
        s = game.state()
        right = s.p[1 - me].x > s.p[me].x
        da, dp = self.coarse
        memo = {}
        angles = range(5, 90, da) if right else range(91, 176, da)
        tries = sorted(((self._try(game, me, a, pw, 0, memo), a, pw) for a in angles
                        for pw in range(15, 101, dp)), reverse=True)
        for _, a0, p0 in tries[:3]:
            for a in range(a0 - da + 1, a0 + da):
                for pw in range(p0 - dp + 1, p0 + dp):
                    self._try(game, me, a, pw, 0, memo)
        top = sorted(((v, k) for k, v in memo.items() if k[2] == 0 and v > 0), reverse=True)[:8]
        if not top:
            v, a, pw = tries[0]
            return a, pw, 0, v
        rob = sorted(((self._robust(game, me, a, pw, 0, memo), a, pw) for _, (a, pw, _) in top), reverse=True)
        v0, a0, p0 = rob[0]
        best, item = rob[0], 0
        for w in range(1, 12):
            if w == COAX or not s.p[me].inv[w]:
                continue
            for a in range(a0 - 6, a0 + 7, 2):
                for pw in range(p0 - 10, p0 + 11, 2):
                    if self._try(game, me, a, pw, w, memo) > best[0] + 5:
                        v = self._robust(game, me, a, pw, w, memo)
                        if v > best[0] + 5:       # a weapon only when it does clearly better
                            best, item = (v, max(0, min(180, a)), max(0, min(100, pw))), w
        return best[1], best[2], item, best[0]

    def __call__(self, game, me):
        a, pw, item, v = self.best(game, me)
        ea, ep = self.err
        if ea:
            a = int(round(a + self.rng.gauss(0, ea)))
            pw = int(round(pw + self.rng.gauss(0, ep)))
        a, pw = max(0, min(180, a)), max(0, min(100, pw))
        self.last = (a, pw, item, v)
        return a, pw, item


class Peer:
    """The other radio, as a program: it follows GROUNDWAVE's protocol from the packets it hears
    and answers with packets of its own. send(pkt, reliable) is how it transmits (whitened
    packets); hear(pkt) takes what it receives. It aims with aim(game, me) -> (angle, power,
    item) and shops with shop(game, me) (default: nothing); call() starts a game as the caller."""

    def __init__(self, send, aim=None, me=1, shop=None):
        self.send, self.me, self.aim = send, me, aim or (lambda g, me: (135 if me else 45, 70, 0))
        self.shop = shop
        self.seq, self.peer_seq, self.gid, self.game = 0, None, 0, None
        self.got_shop, self.sent_shop, self.log, self.done = False, False, [], False
        self.desync = 0

    def _pkt(self, t, body=b"", h=0):
        if t != ACK:
            self.seq = (self.seq + 1) & 0xFF
        s = self.game.state() if self.game else None
        return build(t, self.gid, self.seq if t != ACK else 0, self.me, s.round if s else 0,
                     s.turn if s else 0, h, body)

    def call(self, seed=0x1234567, rounds=3):
        self.me, self.gid = 0, (seed >> 8 | 1) & 0xFFFF
        self.game = Game(seed, rounds)
        self.send(self._pkt(HELLO, struct.pack("<IB", seed, rounds)), True)

    def _shop(self):
        if self.shop:
            self.shop(self.game, self.me)
        sh, inv, money = self.game.shop_of(self.me)
        self.sent_shop = True
        self.send(self._pkt(SHOP, shop_body(sh, inv, money)), True)
        self._maybe_round()

    def _maybe_round(self):
        if self.sent_shop and self.got_shop:
            self.sent_shop = self.got_shop = False
            self.game.new_round()
            self._maybe_move()

    def _maybe_move(self):
        s = self.game.state()
        if s.over or s.mover != self.me:
            return
        a, pw, w = self.aim(self.game, self.me)
        h = self.game.hash()
        self.send(self._pkt(MOVE, bytes([a, pw, w]), h), True)
        self.game.shot(a, pw, w)
        self._after_shot()

    def _after_shot(self):
        s = self.game.state()
        if s.over == 2:
            self.done = True
        elif s.over == 1:
            self._shop()
        else:
            self._maybe_move()

    def hear(self, pkt):
        d = parse(pkt)
        if not d:
            return None
        t = d["type"]
        self.log.append(d)
        if t == ACK:
            return d
        if t != HELLO and d["gid"] != self.gid:
            return d
        self.send(build(ACK, d["gid"] if t == HELLO else self.gid, 0, self.me, 0, 0, 0,
                        bytes([d["seq"], t])), False)
        if d["seq"] == self.peer_seq:
            return d
        self.peer_seq = d["seq"]
        if t == HELLO and self.game is None:
            seed, rounds = struct.unpack("<IB", d["body"][:5])
            self.me, self.gid, self.game = 1, d["gid"], Game(seed, rounds)
            self.send(self._pkt(JOIN), True)
            self._shop()
        elif t == JOIN:
            self._shop()
        elif t == SHOP:
            self.game.set_shop(d["from"], *body_shop(d["body"]))
            self.got_shop = True
            self._maybe_round()
        elif t == MOVE:
            if d["hash"] and d["hash"] != self.game.hash():
                self.desync += 1
            b = d["body"]
            self.game.shot(b[0], b[1], b[2])
            self._after_shot()
        elif t == BYE:
            self.done = True
        return d


# ---- when the PC's packets go on air ----

class Link:
    """The PC's side of the air, as gw.c's overs and tests/test_k6_gw.py's model run it: one
    packet an over (sent twice, then the CW ID); ACKs go before anything else; a reliable packet
    waits for its ACK and goes again after RETRY_S, at most TRIES times; nothing goes while the
    channel is busy or until QUIET_S after the last packet heard (the K6's over still has its ID
    to send and its key-down). heard() takes every decoded packet with its time; the two copies
    of one over, and the PC's own packets heard back, are dropped. due(t, busy) gives the next
    packet to send, or None; sent(t) says it went."""
    QUIET_S = 3.5
    RETRY_S = 14.0
    TRIES = 8
    COPY_S = 3.0

    def __init__(self, peer):
        self.peer = peer
        peer.send = self._queue
        self.acks, self.queue = [], []
        self.pending = None                     # (seq, t) of the reliable packet on the air
        self.t_heard = -1e9
        self.t_sent = -1e9
        self.recent = []                        # (t, bytes) heard, for the copies
        self.mine = []                          # bytes sent, to drop our own echo
        self.log = []                           # (t, "rx" / "tx", parsed)

    def _queue(self, pkt, reliable):
        (self.queue if reliable else self.acks).append([pkt, reliable, 0])

    def heard(self, pkt, t):
        pkt = bytes(pkt)
        if pkt in self.mine:
            return None
        self.recent = [(u, b) for u, b in self.recent if t - u < self.COPY_S]
        if any(b == pkt for _, b in self.recent):
            self.t_heard = max(self.t_heard, t)
            return None
        d = parse(pkt)
        if d is None:
            return None
        self.recent.append((t, pkt))
        self.t_heard = max(self.t_heard, t)
        self.log.append((t, "rx", d))
        if (d["type"] == ACK and self.pending and self.queue and d["body"][0] == self.pending[0]
                and d["body"][1] == parse(self.queue[0][0])["type"]):
            self.queue.pop(0)
            self.pending = None
        self.peer.hear(pkt)
        return d

    def due(self, t, busy=False):
        if busy or t - self.t_heard < self.QUIET_S:
            return None
        if self.pending and t - self.pending[1] >= self.RETRY_S:
            self.pending = None
            if self.queue and self.queue[0][2] >= self.TRIES:
                self.queue.pop(0)               # never ACKed: give up on it
        if self.acks:
            return self.acks[0][0]
        if self.queue and not self.pending:
            return self.queue[0][0]
        return None

    def sent(self, t):
        if self.acks:
            item = self.acks.pop(0)
        else:
            item = self.queue[0]
            self.pending = (parse(item[0])["seq"], t)
        item[2] += 1
        self.mine = (self.mine + [item[0]])[-16:]
        self.t_sent = t
        self.log.append((t, "tx", parse(item[0])))

    def idle(self):
        return not self.acks and not self.queue
