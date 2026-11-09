"""GROUNDWAVE's PC opponent (modem/k6gw.py: Aimer, buy, Link; tools/k6_gw.py play), on the host:
it aims well enough to win, skill levels order, it shops within the rules, and Link puts its
packets on the air as gw.c's overs expect. tests/test_k6_gw.py plays it against the radio's own
module on the emulated M0. SPDX-License-Identifier: MIT"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import k6gw  # noqa: E402


def duel(aim_a, aim_b, seed, shop_a=None, shop_b=None, rounds=3):
    q = []
    a = k6gw.Peer(lambda p, r: q.append((1, p)), aim=aim_a, shop=shop_a)
    b = k6gw.Peer(lambda p, r: q.append((0, p)), aim=aim_b, shop=shop_b)
    a.call(seed, rounds=rounds)
    n = 0
    while q and n < 10000:
        to, p = q.pop(0)
        (b if to else a).hear(p)
        n += 1
    assert a.done and b.done and a.desync == b.desync == 0
    s = a.game.state()
    return s.p[0].wins, s.p[1].wins


def test_the_aimer_beats_a_fixed_shot():
    wins = [duel(k6gw.Aimer("fair", seed=i), None, 1000 + i, k6gw.buy) for i in range(8)]
    assert sum(w0 > w1 for w0, w1 in wins) >= 6, wins


def test_hard_beats_easy():
    wins = [duel(k6gw.Aimer("easy", seed=i), k6gw.Aimer("hard", seed=i), 2000 + i, k6gw.buy, k6gw.buy)
            for i in range(4)]
    assert sum(w1 > w0 for w0, w1 in wins) >= 3, wins


def test_hard_hits_from_the_first_shot():
    g = k6gw.Game(0x2468ACE, 3)
    g.new_round()
    me = g.state().mover
    a, pw, item, v = k6gw.Aimer("hard").best(g, me)
    before = g.state().p[1 - me].hp
    g.shot(a, pw, item)
    assert g.state().p[1 - me].hp < before and v > 0


def test_buy_keeps_to_the_rules():
    g = k6gw.Game(7, 3)
    assert k6gw.buy(g, 0) == [k6gw.FARADAY]              # $80: the shield, then nothing fits
    sh, inv, money = g.shop_of(0)
    assert sh == k6gw.FARADAY and money == 10 and not any(inv[1:12])
    g.set_shop(1, 0, [0] * k6gw.NI, 300)
    got = k6gw.buy(g, 1)
    sh, inv, money = g.shop_of(1)
    assert got[0] == k6gw.FARADAY and k6gw.GROUND_ROD not in got and money >= 0
    assert money == 300 - sum(k6gw.ITEM_PRICES[i] for i in got)


def pk(t, seq, frm=0, body=b""):
    return k6gw.build(t, 0x1235, seq, frm, 1, 0, 0, body)


class Hush:
    """a Peer that records what it is asked to hear and sends nothing by itself"""

    def __init__(self):
        self.heard, self.send = [], None

    def hear(self, p):
        self.heard.append(k6gw.parse(p))


def test_link_acks_first_and_waits_for_the_k6():
    h = Hush()
    ln = k6gw.Link(h)
    h.send(pk(k6gw.MOVE, 9, 1, bytes([45, 70, 0])), True)
    h.send(pk(k6gw.ACK, 0, 1, bytes([4, k6gw.SHOP])), False)
    assert k6gw.parse(ln.due(10.0))["type"] == k6gw.ACK
    ln.sent(10.0)
    k6 = pk(k6gw.SHOP, 5)
    ln.heard(k6, 20.0)
    ln.heard(k6, 20.4)                                  # the over's second copy
    assert len(h.heard) == 1
    assert ln.due(22.0) is None                         # the K6's ID and key-down
    assert ln.due(24.0, busy=True) is None
    assert k6gw.parse(ln.due(24.0))["type"] == k6gw.MOVE
    ln.sent(24.0)
    assert ln.due(30.0) is None                         # waiting for its ACK
    ln.heard(ln.mine[-1], 30.5)                         # our own over heard back: ignored
    assert len(h.heard) == 1
    ln.heard(pk(k6gw.ACK, 0, 0, bytes([9, k6gw.MOVE])), 31.0)
    assert ln.idle() and ln.due(40.0) is None


def test_link_resends_and_gives_up():
    h = Hush()
    ln = k6gw.Link(h)
    h.send(pk(k6gw.MOVE, 9, 1, bytes([45, 70, 0])), True)
    t, sends = 0.0, 0
    while t < 400:
        if ln.due(t) is not None:
            ln.sent(t)
            sends += 1
        t += 1.0
    assert sends == k6gw.Link.TRIES and ln.idle()
    ts = [u for u, way, _ in ln.log if way == "tx"]
    assert all(b - a >= k6gw.Link.RETRY_S for a, b in zip(ts, ts[1:]))


def test_a_wrong_ack_is_not_taken():
    h = Hush()
    ln = k6gw.Link(h)
    h.send(pk(k6gw.MOVE, 9, 1), True)
    ln.sent(0.0)
    ln.heard(pk(k6gw.ACK, 0, 0, bytes([8, k6gw.MOVE])), 5.0)
    ln.heard(pk(k6gw.ACK, 0, 0, bytes([9, k6gw.SHOP])), 9.0)
    assert not ln.idle()


@pytest.mark.parametrize("skill", ["easy", "hard"])
def test_sim_plays_a_whole_game_over_links(skill, capsys):
    import argparse
    import k6_gw
    a = argparse.Namespace(skill=skill, seed=5, no_shop=False, rounds=1)
    assert k6_gw.cmd_sim(a) == 0
    out = capsys.readouterr().out
    assert "desync 0" in out and out.count("wins 1") == 1, out


def test_an_over_decodes_as_the_k6_would_hear_it():
    import k6_gw
    p = pk(k6gw.MOVE, 7, 1, bytes([45, 70, 3]))
    x = k6_gw.over_audio(p)
    got = k6_gw.decode(x, k6_gw.FS)
    assert [d["raw"] for _, d in got] == [p, p]
    assert 3.5 < len(x) / k6_gw.FS < 5.0
