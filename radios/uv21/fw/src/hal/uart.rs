use core::cell::UnsafeCell;
use core::sync::atomic::{AtomicUsize, Ordering};

use at32f421_pac as pac;

const RX_RING_CAP: usize = 256; // power of two
const RX_RING_MASK: usize = RX_RING_CAP - 1;

/// Lock-free single-producer (USART1 IRQ) / single-consumer (main loop) RX ring.
/// Avoids disabling interrupts (`cpsid i`) and `RefCell` borrow flags while polling.
struct RxRing {
    buf: UnsafeCell<[u8; RX_RING_CAP]>,
    head: AtomicUsize,
    tail: AtomicUsize,
}

unsafe impl Sync for RxRing {}

impl RxRing {
    const fn new() -> Self {
        RxRing {
            buf: UnsafeCell::new([0; RX_RING_CAP]),
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
        }
    }

    fn push(&self, byte: u8) {
        let head = self.head.load(Ordering::Relaxed);
        let next = (head + 1) & RX_RING_MASK;
        let tail = self.tail.load(Ordering::Acquire);
        if next == tail {
            // Ring full: drop oldest byte (same overwrite policy as before).
            self.tail.store((tail + 1) & RX_RING_MASK, Ordering::Release);
        }
        unsafe { (*self.buf.get())[head] = byte };
        self.head.store(next, Ordering::Release);
    }

    fn pop(&self) -> Option<u8> {
        let tail = self.tail.load(Ordering::Relaxed);
        let head = self.head.load(Ordering::Acquire);
        if tail == head {
            return None;
        }
        let byte = unsafe { (*self.buf.get())[tail] };
        self.tail.store((tail + 1) & RX_RING_MASK, Ordering::Release);
        Some(byte)
    }
}

static RX_RING: RxRing = RxRing::new();

/// UV-21 Pro: the KDH bootloader's flash-mode command, as the bench's probe sends it. Seen whole
/// on the serial line, it resets the MCU into the bootloader, so a new image can go on with no
/// hands on the radio (the bring-up images do the same). Eight bytes with a CRC, so programming
/// data can't trip it by accident.
const KDH_FLASH_MODE: [u8; 8] = [0xAA, 0x42, 0x00, 0x00, 0x00, 0x83, 0xF4, 0xEF];
static mut KDH_MATCHED: usize = 0;

fn kdh_watch(byte: u8) {
    // SAFETY: only the USART1 interrupt touches KDH_MATCHED.
    unsafe {
        let n = KDH_MATCHED;
        KDH_MATCHED = if byte == KDH_FLASH_MODE[n] {
            n + 1
        } else {
            usize::from(byte == KDH_FLASH_MODE[0])
        };
        if KDH_MATCHED == KDH_FLASH_MODE.len() {
            cortex_m::peripheral::SCB::sys_reset();
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn USART1() {
    let regs = &*pac::Usart1::ptr();
    let sts = regs.sts().read();
    if sts.rdbf().bit_is_set() {
        let byte = regs.dt().read().dt().bits() as u8;
        kdh_watch(byte);
        RX_RING.push(byte);
    } else if sts.roerr().bit_is_set() {
        let _ = regs.dt().read().dt().bits();
    }
}

pub fn take_byte() -> Option<u8> {
    RX_RING.pop()
}

pub fn write_byte(byte: u8) {
    let regs = unsafe { &*pac::Usart1::ptr() };
    while regs.sts().read().tdbe().bit_is_clear() {}
    regs.dt().write(|w| unsafe { w.dt().bits(byte as u16) });
}

pub fn write_bytes(bytes: &[u8]) {
    for &b in bytes {
        write_byte(b);
    }
}

pub fn flush() {
    let regs = unsafe { &*pac::Usart1::ptr() };
    while regs.sts().read().tdc().bit_is_clear() {}
}
