# GROUNDWAVE: an artillery game for the K6

GROUNDWAVE (2026-11-09, Jeff's idea) is a turn-based artillery game in the classic genre:
two stations on a hilly field take turns lobbing shots at each other, with wind, craters and a
shop between rounds. Its look, terrain, field and weapons are its own, and every item is a
radio joke. It runs as a RAM module (`c/k6/mod/gw.c`, level 6, the LED at 7) and can be played
two ways:

- **Hot seat**: one radio, passed between two players.
- **Radio**: two K6s, each running the module, on 440.850 MHz. The radios swap moves over
  the FD6818's own FSK modem, and each one works out the flight itself.

| File | What it is |
|---|---|
| `c/k6/mod/gw_game.h` | The rules: terrain, flight, weapons, shields, money. Pure C with integer arithmetic only. |
| `c/k6/mod/gw.c` | The module: keys, screens and play over the air. |
| `c/k6/mod/gw_auto.c` | Bot build: the radio plays a hot-seat game against itself (bench, tests). |
| `c/k6/mod/gw_call.c` | Bot build that calls over the radio. |
| `c/k6/mod/gw_answer.c` | Bot build that answers a call. |
| `c/k6/gw_host.c` | The same rules built for the PC, for the tests and a PC opponent. |
| `modem/k6gw.py` | Packets, the rules through ctypes, `Peer` (the protocol), `Aimer` and `buy` (a PC opponent worth playing), and `Link` (when the PC's packets go on air). |
| `tools/k6_gw.py` | `decode` a recording; `play` the K6 from the PC over the IC-7100; `sim` the PC against itself. |
| `tests/test_k6_gw.py` | Host rules, hot seat, and radio play against `Peer` over a modelled FSK modem. |

## The field and the screen

The field is the level-6 bitmap: 128 x 30 pixels, between text lines 0-1 and 5. Line 0 shows
both stations' health and the round, and a `>` marks whose turn it is. Line 1 shows the angle,
the power and the wind. Line 5 shows the item in hand and how many are left, or a message.

Each round makes a new field: midpoint-displacement hills, with the two stations on flat pads,
P1 on the left third and P2 on the right. A shack with a ground-plane mast is P1, an open shack
under a dipole is P2. A station that is knocked off the air becomes a heap of rubble.

Flights are drawn as they happen, with a dotted trail, a 2 x 2 shot, and a caret on the top row
while a shot is above the screen. A blast shows a disc and then a crater, and the dirt above
the crater falls in. A station whose ground goes falls with it and takes damage (2 points per
pixel past the first).

## Sound

The shots make sound on the speaker (Jeff, 2026-11-10). A shot coming down whistles quietly,
falling in pitch as it falls faster: about 3.3 kHz at the top of the arc and 1 kHz after about
2 s of fall. Each blast is 150 ms of noise that starts as a loud crackle of dirt (1-16 kHz) and
sinks into a quieter rumble (about 200 Hz). The rising half of a flight is silent.

It uses the speaker path that midi.c and spksweep.c found (PA11 = TIM1_CH4 through two RC
poles into the amplifier, PB2 the amplifier's switch), but not their 16 kHz samples. Instead,
TIM1 counts at 1 MHz and makes a pulse wave at the pitch itself: ARR is the period in
microseconds, and CCR4 is the high time (1/32 of the period for the whistle, 1/4 to 1/16 for a
blast). The timer runs on its own, so the module can keep its usual waits. Between sounds the
pulse wave sits at 31 kHz with the whistle's 1/32 duty, which is inaudible and keeps the DC
level steady, so a whistle starts without a click.

`fly()` turns the sound on before each shot and off after it. Turning it on mutes the FD6818's
AF output (REG_47 AF 0), waits 30 ms for the filter, and then turns the amplifier on. Turning it
off waits 20 ms. It then puts back TIM1's CCMR2, CCER and BDTR, PA11's mode and alternate
function, TIM1's clock, the amplifier and REG_47 as it found them. PSC and ARR go back to the
voice prompts' 48 kHz carrier. If TIM1 is already running, the shot is silent. Nothing is
played during an over: shots never fly while the radio is keyed.

## Keys

The module scans the keypad matrix itself, as surf.c does.

| Keys | Action |
|---|---|
| 4 6, or the side keys | Angle (hold to repeat) |
| 1 3 | Angle, 10 at a time |
| 2 8, or UP DOWN | Power |
| 9 7 | Power, 10 at a time (9 up, 7 down: up on the right) |
| * # | Previous or next item held |
| MENU or 5 | Fire |
| EXIT twice | Leave the game (from the title screen, leave the module) |
| Shop: UP DOWN (2 8) | Pick an item |
| Shop: MENU (5) | Buy |
| Shop: EXIT | Done |

Title screen: 1 hot seat, 2 radio, 3 help, EXIT quit.

## Weapons and shields

Everyone starts with $80 and unlimited QRP Shots.

- **Pay per round**: $30, plus the damage you dealt, plus $40 for winning it.
- **Power**: 0 to 100. Wind changes from turn to turn, by up to 9 either way.

| Item | $ | Qty | What it does |
|---|---|---|---|
| QRP Shot | free | ∞ | 5 W: a small blast (radius 3) |
| Kilowatt | 60 | 1 | QRO: a big blast (radius 8, 80 damage) |
| FCC Violator | 35 | 2 | Splatter: the widest blast (radius 11), but its power stops at 55, so it can't reach far |
| 12V Charger | 45 | 2 | Throws sparks that land around it and blast again (the funky bomb) |
| CW Strike | 30 | 3 | Ignores the wind, and a direct hit does 100 damage, but the blast is tiny (radius 2) |
| Beverage | 40 | 2 | A strong, wide blast (65 damage), but it can't rise above a ceiling (the dotted row while aiming) |
| The Lid | 15 | 3 | Wanders off its path, a different way each time |
| Skip Zone | 30 | 3 | Skips off the ground before it blasts |
| Repeater | 55 | 1 | Kerchunk: splits into three at the top of its arc |
| Yagi Beam | 35 | 2 | A straight line, with no gravity and no wind; it fizzles after 70 steps |
| Coax Spool | 20 | 2 | Does no damage, but piles up a mound of dirt (a wall, or a burial) |
| Tropo Duct | 25 | 3 | Half gravity: it flies far |
| Ground Rod | 30 | shield | Soaks up half of each hit until its 60 points are gone, and nudges shots a little |
| Faraday Cage | 70 | shield | Bends shots away (radius 12) and soaks up all of a hit until its 100 points are gone |

Shields work like the classic magnetic shield. A shot inside the shield's radius is pushed away
from the station (but not its owner's own shot), and a blast's damage comes off the shield's
energy first. A station can hold one shield at a time. The dome is drawn dotted while it has
energy.

A game is best of 3 rounds (`GW_ROUNDS`). If both stations go off the air together, the round
is a draw.

## Radio play

In radio play, one radio calls (MENU in the lobby) and the other answers (MENU when it shows
"AG7EW calls!"). The caller picks the seed, which fixes every field and wind of the game.

1. Both stations shop, and swap their shops in SHOP packets.
2. Both build the round's field.
3. In turn, the station to move sends a MOVE (angle, power, item, and its game hash), and both
   radios fly the same shot.

Every packet except an ACK is acknowledged. The sender tries 4 times, with a random pause
between tries. A repeated packet (same seq) gets its ACK again and is otherwise dropped. If a
MOVE's hash doesn't match the receiver's, line 5 shows DESYNC, and the move is still played.

Each over follows the same steps:

1. Wait for a clear channel: below -90 dBm for 500 ms, giving up after 15 s.
2. Key up with `tx_on`, which works only on the pager's frequency at low power.
3. After a 300 ms lead, send the packet twice on the pager's PHY F (FFSK 1200/1800 Hz at 1800
   baud, the registers of aura-pager.patch).
4. Send AG7EW in MCW (800 Hz, 25 wpm) on the tone oscillator.
5. Key down and listen again.

That is about 3.7 s keyed, plus the time `tx_on` takes. A key pressed during an over keys down
at once. The module refuses radio play when the pager isn't on 440.850 ("radio: 440.850 only").

Packets are 64 bytes, PN9-whitened as the pager's (magic 0xA6, which no other packet uses):

```
0 0xA6   1 type (1 HELLO 2 JOIN 3 SHOP 4 MOVE 5 ACK 6 BYE)   2-3 game id (LE)   4 seq
5 from (station 0/1)   6 round   7 turn   8-11 sender's gw_hash before its action (LE; 0: no check)
12..  HELLO: seed (4, LE), rounds | SHOP: shield, inv[14], money (2, LE) |
      MOVE: angle, power, item | ACK: the seq and type it answers
46-51 "AG7EW"   62-63 CRC-16/CCITT-FALSE of bytes 0-61, big-endian
```

The answerer plays the caller's number of rounds (HELLO byte 16). The rules are integer-only,
and the bot never touches the game's random generator, so the two radios stay in step to the
bit. `modem/k6gw.py`'s `Game` is the same C file built for the PC, so a PC opponent stays in
step too. `test_radio_game_against_the_pc` checks the hashes every turn.

## Memory

| Build | Code (CI's ATfE 22.1 clang, -Oz) |
|---|---|
| gw | 11,739 B |
| gw_auto | 11,365 B |
| gw_call | 11,245 B |
| gw_answer | 11,301 B |

The block holds 11,752 B of code, so gw has 13 B to spare: the game is full. The sound took
about 400 B. To make room, the items' names and shop lines moved into one string (`GW_TEXT`
in gw.c) instead of fixed-size fields. Ubuntu's clang 18 builds gw about 400 B bigger (12,1xx B),
so `test_builds_and_fits[gw]` passes only with CI's compiler (docs/k6-compilers.md). The state is about 1.2 KB: the game (392 B), the 480-byte
bitmap, three packet buffers, and the sound's 32 B of saved registers. The trail dots share the receive and transmit buffers, since
nothing is sent or received while a shot flies.

The code leaves less scratch than the state needs, so `k6_main` puts the state on the stack.
The emulator measures 1.8 KB of stack at the deepest point. The voice SPI images give a module
about 3.1 KB (the vlrh image's 3,112 B, docs/k6-modules.md), which leaves about 1.3 KB for
Aura's own calls. The tests fail above 2,000 B. On the bench, cf9516e vlrh gave the module 3,044 B and
684 B were never touched: Aura's calls under the module took about 580 B.

The game needs a voice SPI image (its 11,776-byte block). The EXPLORE and lean images' 5,376 B
are too small.

## Tests

`python -m pytest -q tests/test_k6_gw.py` (about 30 s with `-n 4`) covers:

- **The rules on the PC**: the same seed and moves give the same game, and each weapon does its
  trick (the Beverage stays under its ceiling, the FCC Violator flies short, the Yagi flies
  straight, the Lid wanders, the Coax piles dirt). It also checks shields, money, hits and the
  end of a game.
- **Hot seat**: the bot plays a whole game on the screen and never transmits. A person's keys
  can shop, aim and fire.
- **Radio**: `gw_call` and `gw_answer` each play a whole game against `k6gw.Peer`, over a model
  of the FD6818's FSK modem. The model covers REG_59 control, the 8-word FIFO and its interrupts,
  the RSSI, and the other radio's overs. The runs are played with one copy lost, and with a
  whole over lost each way, so both sides have to repeat. The hashes stay in step.
- **Sound**: the bot's hot-seat game against a register model of TIM1, RCC and GPIOA. Each
  fall whistles with a rising period (falling pitch), and each blast is 150 noise steps from high
  and loud to low and quiet. After every shot TIM1, the pins, the amplifier and REG_47 are as
  they were.
- **The TX rules** (test_k6_txrules.py's, here with the modem model): `tx_on` only on 440.850,
  never on a busy channel, only FSK and tone registers written while keyed, every over ending
  in AG7EW, and a key ending an over with the modem quiet.

## On the bench

Use RAM modules (no flash) on a voice SPI image (vlrh or similar) at level 6 or above. Upload
with `tools/k6_upload.py mod-gw_auto.bin --api-level 6 --telemetry 0 --run-s 150`. Telemetry
has to be 0: its 696 B at the top of the block don't fit beside the code.

1. **Hot seat, no TX**: `mod-gw_auto.bin` plays itself and returns. Check the screen, the
   `gw shot` and `gw end` lines, and `stack_free=` in `K6 MOD ret` (it should be over 512 B).
2. **Hot seat by hand**: `mod-gw.bin`, key 1, play a round.
3. **A few overs on the air**: `mod-gw_call.bin` on 440.850 calls with up to 4 HELLO overs,
   about 12-14 s apart, and quits when nothing answers (EXIT stops it sooner). A busy channel
   doesn't count as a try. Record the overs on the IC-7100 (receive only) with
   `k6_upload.py --keep RUN.raw`; each over is two FSK packets, then AG7EW in MCW.
   `python3 tools/k6_gw.py decode RUN.raw` reads the packets back.
4. **Two radios**: a second K6, or the PC on the IC-7100 (`tools/k6_gw.py play`, below).

**Bench run 1** (2026-11-09 ~06:50Z, image cf9516e vlrh, CI modules from e82a129, IC-7100 at RF
POWER 0 receiving only):

- **gw_auto**: 91 chunks on the first send. `code=10971 B stack=3044 B scratch=780 B`, which
  means CI's clang builds about 330 B smaller than clang 18 here. It played a 3-round game, 26
  shots about 3 s apart, P1 winning 2-0 (`gw end 0`). `ret=2 in 71214 ms stack_free=684 B`.
  There was no fault and no TXDOG line, and the K6 never keyed.
- **gw_call**: the first two HELLOs said `r=-2`. The module started while the upload's second
  copy was still on the air, so its clear-channel wait held off, as it should, but each wait
  counted as a try. Only 2 of the 4 tries reached the air, keyed 3.81 s and 4.01 s, 13 s apart,
  on 440.850 (`tx on hz=440850000 max=8000 ms`). `ret=0 stack_free=684 B`, then the core's
  report as usual.
- **Chip FSK from a module's own `tx_on` works**: `k6_gw.py decode` read the HELLO (gid bfdd,
  seed and rounds intact, call AG7EW) from both overs. Over 3 gave 1 of its 2 copies and over 4
  gave both.
- **Fixed since**: a busy channel no longer counts as a try, and the lead before the first
  packet is now 500 ms instead of 300 ms (300 ms may have clipped over 3's first copy).
- **Still to see**: a bot that varies its angle. The bots aim at 45° and 135° and learn only
  the power.

## Playing the PC

`python3 tools/k6_gw.py play` is a PC opponent on the IC-7100 (2026-11-09, from the thread
clean-up). Start `mod-gw.bin` on the K6, pick 2 (radio), then run `play` on hamlet and call from
the K6 with MENU. `play --call` makes the PC call instead, and the K6 answers.

- **It aims by trying.** `k6gw.Aimer` fires every angle toward the K6 at every power on a copy of
  the game (the same C rules, so the copy shows exactly what the shot will do), refines the best
  few, and keeps the aim that still hits when it is a little off. Then it tries each weapon it
  holds near that aim, and uses one when it clearly does better. About 50 ms a move.
- **Skill** is how far it misses on purpose: `--skill easy` (about 5 degrees and 6 power points),
  `fair` (2 and 2, the default), `hard` (none). Hard won every game against easy in the tests,
  and fair won 6 or more of 8 against the bots' fixed 45/135 degree shot.
- **It shops** (`k6gw.buy`): a Faraday Cage first, then the big weapons, down a fixed list.
  `--no-shop` buys nothing.
- **On the air** (`k6gw.Link`, the same rules as gw.c and the test model): one packet an over,
  sent twice, then AG7EW in MCW at 25 wpm. ACKs go first. Nothing goes while the S-meter reads
  busy (`--busy-db`, -40 dB over S9) or for 3.5 s after the K6's last packet (its ID and key-down
  are still to come). A packet waits for its ACK and goes again after 14 s, at most 8 times.
  Every over goes through `tools/k6_bench.py`: the PTT lock, the bench owner and 70 cm. `play`
  refuses unless the IC-7100 reads 440.850.
- **What it keeps**: `$BLOWFANG_WORK/runs/gw-play-<UTC>/` holds the receive recording
  (`rx.raw`), every over it sent as a WAV, and `result.txt` (the score and every packet each way).
- `python3 tools/k6_gw.py sim --skill hard` plays the PC against a second PC through two Links,
  with no radio, to see a game and its timing.

Tests: `tests/test_k6_gw_pc.py` (aiming, skill order, shopping, Link's timing and resends, an
over decoding as the K6 hears it, whole games through Links) and
`test_radio_game_against_the_pc_opponent` in `tests/test_k6_gw.py`: the hard Aimer with its
shopping against `gw_answer` on the emulated M0, hashes in step, the PC winning the round.

**The pattern for other turn games** (insight 47): both sides run the same deterministic rules,
here one C header built for the M0 and the PC, and only the moves cross the air, each with the
sender's game hash so a desync shows at once. A PC side is then three parts: the rules through
ctypes, a player that tries moves on copies of the game (`Aimer`), and `Link` for the overs.
Chess or battleship as RAM modules would reuse `Link` and `tools/k6_gw.py`'s `play` loop as
they are, with a new packet type and rules header.
