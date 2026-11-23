use core::cell::RefCell;
use cortex_m::interrupt::Mutex;

use at32f421_pac as pac;

const RX_RING_CAP: usize = 256;

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

static RX_RING: Mutex<RefCell<RxRing>> =
    Mutex::new(RefCell::new(RxRing::new()));

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
        cortex_m::interrupt::free(|cs| {
            RX_RING.borrow(cs).borrow_mut().push(byte)
        });
    } else if sts.roerr().bit_is_set() {
        let _ = regs.dt().read().dt().bits();
    }
}

pub fn take_byte() -> Option<u8> {
    cortex_m::interrupt::free(|cs| RX_RING.borrow(cs).borrow_mut().pop())
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
