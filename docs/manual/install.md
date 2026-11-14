# Installing blowfang on your Baofeng UV-K6: the instruction book

This book is for anyone with a Baofeng UV-K6 (or K61) who wants to run blowfang on it. You need
the radio, its USB programming cable and a computer. You don't need a second radio: everything
goes in over the cable, and one script does the work while you watch the radio.

If you just want the commands, they are in [The short version](#the-short-version). Read
[Before you start](#before-you-start) first all the same: one step can't be undone.

## What you get

blowfang replaces the radio's firmware. After the install the radio starts in **EXPLORE**, a
band scanner that finds repeaters and other stations, grades them, listens for their Morse ID
and keeps a table of what it found. On top of that:

- **Modules**: small programs kept in the radio's own flash. **MENU** lists them: an AIS ship
  list and clock, an APRS clock, an alarm clock, a MIDI music player, a Morse trainer, a band
  surfer, pager and weather alert receivers, FT8 and WSPR, and more (the full list is
  [below](#the-modules)).
- **Help cards**: hold **MENU** on any screen to see what its keys do.
- **A station editor**: fix a station's name, call, tones and offset on the radio.
- **Voice messages**: the radio can receive and play short sotto voice messages sent as data.

blowfang is built on Aura, the open firmware for this radio. Your radio's factory calibration
stays where it is.

## Before you start

**Flashing replaces the stock firmware for good.** Baofeng never published its firmware, and
the radio can't give it back once it's overwritten, so there is no going back to the exact
software the radio came with. You can always flash blowfang again, or plain Aura, from the same
cable. Nothing here changes the hardware, and a flash that goes wrong can be redone (see
[When something goes wrong](#when-something-goes-wrong)).

**Only the UV-K6 / K61 with the FD6818 radio chip works.** That is most of them. The installer
checks nothing about this before it flashes, but blowfang shows `RF chip not FD6818` and won't
listen on a radio with the other chip (FD6818B); flash plain Aura's `fd6818b` branch there
instead. The Quansheng UV-K5 and UV-K6 are different radios: this won't run on them.

**You need an amateur radio licence to transmit.** blowfang listens far more than it talks. The
few modules that transmit (FT8 TX, WSPR TX, WSPR BEACON, RANGE TX, CW KEY and GROUNDWAVE) do it
only on 440.850 MHz, and only when you run them. In the US, the 70 cm band and that frequency are for
licensed amateurs only. The install itself never transmits.

**What you need**

- The radio, charged.
- A USB programming cable with the two-pin Kenwood plug, the kind sold for the UV-5R and
  UV-K5. It shows up as a serial port: `/dev/ttyUSB0` on Linux, `/dev/cu.usbserial-...` on a
  Mac, `COM3` or similar on Windows.
- Python 3.8 or newer with two packages: `pip install pyserial numpy`.
- This repository (for `tools/k6_install.py`) and a **release folder**, `blowfang-k6-...`. Get
  the folder from a GitHub release of blowfang when there is one, or from the newest run of the
  *K6 pager firmware* workflow on GitHub (artifact `blowfang-k6-<commit>`), or build it
  yourself ([below](#building-the-release-folder-yourself)).
- About 20 minutes the first time. Updates later take a few minutes and need no hands.

## The short version

```sh
pip install pyserial numpy
git clone https://github.com/baitisj/blowfang
cd blowfang
python3 tools/k6_install.py --port /dev/ttyUSB0 install ~/Downloads/blowfang-k6-abc1234
```

Plug the cable into the radio's two-pin socket (the speaker/mic jack) all the way in. Then follow
what the script says. It will ask you to switch the radio off and on three times, and once to
press **MENU** on the radio. It never asks you to hold keys.

## The long version: what happens, step by step

### 1. Your key

The first time, the script makes a **module key** for you: `~/.config/blowfang/k6-mod.key`, a
random 16-byte number in a file only you can read. It writes this key into each firmware image
on your computer just before flashing it, and it signs every module with it as it sends it. The
radio then runs only modules signed with your key, so nobody else can load code into your radio,
over the cable or over the air.

Keep this file. Back it up with your other private files. If you lose it, nothing breaks, but to
add modules later you'll have to make a new key and reflash (the script does both:
`python3 tools/k6_install.py key`, then `install` again).

Never share the key or an image the script keyed: either one lets someone sign modules for your
radio. The release folder holds no key at all: its images carry a placeholder.

### 2. Finding the radio

The script first knocks on the cable for a few seconds to see whether the radio already runs
blowfang. On a new radio nothing answers, and it says it is treating it as a new radio. It asks
you to type `YES` before it replaces the stock firmware.

### 3. The codebook installer and your backup

blowfang's voice decoder needs its codebooks in the radio's SPI flash (the 2 MB memory chip next
to the processor). Two small installer images put them there, once per radio.

The script says *Switch the radio OFF, then press Enter here and switch it ON (no keys held)*.
Do that. For a moment after power-on the radio's bootloader listens on the cable, and the script
catches it then. About 120 KB go in, in under a minute; the screen may show `UPDATE`
meanwhile. The radio restarts by itself when it's done.

On its first start the radio shows **FORMAT FLASH?**. Don't press anything yet. The script now
reads the whole SPI flash, 2 MB, into a file in `~/blowfang-backups/`. This takes about four minutes.
**Keep that file**: it holds the factory calibration and everything else that was on the chip.

When the script says so, press **MENU** on the radio. It formats the settings area (not the
calibration) and writes sotto's codebook; the script sees the radio's `K6 CB` line and moves on.

### 4. The second codebook installer

Off and on again, the same way. This image writes the codebooks for sotto 600 and 400 (`K6 VLR`).

### 5. blowfang itself

Off and on a third time: now the real image goes in. When it starts, the radio shows
**PAGES MISSING**. That is expected: EXPLORE's code lives in the SPI flash too, and it isn't
there yet.

### 6. Loading the library over the cable

The radio now answers the script on the cable. The screen shows **BLOWFANG / CABLE LOAD...**.
While this screen is up the radio's receiver is off and it can't transmit. The script first
checks that the radio's key is yours, then sends, in this order:

- EXPLORE's four code pages (slots 9-12),
- the help card deck (slot 13),
- the modules (slots 0-8 and 16-31).

Each one is checked by the radio before it is written, and read back after. The script prints a
line for each slot. Then the radio restarts, and you're in **EXPLORE**.

The whole library is about 160 KB (29 images in the first release) and goes in in about 25 seconds,
or about 40 seconds with `--verify`, which reads every slot back.

## Using the radio

The help cards (hold **MENU**) say all of this on the radio itself.

**EXPLORE** starts by itself. It sweeps the bands and lists what it hears, newest above the
line, saved stations below.

| Key | In EXPLORE |
| --- | --- |
| UP / DOWN | pick a row |
| Side key 1 | try: park on the row and listen (Side key 1 or EXIT goes back) |
| 0 | listen for the station's Morse ID |
| 5 | save the station |
| 8 | edit the station |
| * | ignore the row |
| # | next set of bands |
| 1 | auto ID on or off |
| 9 | speaker |
| MENU | the module list |
| EXIT, EXIT | the RAM arena |
| hold MENU | help for this screen; DOWN turns the page |

A row's letter: `R` repeater, `r` maybe a repeater, `?` not sure yet, `s` simplex, `x` ignored.
A check mark means its call was heard; a gull means a birdie (a signal the radio makes itself).

**The module list** (MENU): EXPLORE, ARENA, then every module in the radio. UP/DOWN pick,
MENU or # runs one, EXIT goes back. Running a module from EXPLORE drops EXPLORE's unsaved list;
saved stations stay.

**The station editor** (8 on a row): UP/DOWN pick a field, # opens it, 5 saves, EXIT leaves
(it asks if you changed something). In an open field digits type, letters go by multi-tap (2 is
ABC ... 9 is WXYZ), * deletes, UP/DOWN step a tone or an offset.

**The RAM arena** (EXIT twice, or MENU > ARENA): the radio listens on 440.850 MHz for voice
messages and modules sent over the air. 5 plays the last message, Side key 1 runs the last
module again. After a quiet minute it goes back to EXPLORE.

### The modules

| Slot | On the radio | What it does |
| --- | --- | --- |
| 0 | SURF | band surfing: a bar graph of the band, tune around by ear |
| 1 | AIS SHIPS | the ships near you, from their AIS broadcasts |
| 2 | AIS CLOCK | sets the radio's clock from AIS |
| 3 | APRS CLOCK | sets the clock from APRS on 144.390 MHz |
| 4 | ALARM | an alarm clock that plays a tune at a set time (UTC) |
| 5 | MIDI | plays tunes on the speaker in four voices |
| 6 | MORSE | hears Morse and draws the dits and dahs on a scrolling tape, with the letters |
| 7 | GROUNDWAVE | a turn-based artillery game between two radios on 440.850 (transmits) |
| 8 | STOCK FONT | checks that Baofeng's fonts are still in the SPI flash |
| 16 | CW LISTEN | decodes a repeater's Morse ID |
| 17 | POCSAG | a pager message receiver |
| 18 | DMR RX | DMR details: colour code, slot, talkgroup, IDs |
| 19 | SAME RX | NOAA weather alert headers on 162.550 MHz |
| 20 | FT8 RX | an FT8 receiver |
| 21 | RANGE RX | the range test receiver |
| 22 | RX MAP | measures how well the radio receives across its coverage |
| 23 | WSPR IQ | streams the receiver to a computer that decodes WSPR |
| 24 | STATION LIST | lists EXPLORE's station table on the cable |
| 25 | CLOCK | says the radio's clock |
| 26 | FT8 TX | one FT8 frame on 440.850 (transmits, with a Morse ID) |
| 27 | WSPR TX | WSPR on 440.850 (transmits, with a Morse ID) |
| 28 | WSPR BEACON | six WSPR frames over an hour (transmits, with a Morse ID) |
| 29 | RANGE TX | range test packets on 440.850 (transmits, with a Morse ID) |
| 30 | CW KEY | sends your Morse ID once as true CW on 440.850 (transmits) |

The list the release loads is `tools/k6_release_modules.txt`; the names on the radio come from
the modules themselves. Some modules report on the serial cable only: see `docs/k6-modules.md`
and the module's own doc.

## Updating later

When a new release comes out, put the radio in EXPLORE or the arena, plug in the cable and run
the same command with the new folder:

```sh
python3 tools/k6_install.py --port /dev/ttyUSB0 install ~/Downloads/blowfang-k6-def5678
```

This time the radio answers on the cable, so the script needs no hands: it tells the radio to
restart, catches the bootloader as it does, flashes the new image and loads the library again.
It skips the backup when `~/blowfang-backups/` already holds one, or when you add `--no-backup`.
On the first test radio the whole update, flash and library, took 48 seconds.

If the script says the radio lacks a codebook, it runs blowfang but its codebooks never went in.
Add `--fresh`: the script then installs as on a new radio, with the two codebook installers and
three power-ons.

To reload only the library (say, after an experiment), use `load`; to look at what the radio
holds, `status`:

```sh
python3 tools/k6_install.py --port /dev/ttyUSB0 load ~/Downloads/blowfang-k6-def5678
python3 tools/k6_install.py --port /dev/ttyUSB0 load ~/Downloads/blowfang-k6-def5678 --only 5,13
python3 tools/k6_install.py --port /dev/ttyUSB0 status
```

## When something goes wrong

**The script says the bootloader didn't answer.** Switch the radio off, press Enter, and switch
it on again within ten seconds, without holding any keys. Check the cable is pushed all the way
into the radio. If it still doesn't answer, hold **Side Key 1 and Side Key 2** while you switch
the radio on: the screen shows `UPDATE`, and the bootloader waits there for as long as it takes.
Then run `python3 tools/k6_install.py --port ... flash IMAGE --power` and press Enter.

**The radio's screen stays on UPDATE after a flash**, or it restarts over and over, or it shows
nothing. The flash didn't finish (this happens now and then). It's safe: the bootloader lives in
a part of the chip nothing here writes. Switch the radio off, then flash the same image again
with `flash IMAGE --power`. Pulling the battery in the middle of a flash is safe too: just flash
again.

**The radio shows PAGES MISSING.** EXPLORE's pages aren't in its flash, or they were made for a
different build of the image (every build has its own). Run `load` with the release folder that
matches the image you flashed. The script refuses pages made for another build and says so.

**The script says the radio holds another key than yours.** The image on the radio was keyed with
a different key (another computer, or a key you lost). Flash it again with `flash IMAGE`; the
script keys it with yours. Then `load`.

**Nothing answers on the cable.** The radio must be on, in EXPLORE or the arena (not in a running
module: press EXIT until you're back), and the cable must be the radio's. On Linux, add yourself
to the `dialout` group (or `uucp`) for permission to open the port, then log in again. Close any
other program that has the port open (CHIRP, a terminal).

**The cable is in and the radio keys up.** The two-pin socket's data wire is also the radio's PTT
line. The script sends only short bursts outside a cable session, far too short to key blowfang,
and inside a session the radio ignores PTT. If your radio ever transmits while the script runs,
unplug the cable and tell us (with the script's output).

## Your backup and your calibration

The backup file is the whole SPI flash as it was before the format: the factory calibration,
the stock fonts and whatever else Baofeng left there. Afterwards the radio writes the SPI flash
only in these places: Aura's settings area (its format, then settings as you change them),
EXPLORE's station table (when you save a station), sotto's codebooks (once, by the two
installers, at 0x100000-0x10D320) and the module library (0x110000-0x18FFFF, when you load it).
None of these is the calibration. The script has no restore command; keep the file so the
calibration can be put back by hand if it is ever needed.

You can make a new backup any time: `python3 tools/k6_install.py --port ... backup -o FILE`.

## Building the release folder yourself

```sh
git clone --recurse-submodules https://github.com/baitisj/blowfang
cd blowfang
sh firmware/k6-aura/build.sh            # needs rustup, clang, lld; the voice + EXPLORE image needs Arm Toolchain for Embedded (K6_ATFE)
python3 tools/k6_install.py bundle firmware/k6-aura/out -o blowfang-k6-mine
```

`bundle` refuses an image built with a key (`K6_MOD_KEY_FILE`): a release folder must never hold
one. It checks that the pages were linked against the image, takes the module list from
`tools/k6_release_modules.txt` and writes `manifest.json` with every file's checksum, which
`install` checks before it writes anything. See [`firmware/k6-aura/README.md`](../../firmware/k6-aura/README.md)
for the build itself.

## How the cable load works (for the curious)

The core image goes in through the stock bootloader, which listens on the cable for about 200 ms
after every reset. Everything else goes through blowfang's **cable session**: the script sends
the 16 characters `PROGRAMBF-K6BLWF` while the radio is in EXPLORE or the arena, and the radio
answers and hands the cable to the script until it restarts. The session takes module chunks in
the same signed format the radio takes over the air and hands them to the same loader, which
checks the signature under your key before anything is written. It can also read the SPI flash
(for the backup and to check what it wrote), report the image's build ID, and restart the radio.
It never transmits and never reads the PTT line. The design, the frame format and the safety
rules are in [`docs/k6-modules.md`](../k6-modules.md#the-cable-session).

## Licences

blowfang's own code (the modules, the tools, this book) is MIT. The firmware images are builds of
Aura with blowfang's patch, so they are GPL-3.0; their source is this repository and Aura's. No
part of Baofeng's firmware is in blowfang or in a release folder.
