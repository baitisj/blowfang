"""[S12] Cycle bench for the UV-21 Pro's sotto 1200 receive path (AT32F421: Cortex-M4 at 120 MHz,
no FPU, flash at 3 wait states behind a prefetch buffer).

Usage: python radios/uv21/sotto/bench_uv21.py radios/uv21/build/sotto/bench.elf [--frames N] [--seed S]
       [--profile L|XL] [--lose 3,17] [--store BYTES] [--ws N] [--data-ws N] [--pcm OUT.raw] [--json OUT.json]

Build the ELF first: sh radios/uv21/sotto/build.sh. N random sotto 1200 frames (any 48 bits
decode; tests/test_k6_pager.py's frames_of) go into 0xA9 voice packets (modem/pager.py, profile
XL by default, as the K6 pager receives them), the --lose packets are dropped, and on the
emulated Cortex-M4: sp_cb_setup (the codebook image in the stand-in SPI flash), sp_dec_pool and
sp_init in a block as the firmware's arena would give them, sp_packet for each packet, then
sp_play_start and sp_play_frame (20 ms, 160 samples at 8 kHz, a call; a 40 ms slot is two calls)
to the end, with the decoder's yield hook on sp_yield_probe. It reports the cycles per call and
per slot against the 120 MHz budget (4.8 M cycles for 40 ms), the cycles per packet, the stack,
the decoder's pool and the longest stretch between yields (the firmware empties the RF chip's
8-word FSK FIFO there: 71 ms of air at 1800 baud); --pcm writes the int16
samples, which must equal c/k6/pager_host_lr's for the same packets (the K6 plays the same).

Timing model: the Cortex-M4 TRM timings of c/fixed/cm4/run.py, instruction by instruction, plus
two flash costs. --ws N: the Cortex-M0 bench's prefetch model (c/fixed/cm0/run.py) for fetches, a
basic block costing the longer of its core cycles and 1 + N cycles a 32-bit word, and N more for a
taken branch's refill. --data-ws N: cycles added to every data read from flash (tables, literal
pools). The defaults (0 and 2) are calibrated on the radio by radios/uv21/mod/wsprobe.c (job
s12ws1, 2026-11-28, port 21 at 120 MHz, FLASH_PSR reads 0x01f3): the port's own crc16 loop ran
86,059 cycles from flash, under the 103,451 this model gives it with no wait states (and the
249,892 it gives at --ws 3, the 3 wait states FLASH_PSR is set to), so the AT32F421's prefetch
hides the fetch wait states; word reads from flash cost 5,677 cycles a 4 KB pass against 4,111
from RAM, 1.5 cycles more a read. --ws 3 --data-ws 3 is the bound with no prefetch at all. The
SPI reads of the codebook (three 20-byte vectors a slot) aren't modelled. Treat it as +-25 %.
Needs: pip install unicorn capstone numpy.  SPDX-License-Identifier: MIT
"""
import argparse
import importlib.util
import json
import os
import sys

import numpy as np
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
_spec = importlib.util.spec_from_file_location('cm4_run', os.path.join(ROOT, 'c', 'fixed', 'cm4', 'run.py'))
cm4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm4)

WS = 0                    # fetch wait states the prefetch leaves (wsprobe: none)
DATA_WS = 2               # cycles a data read from flash adds (wsprobe: 1.5)
MHZ = 120
BUDGET_40MS = MHZ * 1000 * 40     # cycles in one sotto 1200 slot
SP_PLAY_WAIT = 2


class AT32(cm4.M4):
    """the Cortex-M4 bench's emulator and instruction timings, with a prefetch-buffer flash"""

    def __init__(self, elf, ws=WS, data_ws=DATA_WS):
        self.ws, self.data_ws = ws, data_ws
        super().__init__(elf)
        self.mu.hook_add(UC_HOOK_MEM_READ, self._flash_read, begin=cm4.FLASH, end=cm4.FLASH + len(self.img))

    def _flash_read(self, uc, acc, addr, size, val, ud):
        self.cyc += self.data_ws

    def _cost(self, addr, size):
        c = self.blocks.get(addr)
        if c is None:
            core, prevmem, last = 0, False, None
            for ins in self.md.disasm(bytes(self.mu.mem_read(addr, size)), addr):
                k, prevmem = cm4.insn_cycles(ins.mnemonic, ins.op_str, prevmem)
                core += k
                last = ins
            words = ((addr + size - 1) >> 2) - (addr >> 2) + 1
            cond = last is not None and cm4.is_cond_branch(last.mnemonic)
            c = self.blocks[addr] = (max(core, words * (1 + self.ws)) if self.ws else core, addr + size, cond)
        return c

    def _block(self, uc, addr, size, ud):
        cyc, end, cond = self._cost(addr, size)
        if self.prev_end is not None and addr != self.prev_end:
            cyc += self.ws + (2 if self.prev_cond else 0)   # taken branch: refill (+2 for a conditional)
        self.cyc += cyc
        self.prev_end, self.prev_cond = end, cond


def frames_of(n, seed=7):
    """n random-looking 6-byte frames, as tests/test_k6_pager.py makes them"""
    rng = np.random.default_rng(seed)
    return [bytes(rng.integers(0, 256, 6, dtype=np.uint8)) for _ in range(n)]


def packets_of(frames, profile='XL', msg_id=9):
    sys.path.insert(0, ROOT)
    from modem import pager
    return pager.profile(profile, '1200').pack(b''.join(frames), msg_id=msg_id)


def mhz(c):
    return round(c * 50 / 1e6, 2)


def run(elf, frames=70, seed=7, profile='XL', lose=(), store=6144, ws=WS, data_ws=DATA_WS):
    """(report, pcm bytes): one message through the emulated receive path"""
    m = AT32(elf, ws, data_ws)
    io = cm4.IO
    blk, pkt, pcm_at = io + 0x10000, io + 0x100, io + 0x2000
    fr = frames_of(frames, seed)
    all_pk = packets_of(fr, profile)
    lose = set(lose)
    packets = [p for i, p in enumerate(all_pk) if i not in lose]
    r, _ = m.call('sp_cb_setup')
    assert r > 0, f'sp_cb_setup {r}'
    need, _ = m.call('sp_dec_need')
    m.call('sp_dec_pool', blk + store, need)
    nmax, _ = m.call('sp_init', blk, store)
    pc = []
    for p in packets:
        m.mu.mem_write(pkt, p)
        r, c = m.call('sp_packet', pkt, len(p))
        assert r >= 0, f'sp_packet {r}'
        pc.append(c)
    m.call('sp_play_start')
    m.paint_stack()
    # the decoder's yield hook (FX_YIELD_HOOK) set to sp_yield_probe, as the firmware will set it
    # to empty the RF chip's FIFO: the longest stretch of decoding between two yields
    gaps, last = [], [0]
    probe = m.sym['sp_yield_probe'] & ~1

    def on_yield(uc, addr, size, ud):
        gaps.append(m.cyc - last[0])
        last[0] = m.cyc
    m.mu.hook_add(UC_HOOK_CODE, on_yield, begin=probe, end=probe + 1)
    m.call('sp_set_yield', m.sym['sp_yield_probe'] | 1)
    fc, out = [], bytearray()
    while True:
        last[0] = 0                               # call() counts cycles from 0
        r, c = m.call('sp_play_frame', pcm_at)
        gaps.append(c - last[0])                  # the last yield to the end of the call
        if r == 0:
            break
        assert r != SP_PLAY_WAIT, 'SP_PLAY_WAIT with the whole message in'
        fc.append(c)
        out += m.mu.mem_read(pcm_at, 320)
    slots = [fc[i] + fc[i + 1] for i in range(0, len(fc) - 1, 2)]
    worst_slot = max(slots)
    res = {
        'frames': frames, 'packets': len(packets), 'lost': sorted(lose), 'store_frames': nmax,
        'flash_ws': ws, 'flash_data_ws': data_ws, 'cpu_mhz': MHZ, 'calls': len(fc),
        'decode_cycles': {'mean': round(sum(fc) / len(fc)), 'worst': max(fc), 'worst_call': fc.index(max(fc))},
        'slot_cycles_worst': worst_slot, 'budget_cycles_40ms': BUDGET_40MS,
        'load_pct_worst': round(100 * worst_slot / BUDGET_40MS, 2),
        'mhz_for_real_time': {'mean': mhz(sum(fc) / len(fc)), 'worst': mhz(max(fc))},
        'packet_cycles': {'mean': round(sum(pc) / len(pc)), 'worst': max(pc)},
        'stack_bytes': m.stack_used(), 'static_ram_bytes': m.call('sp_static_bytes')[0],
        'dec_pool_bytes': need,
        'yield_gap_us': round(max(gaps) / MHZ, 1),
    }
    return res, bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('elf')
    ap.add_argument('--frames', type=int, default=70)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--profile', choices=('L', 'XL'), default='XL')
    ap.add_argument('--lose', default='')
    ap.add_argument('--store', type=int, default=6144)
    ap.add_argument('--ws', type=int, default=WS)
    ap.add_argument('--data-ws', type=int, default=DATA_WS)
    ap.add_argument('--pcm')
    ap.add_argument('--json')
    a = ap.parse_args()
    lose = [int(x) for x in a.lose.split(',') if x]
    res, pcm = run(a.elf, a.frames, a.seed, a.profile, lose, a.store, a.ws, a.data_ws)
    if a.pcm:
        open(a.pcm, 'wb').write(pcm)
    js = json.dumps(res, indent=1)
    print(js)
    if a.json:
        open(a.json, 'w').write(js + '\n')


if __name__ == '__main__':
    main()
