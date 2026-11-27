# radio-common: code the Rust firmwares share

A `no_std` crate that both Rust firmwares link: the K6's (`radios/k6/fw`, from Aura) and the
UV-21 Pro's (`radios/uv21/fw`, from bf-5rh-firmware-open). It holds the pieces the two had in
identical copies, so a fix lands once:

- `debounce`: the threshold debouncer for keys, squelch and power sense.
- `delay`: SysTick busy-waits in ms and us, at the clock the caller passes.
- `wear_leveled`: the append-only record store in one SPI flash sector, with CRC-16/XMODEM, over
  a small `FlashStorage` trait that each radio's `NorFlash` driver implements.
- `spsc`: a lock-free single-producer, single-consumer byte ring (the UV-21's serial receive).

Each firmware keeps its `src/hal/debounce.rs`, `delay.rs` and `wear_leveled.rs` as one-line
re-exports or wrappers, so the call sites didn't change. Everything here compiles for Cortex-M0
(`thumbv6m-none-eabi`) and Cortex-M4 (`thumbv7em-none-eabi`), allocates nothing, and moved
without changing any code. Measured when it moved (2026-11-27): every K6 image kept the same
functions with the same instructions, only laid out in another order (images within 8 bytes);
the UV-21 port shrank 36 bytes, as its two inlined copies of the CRC became one shared function.

The unit tests run on the host: `cd radios/common-rs && cargo test` (the UV-21 CI workflow runs
them). The code comes from Aura (GPL-3) and the UV-21 port, so the crate is GPL-3.0 (`LICENSE`),
like both firmwares; the rest of blowfang stays MIT.
