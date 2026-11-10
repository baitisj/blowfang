# MIDI player: quick manual

The MIDI player is a RAM module that plays tunes from the K6's speaker in four voices, round and
round until you hold a key. It only listens and never keys the transmitter. It runs from RAM, so
you never reflash the radio to use it or to change its songs. Design notes and tests are in
[../k6-midi.md](../k6-midi.md); the code is `c/k6/mod/midi.c`.

## The songs it ships with

| # | Song | Source (public domain) |
| --- | --- | --- |
| 1 | Nokia Tune (Tarrega) | Tarrega, Gran Vals (1902), bars 13-16 |
| 2 | Fur Elise | Beethoven, WoO 59 (1810) |
| 3 | Mozart 40 | Mozart, Symphony No. 40 (1788) |
| 4 | Bumblebee | Rimsky-Korsakov, Flight of the Bumblebee (1900) |
| 5 | Valkyrie | Wagner, Ride of the Valkyries (1856) |
| 6 | Ode to Joy | Beethoven, Symphony No. 9 (1824) |

Three more scores wait in `tools/midi_songs/` for a swap:

- `greensleeves.txt`
- `minuet_in_g.txt`
- `twinkle.txt`

## Playing it

1. Get `mod-midi.bin` from the newest CI run of `k6-firmware.yml` (artifact `aura-pager-<id>`).
2. Have the K6 on a pager image, listening on 440.850 MHz with XL packets.
3. Send it from hamlet, signed:

```sh
python3 tools/k6_upload.py mod-midi.bin --api-level 2 --telemetry 3 --rounds 1 --key-file ~/.config/sotto/k6-mod.key
```

Always give `--key-file`: the K6 refuses an unsigned module. Always give `--rounds 1`: the module
loops, so the K6 can't answer until you stop it, and more rounds would just resend chunks it
can't hear.

## Keys

| On the K6 | What happens |
| --- | --- |
| Press any key | Next song (after the last, back to the first) |
| Hold a key for 1 s | Stop. The radio goes back to the pager |

The screen shows the song's title and "song 2 of 6" between songs (level 6 images). The green
LED blinks on each note (level 7 images).

## Swapping the songs

You never need a compiler for this. `tools/k6_midi.py patch` cuts the song table off the end of
CI's `mod-midi.bin` and puts a new one in its place, so it works on hamlet as it is.

1. Pick the songs: any mix of text scores (`.txt`) and standard MIDI files (`.mid`), in the
   order you want them played.
2. Check they fit:

   ```sh
   python3 tools/k6_midi.py info tools/midi_songs/greensleeves.txt my_song.mid
   ```

   It prints each song's notes, length and bytes, and says whether the table fits every image
   or only the SPI voice images.
3. Listen first (optional): this writes exactly what the K6 will play.

   ```sh
   python3 tools/k6_midi.py wav preview.wav tools/midi_songs/greensleeves.txt my_song.mid
   ```

4. Make the new module:

   ```sh
   python3 tools/k6_midi.py patch mod-midi.bin my-midi.bin tools/midi_songs/greensleeves.txt my_song.mid
   ```

5. Send `my-midi.bin` as in "Playing it" above.

Give `patch` the same options for every song in the table:

| Option | Does |
| --- | --- |
| `--once` | Play the list once and stop, instead of looping |
| `--volume N` | 1-255, default 100 |
| `--transpose N` | Shift by N semitones |
| `--max-s S` | Cut each song after S seconds |
| `--low N`, `--high N` | Fold notes outside MIDI N..N into range by octaves (default 40-95, E2-B6) |
| `--drums` | Keep MIDI channel 10 (it plays as pitches) |

### What happens to a MIDI file

The player has four voices, so the tool simplifies a `.mid`:

- channel 10 (drums) is left out;
- notes are folded by octaves into E2-B6, where the speaker sounds good;
- at most four notes sound at once, and the highest and lowest win, so the melody and bass stay;
- velocities are rounded to steps of 8;
- times are put on a grid: 1/24 of a quarter note for a file with one tempo, 8 ms otherwise.

Use `wav` to hear the result before you send it.

## Writing a song as text

A text score is one item per line. Lines starting with `#` are comments. This one fits every image
(168 bytes):

```text
# Frere Jacques (French traditional, 18th century): public domain.
title: Frere Jacques
tempo: 120
mel: G4:1 A4 B4 G4 | G4 A4 B4 G4 | B4 C5 D5:2 | B4:1 C5 D5:2
mel: D5:1/2 E5 D5 C5 B4:1 G4 | D5:1/2 E5 D5 C5 B4:1 G4 | G4 D4 G4:2 | G4:1 D4 G4:2
bass: !80 G3:4 | G3 | G3 | G3
bass: G3:4 | G3 | G3:2 D3 | G3:4
```

| Line | Meaning |
| --- | --- |
| `title: Name` | Shown on the K6's screen, up to 21 characters |
| `tempo: 120` | Quarter notes a minute |
| `legato: 0.9` | How much of its length a note sounds (default 0.9) |
| `name: tokens` | Any other name is a voice. Lines with the same name carry on that voice. Every voice starts at time 0 |

| Token | Meaning |
| --- | --- |
| `C4`, `F#5`, `Bb3` | A note; C4 is middle C |
| `:len` | Length in quarter notes (`:2`, `:1.5`, `:1/2`). It carries on to the next notes until changed |
| `~ring` | Let the note sound this many quarters while the voice moves on, like a pedal |
| `C4+E4+G4` | A chord |
| `r` | A rest (takes a length too: `r:3`) |
| `!90` | Velocity of the notes that follow, 1-127 |
| `\|` | Bar line; ignored, just for reading |

To make a new song part of every CI build, put its file in `tools/midi_songs/`, add it to
`DEFAULT_SONGS` in `tools/k6_midi.py`, and regenerate the header:
`python3 tools/k6_midi.py header`. The default list is kept small so the module still fits the
EXPLORE image.

## Limits

- Size: about 2.5 KB of code plus the songs. Songs up to about 1.6 KB fit every image (the EXPLORE
  image's block is 5,376 bytes); the SPI voice images (pager-only, lr, vlr, vlrh) take about 8 KB
  of songs. A note costs about 3 bytes.
- Four voices, and notes from E2 to B6.
- Public-domain music only. A MIDI file someone sequenced can be a work of its own even when the
  tune is old, so prefer writing the score yourself.

## If something goes wrong

| What you see | Why, and the fix |
| --- | --- |
| The K6 ignores the module | Unsigned or wrong key: send with `--key-file ~/.config/sotto/k6-mod.key` |
| `k6_upload.py` keeps resending | It wasn't given `--rounds 1`; the K6 is busy playing |
| Returns -3 | Not enough scratch RAM for the 512-byte wavetable |
| Returns -4 | TIM1 is already running (another module holds the speaker) |
| Returns -5 | No song table in the module: patch it again from CI's `mod-midi.bin` |
| `patch` says "SPI voice images only" | Too many songs for the EXPLORE image: drop one, or use a voice image |

To make it ring at a set time instead, see the alarm clock (`tools/k6_midi.py alarm`) in
[../k6-midi.md](../k6-midi.md).
