# NATO keyword spotter (PC side)

PC prototype of the K6 NATO-phonetic keyword spotter: a small DS-TCN + CTC net (JAX) over
sotto 1200 frames (voicing, pitch, energy, 19 shape coefficients per 20 ms), plus the stage-2
known-repeater template check. Thread "Spot NATO phonetic callsigns on the K6".

- `kws_feat.c` — sotto 1200 frame extractor (built as `kws_feat`, called by `feats.py`)
- `feats.py` — features, FM / narrow repeater channel models
- `textgen.py`, `gen_kokoro.py`, `gen_robot.py`, `gen_rc850.py`, `render_lpc.py` — synthetic training data
- `make_feats.py`, `train.py`, `evalm.py`, `eval_real.py`, `quant.py`, `stage1.py`, `s1roc.py`, `closedset.py`, `window.py` — training and evaluation
- `known_id_template.py`, `live_detect.py` — stage-2 template match and the live 70 cm report

Training data, models and the RC-850 vocabulary are not in the repository (the RC-850 vocabulary
and models trained on it stay private). Paths in the scripts point at the cloud work folder.
Frequencies and callsigns in `live_detect.py` and `known_id_template.py` are SITE-SPECIFIC.
