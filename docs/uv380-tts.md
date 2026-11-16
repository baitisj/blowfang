# Speech for the UV-380 player

The UV-380 player (PAGER mode) plays the sotto 1200 messages it hears as PHY R range packets.
The test messages and the drive echo's "Received FSK" / "Received range" are synthetic speech
made on hamlet. Until 11-16 that speech was espeak-ng at 140 wpm. This page picks a better voice
and says how to make a send with it.

## Result (11-16)

Default: **Kokoro 82M, voice af_heart** (`tools/tts_say.py`, engine `kokoro`).
Alternate: **Piper en_US-lessac-high** (`--engine piper`).

Jeff listened to nine voices after sotto 1200 (11-16 09:16Z): "#1 [Kokoro af_heart] and #7
[Piper lessac] were both pretty good. I thought #1's synthesis was best, but #7 had more
appropriate prosody." The clips are in the project folder, uv380/tts-20261116/.

espeak-ng, the old voice, is the worst by far after sotto 1200. A speech recognizer
(Parakeet) misheard 53% of its words, against 3-9% for every neural voice in round 2. Clean,
before the codec, espeak-ng was already at 38%: sotto 1200 doesn't break it, it was never
easy to understand.

## How the voices were scored

`tools/tts_compare.py`. Each voice speaks the same sentences. Each sentence takes the radio's
path on the host: 8 kHz at peak -1 dBFS, then the fixed-point sotto 1200 encoder and decoder
from the sotto submodule (`VARIANT=v3.7`, the UV-380's codec build, which plays bit-exact with
the radio). The decoded speech is scored:

- `wer_pk`: word error rate of NVIDIA Parakeet TDT 0.6B v2 (sherpa-onnx, int8).
- `wer_wh`: word error rate of Whisper base.en. A weaker listener, so it spreads the voices.
- `clean`: Parakeet's word error rate before the codec.
- `ovrl`, `sig`: DNSMOS P.835 overall and speech quality, 1-5.
- `pesq`: PESQ narrowband of the decoded speech against the 8 kHz voice before the codec,
  i.e. how much sotto takes away from that voice.
- `s/w`: seconds of speech a word. This sets air time: PHY R carries about 0.43 s of speech
  a second.
- `rtf`: synthesis time over speech time, on a 4-core cloud machine.

Round 1: 25 voices, 10 sentences (Harvard lists 1-2 and two radio phrases, 92 words).
Round 2: the 10 leaders, 45 more sentences (Harvard lists 3-6 and five radio phrases, 344
words). One word is about 0.3% in round 2, so differences under about 2% are noise.

### Round 2, the leaders (ranked by the two word error rates, then DNSMOS)

| engine | voice | wer_pk | wer_wh | clean | ovrl | sig | pesq | s/w | rtf |
|---|---|---|---|---|---|---|---|---|---|
| kokoro | bm_fable | 0.054 | 0.079 | 0.041 | 2.97 | 3.23 | 2.53 | 0.381 | 0.328 |
| kokoro | bf_emma | 0.050 | 0.103 | 0.035 | 2.77 | 3.18 | 2.55 | 0.287 | 0.392 |
| piper | en-us-amy-low | 0.034 | 0.124 | 0.034 | 2.72 | 3.13 | 2.67 | 0.380 | 0.029 |
| kokoro | **af_heart** | 0.053 | 0.110 | 0.046 | 2.69 | 3.05 | 2.38 | 0.281 | 0.329 |
| kokoro | am_michael | 0.044 | 0.121 | 0.035 | 2.81 | 3.17 | 2.36 | 0.316 | 0.357 |
| piper | **en_US-lessac-high** | 0.058 | 0.127 | 0.043 | 2.63 | 2.96 | 2.61 | 0.294 | 0.232 |
| piper | en_GB-alan-medium | 0.073 | 0.118 | 0.028 | 3.03 | 3.35 | 2.18 | 0.360 | 0.036 |
| piper | en_US-hfc_male-medium | 0.065 | 0.128 | 0.034 | 2.88 | 3.15 | 2.69 | 0.310 | 0.039 |
| kokoro | af_sarah | 0.071 | 0.145 | 0.043 | 2.69 | 3.05 | 2.46 | 0.276 | 0.397 |
| piper | en_US-hfc_female-medium | 0.089 | 0.162 | 0.043 | 2.55 | 2.90 | 2.42 | 0.294 | 0.042 |

The leaders are close on words: the numbers alone don't pick one, so Jeff's ears did.
af_heart and lessac-high are among the fastest talkers (0.28-0.29 s a word), so they cost the
least air time. bm_fable and amy are about 30% slower.

### Round 1, everyone (10 sentences)

| engine | voice | wer_pk | wer_wh | clean | ovrl | pesq | s/w |
|---|---|---|---|---|---|---|---|
| kokoro | bf_emma | 0.061 | 0.118 | 0.079 | 2.76 | 2.65 | 0.293 |
| kokoro | bm_fable | 0.068 | 0.118 | 0.056 | 2.95 | 2.56 | 0.380 |
| piper | en-us-amy-low | 0.078 | 0.110 | 0.056 | 2.86 | 2.65 | 0.373 |
| kokoro | am_michael | 0.092 | 0.118 | 0.079 | 2.85 | 2.38 | 0.333 |
| piper | en_US-lessac-high | 0.093 | 0.120 | 0.068 | 2.68 | 2.59 | 0.306 |
| kokoro | af_heart | 0.093 | 0.120 | 0.068 | 2.67 | 2.33 | 0.292 |
| kokoro | af_sarah | 0.079 | 0.147 | 0.068 | 2.72 | 2.51 | 0.288 |
| piper | en_US-hfc_male-medium | 0.068 | 0.164 | 0.056 | 2.95 | 2.86 | 0.334 |
| piper | en_GB-alan-medium | 0.090 | 0.153 | 0.079 | 3.02 | 2.19 | 0.371 |
| piper | en-us-lessac-medium | 0.095 | 0.163 | 0.092 | 2.53 | 2.55 | 0.291 |
| piper | en_US-hfc_female-medium | 0.078 | 0.180 | 0.072 | 2.63 | 2.46 | 0.304 |
| piper | en-us-ryan-medium | 0.120 | 0.143 | 0.081 | 2.75 | 2.56 | 0.288 |
| kokoro | am_fenrir | 0.092 | 0.172 | 0.079 | 2.73 | 2.62 | 0.273 |
| kokoro | am_puck | 0.081 | 0.195 | 0.068 | 2.81 | 2.76 | 0.285 |
| kokoro | bm_george | 0.104 | 0.180 | 0.079 | 2.83 | 2.07 | 0.363 |
| kokoro | af_bella | 0.092 | 0.207 | 0.068 | 2.47 | 2.45 | 0.290 |
| piper | en_US-kristin-medium | 0.093 | 0.233 | 0.081 | 2.40 | 2.55 | 0.337 |
| piper | en-us-ryan-high | 0.128 | 0.206 | 0.079 | 2.73 | 2.45 | 0.288 |
| piper | en_GB-cori-high | 0.067 | 0.288 | 0.067 | 2.81 | 2.72 | 0.321 |
| piper | en_US-john-medium | 0.142 | 0.244 | 0.092 | 2.89 | 2.94 | 0.354 |
| piper | en_US-norman-medium | 0.105 | 0.291 | 0.081 | 2.94 | 2.53 | 0.307 |
| piper | en_US-joe-medium | 0.140 | 0.261 | 0.118 | 2.86 | 2.60 | 0.304 |
| kokoro | af_nicole | 0.161 | 0.256 | 0.056 | 1.78 | 1.37 | 0.438 |
| piper | en_GB-northern_english_male-medium | 0.142 | 0.379 | 0.079 | 2.75 | 2.59 | 0.284 |
| espeak | en-us (140 wpm, the old voice) | 0.530 | 0.652 | 0.379 | 2.55 | 1.85 | 0.342 |

af_nicole whispers, and sotto's pitch-and-voicing model has nothing to hold on to (PESQ 1.37).

## Cost on hamlet (11-16, 4 cores, model loaded, same 10 s text)

| | Kokoro af_heart | Piper lessac-high |
|---|---|---|
| synthesis per second of speech | 0.95 s wall, 3.2 s CPU | 0.70 s wall, 2.5 s CPU |
| whole command, max RSS | 450 MB | 280 MB |
| model files | 121 MB (int8 model + voice pack) | 114 MB |
| repeatable | yes, same output every run | no, Piper's noise is random |

A 15 s message (the 375-frame cap) takes about 15 s to build with Kokoro and 11 s with Piper,
before the send starts. Loading either model takes under 0.5 s. Kokoro's fp32 model is no
faster than int8 on x86, so the 88 MB int8 file is the one fetched.

## On air (11-16 09:12-09:14Z)

The IC-7100 at RF power 0 on 440.850 sent `tools/uv380_say.py` messages to the UV-380 in PAGER
mode, cable in. Kokoro, msg 0x5d: 141 of 141 frames, 8 packets, 19.1 s on air. Piper
hfc_male, msg 0x5e: 137 of 137 frames, 7 packets, 17.2 s. Playback isn't confirmed over USB:
`pv` reads 0 with the cable in, as on 11-14. See docs/uv380-characteristics.md.

## Making a send

    python -m venv ~/blowfang-work/tts
    ~/blowfang-work/tts/bin/pip install -r tools/tts_requirements.txt
    sh tools/tts_models.sh ~/blowfang-work/tts/models          # Kokoro and Piper lessac-high
    make -C sotto/c VARIANT=v3.7 fixed PYTHON=false
    ~/blowfang-work/tts/bin/python tools/uv380_say.py "This is a test. A G 7 E W." send.wav --msg-id 0x5f

`uv380_say.py` builds the same wav as hamlet's `echo_data.build`: speech at 8 kHz, peak 0.6;
sotto 1200; XL packets (depth 8, short last group); PHY R at peak 0.3 with 300 Hz
pre-emphasis; then the AG7EW CW ident. Play it with `tools/k6_send.sh` (IC-7100, 70 cm only).
The radio ignores a msg id it played in the last 60 s, so give each test a new one.

hamlet's `kokoro-onnx` 0.6.1 says Python < 3.14. It is pure Python and runs on hamlet's 3.14
with `pip install --ignore-requires-python kokoro-onnx==0.6.1`.

`tools/tts_say.py "text" out.wav` (the drive echo's call) now speaks with Kokoro. It falls back
to espeak-ng when the Kokoro files are missing, and still uses espeak-ng when called with the
old words-a-minute argument (`... out.wav 140`).

## Licences

- Kokoro 82M: Apache-2.0.
- Piper's lessac voices are trained on the Blizzard 2013 Lessac data, which is non-commercial
  use only. Fine for amateur radio.
- Piper's hfc_* voices: CC0.
- Model files are fetched, not kept in this repository.
