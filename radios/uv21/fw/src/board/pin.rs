//! UV-21 Pro: one GPIO pin on any port. The UV-21's pins are spread over ports A, B, C and F
//! (the UV-5RH's board code takes one port per function), so the board files below name each pin
//! once here and leave the callers' signatures as upstream has them.

use at32f421_pac as pac;

type Regs = pac::gpioa::RegisterBlock;

#[derive(Clone, Copy)]
pub struct Pin {
    port: u8,
    bit: u8,
}

pub const fn pin(port: u8, bit: u8) -> Pin {
    Pin { port, bit }
}

fn regs(port: u8) -> &'static Regs {
    // SAFETY: the GPIO blocks are memory-mapped and every access below is a single
    // read-modify-write of one pin's field or a write to a set/clear register.
    unsafe {
        match port {
            b'A' => &*pac::Gpioa::ptr(),
            b'B' => &*pac::Gpiob::ptr(),
            b'C' => &*pac::Gpioc::ptr(),
            _ => &*pac::Gpiof::ptr(),
        }
    }
}

impl Pin {
    fn mode(self, m: u32) {
        let s = 2 * self.bit as u32;
        regs(self.port)
            .cfgr()
            .modify(|r, w| unsafe { w.bits((r.bits() & !(3 << s)) | (m << s)) });
    }

    /// 0 none, 1 pull-up, 2 pull-down
    pub fn pull(self, p: u32) {
        let s = 2 * self.bit as u32;
        regs(self.port)
            .pull()
            .modify(|r, w| unsafe { w.bits((r.bits() & !(3 << s)) | (p << s)) });
    }

    pub fn output(self) {
        self.mode(0b01);
    }

    pub fn input(self, pull: u32) {
        self.mode(0b00);
        self.pull(pull);
    }

    pub fn analog(self) {
        self.mode(0b11);
        self.pull(0);
    }

    pub fn alternate(self, af: u32) {
        let r = regs(self.port);
        if self.bit < 8 {
            let s = 4 * self.bit as u32;
            r.muxl()
                .modify(|v, w| unsafe { w.bits((v.bits() & !(15 << s)) | (af << s)) });
        } else {
            let s = 4 * (self.bit - 8) as u32;
            r.muxh()
                .modify(|v, w| unsafe { w.bits((v.bits() & !(15 << s)) | (af << s)) });
        }
        self.mode(0b10);
    }

    pub fn set(self, high: bool) {
        let r = regs(self.port);
        if high {
            r.scr().write(|w| unsafe { w.bits(1 << self.bit) });
        } else {
            r.clr().write(|w| unsafe { w.bits(1 << self.bit) });
        }
    }

    pub fn read(self) -> bool {
        regs(self.port).idt().read().bits() >> self.bit & 1 != 0
    }
}
