//! 8 kHz PCM (16 kHz in the hiss band image) out of the MCU's PWM pin, the path the stock firmware uses for
//! its voice prompts: PA11 = TIM1_CH4 (AF2) -> two RC poles (4.7k/3.9n,
//! 4.7k/22n) -> 22k -> the speaker amplifier's input, summed with the RF
//! chip's AF output. Aura itself never drives PA11.
//!
//! Same timer setup as the stock voice prompts: TIM1 counts to 2000 at
//! 96 MHz (a 48 kHz carrier), the repetition counter makes an update event
//! every 6 periods (8 kHz), and each update has DMA1 channel 5 copy the next
//! compare value from a circular RAM ring into CCR4 through TIM1_DMAR. No
//! interrupt: the caller polls `play_segment()` and refills the segments
//! behind it. Registers are written by address (STM32F0 layout, checked
//! against KD32F328CB.svd) to keep this independent of the PAC's API.
//!
//! Part of the sotto voice pager (github.com/baitisj/sotto, docs/k6-pager.md).

use core::ptr::{read_volatile, write_volatile};

pub const TOP: u16 = 2000;
pub const MID: u16 = TOP / 2;
// one 20 ms sotto frame: 160 samples at 8 kHz, or 320 at 16 kHz in the hiss band image (the
// decoder's sp_frame_samples() must agree); four of them in the ring: the decoder has the
// time of two to three frames for each one (the hiss image's ring had 3 until the RAM above
// Aura's old region was found, and streaming dense speech popped with no late count)
#[cfg(not(feature = "hiss"))]
pub const SEG: usize = 160;
#[cfg(feature = "hiss")]
pub const SEG: usize = 320;
pub const NSEG: usize = 4;
pub const RING: usize = SEG * NSEG;
/// TIM1's repetition count: an update (one sample) every RCR + 1 periods of the 48 kHz PWM
#[cfg(not(feature = "hiss"))]
const RCR: u32 = 5; // 8 kHz
#[cfg(feature = "hiss")]
const RCR: u32 = 2; // 16 kHz

const RCC_AHBENR: u32 = 0x4002_1014; // DMAEN bit 0
const RCC_APB2ENR: u32 = 0x4002_1018; // TIM1EN bit 11
const GPIOA_MODER: u32 = 0x4800_0000;
const GPIOA_OSPEEDR: u32 = 0x4800_0008;
const GPIOA_AFRH: u32 = 0x4800_0024;
const TIM1: u32 = 0x4001_2C00;
const TIM_CR1: u32 = TIM1;
const TIM_DIER: u32 = TIM1 + 0x0C;
const TIM_EGR: u32 = TIM1 + 0x14;
const TIM_CCMR2: u32 = TIM1 + 0x1C;
const TIM_CCER: u32 = TIM1 + 0x20;
const TIM_PSC: u32 = TIM1 + 0x28;
const TIM_ARR: u32 = TIM1 + 0x2C;
const TIM_RCR: u32 = TIM1 + 0x30;
const TIM_CCR4: u32 = TIM1 + 0x40;
const TIM_BDTR: u32 = TIM1 + 0x44;
const TIM_DCR: u32 = TIM1 + 0x48;
const TIM_DMAR: u32 = TIM1 + 0x4C;
const DMA_IFCR: u32 = 0x4002_0004;
const DMA_CCR5: u32 = 0x4002_0058;
const DMA_CNDTR5: u32 = 0x4002_005C;
const DMA_CPAR5: u32 = 0x4002_0060;
const DMA_CMAR5: u32 = 0x4002_0064;

fn rd(a: u32) -> u32 {
    unsafe { read_volatile(a as *const u32) }
}
fn wr(a: u32, v: u32) {
    unsafe { write_volatile(a as *mut u32, v) }
}
fn modify(a: u32, clear: u32, set: u32) {
    wr(a, (rd(a) & !clear) | set);
}

// The DMA ring is part of the shared RAM block (hal::arena), right after the message store.
// Only `PwmAudio` touches it while it plays; others borrow it through `idle_bytes`, and a RAM
// module's code may cover it while nothing plays.

/// The ring's size in bytes
pub const RING_BYTES: usize = RING * 2;

/// The ring as bytes, for other uses while nothing plays (the coded 4FSK session's receiver;
/// the screen's page buffer while it is drawn, hal::arena::lcd). A playback fills it before it
/// starts, which overwrites whatever was there.
pub fn idle_bytes() -> *mut u8 {
    super::arena::ring() as *mut u8
}

pub struct PwmAudio {
    running: bool,
}

impl PwmAudio {
    pub const fn new() -> Self {
        PwmAudio { running: false }
    }

    /// One segment of the ring, to fill before `start` or after the DMA has
    /// moved past it.
    pub fn segment(&mut self, i: usize) -> &mut [u16; SEG] {
        // SAFETY: the DMA only reads the segment it is playing; callers
        // write only the others (see `play_segment`).
        unsafe {
            let p = super::arena::ring();
            &mut *(p.add(i * SEG) as *mut [u16; SEG])
        }
    }

    /// Starts playing the ring from segment 0 (fill it first).
    pub fn start(&mut self) {
        modify(RCC_AHBENR, 0, 1 << 0);
        modify(RCC_APB2ENR, 0, 1 << 11);

        // TIM1: 48 kHz PWM on CH4, update (and DMA request) every RCR + 1 periods.
        wr(TIM_CR1, 0);
        wr(TIM_PSC, 0);
        wr(TIM_ARR, TOP as u32);
        wr(TIM_RCR, RCR);
        wr(TIM_CCR4, MID as u32);
        // OC4M = 110 (PWM mode 1), OC4PE preload; channel 3 untouched (unused).
        modify(TIM_CCMR2, 0xFF00, (0b110 << 12) | (1 << 11));
        wr(TIM_CCER, 1 << 12); // CC4E, active high
        wr(TIM_DCR, (0 << 8) | (0x40 / 4)); // DMA burst: 1 transfer to CCR4
        wr(TIM_DIER, 1 << 8); // UDE
        wr(TIM_EGR, 1); // load ARR, RCR, PSC

        // DMA1 channel 5 (TIM1_UP): ring -> TIM1_DMAR, circular, 16-bit.
        wr(DMA_CCR5, 0);
        wr(DMA_IFCR, 0xF << 16); // clear channel 5 flags
        wr(DMA_CPAR5, TIM_DMAR);
        wr(DMA_CMAR5, super::arena::ring() as u32);
        wr(DMA_CNDTR5, RING as u32);
        // PL high, MSIZE 16, PSIZE 16, MINC, CIRC, DIR = memory to peripheral
        wr(DMA_CCR5, (0b10 << 12) | (0b01 << 10) | (0b01 << 8) | (1 << 7) | (1 << 5) | (1 << 4));
        modify(DMA_CCR5, 0, 1);

        // PA11 -> AF2 (TIM1_CH4), fast.
        modify(GPIOA_AFRH, 0xF << 12, 2 << 12);
        modify(GPIOA_OSPEEDR, 0b11 << 22, 0b11 << 22);
        modify(GPIOA_MODER, 0b11 << 22, 0b10 << 22);

        wr(TIM_CR1, (1 << 7) | 1); // ARPE, CEN
        modify(TIM_BDTR, 0, 1 << 15); // MOE: outputs on
        self.running = true;
    }

    /// Index of the segment the DMA is reading now.
    pub fn play_segment(&self) -> usize {
        let left = (rd(DMA_CNDTR5) & 0xFFFF) as usize;
        let pos = if left == 0 || left > RING { 0 } else { RING - left };
        pos / SEG
    }

    /// For bring-up reports: DMA channel 5's count left, TIM1's counter, DMA
    /// channel 5's control word and DMA1's interrupt status.
    pub fn probe(&self) -> (u32, u32, u32, u32) {
        (rd(DMA_CNDTR5) & 0xFFFF, rd(TIM1 + 0x24) & 0xFFFF, rd(DMA_CCR5), rd(0x4002_0000))
    }

    /// Stops the timer and DMA and returns PA11 to an input, as at reset
    /// (call it with the speaker amplifier already off: the 1 uF coupling
    /// capacitor makes the step audible).
    pub fn stop(&mut self) {
        if !self.running {
            return;
        }
        modify(TIM_BDTR, 1 << 15, 0);
        wr(TIM_CR1, 0);
        wr(TIM_DIER, 0);
        modify(DMA_CCR5, 1, 0);
        modify(GPIOA_MODER, 0b11 << 22, 0b00 << 22); // input (hi-Z), as at reset
        self.running = false;
    }
}
