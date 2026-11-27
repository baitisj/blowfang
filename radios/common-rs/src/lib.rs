//! Shared `no_std` HAL building blocks for Blowfang's Rust radio firmwares
//! (`radios/k6/fw` and `radios/uv21/fw`).
//!
//! Everything here is MCU-independent (compiles for both Cortex-M0 `thumbv6m-none-eabi`
//! and Cortex-M4 `thumbv7em-none-eabi`) with zero dynamic allocation and zero extra
//! runtime overhead.
#![cfg_attr(not(test), no_std)]

pub mod debounce;
pub mod delay;
pub mod spsc;
pub mod wear_leveled;
