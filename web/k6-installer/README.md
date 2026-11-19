# The K6 installer in the browser

`index.html` installs blowfang on a Baofeng UV-K6 / K61 over the programming cable, from Chrome,
Edge or Opera on a desktop, through Web Serial. It is `tools/k6_install.py` ported to JavaScript
for owners who don't have Python: the same frames, the same steps and the same checks.

| Page button | `k6_install.py` | What it does |
|---|---|---|
| Install or update | `install BUNDLE` | A new radio: back up at FORMAT FLASH?, both codebook installers, the core image, then the library. A blowfang radio: backup, core image over the cable, library, with no hands. |
| Load library only | `load BUNDLE` | EXPLORE's pages, the help deck and the modules into the SPI library (slots at 0x110000) |
| Back up SPI flash | `backup` | The whole 2 MB chip (factory calibration included), saved as a download |
| Radio status | `status` | Build ID, missing codebooks, what each library slot holds |
| Flash one image | `flash IMAGE` | Keys any blowfang `.bin` with your key and flashes it |
| Make my key | `key` | A random 16-byte key, saved as `k6-mod.key` (the same file format) |

## Keys

The page carries no key and never uploads one. The owner makes a key in the browser (or loads
the `k6-mod.key` that `k6_install.py key` made), and the page writes it over the placeholder in
each image just before flashing and signs every library image with it (SipHash-2-4, as
`modem/k6mod.py`) as it sends it. Release bundles stay unkeyed, as `k6_install.py bundle`
requires. A pre-keyed image can't be published safely, because a keyed image holds its key in
plain bytes.

## Files

- `k6web.js`: everything that talks to the radio (CRCs, whitening, SipHash, module images and
  packets, the CPS cable session, the stock bootloader, the install steps, zip and manifest
  checks, the Web Serial port). No DOM, so it also runs under Node for the tests.
- `app.js`: the page's buttons, prompts, downloads and log.
- `index.html`: the page. Plain scripts with no build step and nothing from a CDN, so it also runs
  from a saved copy opened as a file.

## Tests

`tests/test_k6_web_install.py` runs `k6web.js` under Node (`tests/k6_web_driver.cjs`) against the
same emulated radio as `tests/test_k6_install.py`: the real module loader and SPI library
(`c/k6/kl_host`), Aura's first-boot dump and the stock bootloader. It also checks that the
JavaScript signs, packs and keys byte for byte as `modem/k6mod.py` does.

```sh
make -C c/k6 kl_host
python -m pytest -q tests/test_k6_web_install.py
```

## Hosting

Web Serial needs a secure page: `https://`, `http://localhost`, or a file opened from disk. GitHub
Pages serving this folder works as it is.
