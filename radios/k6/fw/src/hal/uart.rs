use core::cell::RefCell;
use cortex_m::interrupt::Mutex;
use kd32f328_pac::usart1;

const USER_VECTORS_BASE: usize = 0x2000_1000;
const USER_VECTOR_USART1: usize = 1;

pub fn install_bootloader_trampoline() {
    let slot = (USER_VECTORS_BASE + USER_VECTOR_USART1 * 4) as *mut usize;
    let handler = USART1 as unsafe extern "C" fn() as usize;
    unsafe { core::ptr::write_volatile(slot, handler) };
}

const RX_RING_CAP: usize = 320;

struct RxRing {
    buf: [u8; RX_RING_CAP],
    head: usize,
    tail: usize,
    len: usize,
}

impl RxRing {
    const fn new() -> Self {
        RxRing {
            buf: [0; RX_RING_CAP],
            head: 0,
            tail: 0,
            len: 0,
        }
    }

    fn push(&mut self, byte: u8) {
        if self.len == RX_RING_CAP {
            self.tail = (self.tail + 1) % RX_RING_CAP;
            self.len -= 1;
        }
        self.buf[self.head] = byte;
        self.head = (self.head + 1) % RX_RING_CAP;
        self.len += 1;
    }

    fn pop(&mut self) -> Option<u8> {
        if self.len == 0 {
            return None;
        }
        let byte = self.buf[self.tail];
        self.tail = (self.tail + 1) % RX_RING_CAP;
        self.len -= 1;
        Some(byte)
    }
}

#[link_section = ".ram0"] // hal::ram0
static RX_RING: Mutex<RefCell<RxRing>> = Mutex::new(RefCell::new(RxRing::new()));

// Report lines leave through this ring and the TXE interrupt, not by waiting on TXE for each
// byte: a `K6 PKT` line is 150-250 characters, 13-22 ms at 115200 baud, and while the pager
// streamed speech it went out between a packet's end and the next decode, so the decoder lost
// that time on every packet (2026-11-08, the hiss band image: 2 speaker underruns a packet,
// docs/k6-hiss-port.md). A writer that finds the ring full sends its oldest byte itself.
// 512: a PKT line fits; the hiss band image's boot stack has about 480 B to spare with it.
// Not in the lean images (codebook install, codebook in MCU flash): they are at the flash
// limit and don't stream; they keep the wait per byte.
#[cfg(not(feature = "lean"))]
const TX_RING_CAP: usize = 512;

#[cfg(not(feature = "lean"))]
struct TxRing {
    buf: [u8; TX_RING_CAP],
    head: usize,
    tail: usize,
    len: usize,
}

#[cfg(not(feature = "lean"))]
impl TxRing {
    const fn new() -> Self {
        TxRing {
            buf: [0; TX_RING_CAP],
            head: 0,
            tail: 0,
            len: 0,
        }
    }

    fn push(&mut self, byte: u8) -> bool {
        if self.len == TX_RING_CAP {
            return false;
        }
        self.buf[self.head] = byte;
        self.head = (self.head + 1) % TX_RING_CAP;
        self.len += 1;
        true
    }

    fn pop(&mut self) -> Option<u8> {
        if self.len == 0 {
            return None;
        }
        let byte = self.buf[self.tail];
        self.tail = (self.tail + 1) % TX_RING_CAP;
        self.len -= 1;
        Some(byte)
    }
}

#[cfg(not(feature = "lean"))]
#[link_section = ".ram0"] // hal::ram0
static TX_RING: Mutex<RefCell<TxRing>> = Mutex::new(RefCell::new(TxRing::new()));

/// USART1 IRQ handler: drains RDR into `RX_RING` on every received byte, and feeds TDR from
/// `TX_RING` while the TXE interrupt is on (off again once the ring is empty)
#[no_mangle]
pub unsafe extern "C" fn USART1() {
    let regs = &*kd32f328_pac::Usart1::ptr();
    let isr = regs.isr().read();
    if isr.ore().bit_is_set() {
        regs.icr().write(|w| w.orecf().set_bit());
    }
    if isr.rxne().bit_is_set() {
        let byte = regs.rdr().read().rdr().bits() as u8;
        cortex_m::interrupt::free(|cs| RX_RING.borrow(cs).borrow_mut().push(byte));
    }
    #[cfg(not(feature = "lean"))]
    if isr.txe().bit_is_set() && regs.cr1().read().txeie().bit_is_set() {
        let next = cortex_m::interrupt::free(|cs| TX_RING.borrow(cs).borrow_mut().pop());
        match next {
            Some(b) => regs.tdr().write(|w| unsafe { w.tdr().bits(b as u16) }),
            None => regs.cr1().modify(|_, w| w.txeie().clear_bit()),
        };
    }
}

/// Queue one byte for USART1: the interrupt sends it. With the ring full (or the interrupt
/// still masked, as before `NVIC::unmask` at boot) the caller sends the oldest byte itself.
#[cfg(not(feature = "lean"))]
pub fn send_byte(byte: u8) {
    let regs = unsafe { &*kd32f328_pac::Usart1::ptr() };
    loop {
        let queued = cortex_m::interrupt::free(|cs| {
            let ok = TX_RING.borrow(cs).borrow_mut().push(byte);
            if ok {
                regs.cr1().modify(|_, w| w.txeie().set_bit());
            }
            ok
        });
        if queued {
            return;
        }
        while regs.isr().read().txe().bit_is_clear() {}
        // TXE checked again inside: the interrupt may have taken this slot meanwhile
        cortex_m::interrupt::free(|cs| {
            if regs.isr().read().txe().bit_is_set() {
                if let Some(b) = TX_RING.borrow(cs).borrow_mut().pop() {
                    regs.tdr().write(|w| unsafe { w.tdr().bits(b as u16) });
                }
            }
        });
    }
}

/// Lean images: the byte goes out here, with a wait on TXE, as every image did before
#[cfg(feature = "lean")]
#[inline(always)]
pub fn send_byte(byte: u8) {
    let regs = unsafe { &*kd32f328_pac::Usart1::ptr() };
    while regs.isr().read().txe().bit_is_clear() {}
    regs.tdr().write(|w| unsafe { w.tdr().bits(byte as u16) });
}

/// Lean images: nothing is queued (the callers' own delays before a reset cover the last byte)
#[cfg(feature = "lean")]
#[inline(always)]
pub fn flush() {}

/// Wait until every queued byte has left (before a reset or a jump to the bootloader), at
/// most about 100 ms; sends them itself if the interrupt is masked.
#[cfg(not(feature = "lean"))]
pub fn flush() {
    let regs = unsafe { &*kd32f328_pac::Usart1::ptr() };
    for _ in 0..200_000u32 {
        let left = cortex_m::interrupt::free(|cs| {
            let mut r = TX_RING.borrow(cs).borrow_mut();
            if regs.isr().read().txe().bit_is_set() {
                if let Some(b) = r.pop() {
                    regs.tdr().write(|w| unsafe { w.tdr().bits(b as u16) });
                }
            }
            r.len
        });
        if left == 0 && regs.isr().read().tc().bit_is_set() {
            return;
        }
    }
}

/// Pull one byte received on USART1 since the last call, if any.
pub fn take_byte() -> Option<u8> {
    cortex_m::interrupt::free(|cs| RX_RING.borrow(cs).borrow_mut().pop())
}

pub struct DebugUart<'a> {
    regs: &'a usart1::RegisterBlock,
}

impl<'a> DebugUart<'a> {
    /// pclk_hz: bus freq (APB2 for USART1)
    /// OVER8 == 0 (16x oversampling)
    pub fn new(regs: &'a usart1::RegisterBlock, pclk_hz: u32, baud: u32) -> Self {
        // RAW BRR = round(pclk/baud)；
        // high 12 bit (bit4..15): mantissa
        // low 4 bit: fraction (1/16)
        let raw = (pclk_hz + baud / 2) / baud;
        let mantissa = (raw >> 4) as u16;
        let fraction = (raw & 0xF) as u8;

        regs.brr().write(|w| unsafe {
            w.div_mantissa().bits(mantissa);
            w.div_fraction().bits(fraction)
        });

        regs.cr1().write(|w| {
            w.te().set_bit();
            w.re().set_bit();
            w.rxneie().set_bit();
            w.ue().set_bit()
        });

        DebugUart { regs }
    }

    pub fn write_byte(&mut self, byte: u8) {
        while self.regs.isr().read().txe().bit_is_clear() {}
        self.regs
            .tdr()
            .write(|w| unsafe { w.tdr().bits(byte as u16) });
    }

    pub fn write_bytes(&mut self, bytes: &[u8]) {
        for &b in bytes {
            self.write_byte(b);
        }
    }
}

#[cfg(debug_assertions)]
impl crate::out::Sink for DebugUart<'_> {
    fn bytes(&mut self, b: &[u8]) {
        for &c in b {
            if c == b'\n' {
                self.write_byte(b'\r');
            }
            self.write_byte(c);
        }
    }
}

impl core::fmt::Write for DebugUart<'_> {
    #[cfg(debug_assertions)]
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        for &b in s.as_bytes() {
            if b == b'\n' {
                self.write_byte(b'\r');
            }
            self.write_byte(b);
        }
        Ok(())
    }

    #[cfg(not(debug_assertions))]
    fn write_str(&mut self, _s: &str) -> core::fmt::Result {
        Ok(())
    }
}
