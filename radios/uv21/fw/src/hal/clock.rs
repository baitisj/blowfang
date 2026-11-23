use at32f421_pac as pac;

/// UV-21 Pro: the external crystal doesn't start (bring-up image, 11-23), so the PLL runs from
/// the internal 8 MHz clock, which enters the PLL halved: 4 MHz x 15 = 60 MHz. (The bootloader
/// leaves 4 MHz x 12 = 48 MHz.) Half the UV-5RH's 120 MHz, on a multiplier setting it has proven.
pub const SCLK_HZ: u32 = 60_000_000;

pub fn configure_system_clock(crm: &pac::Crm, flash: &pac::Flash) {
    if crm.cfg().read().sclksts().bits() != 0b00 {
        crm.cfg().modify(|_, w| unsafe { w.sclksel().bits(0b00) });
        while crm.cfg().read().sclksts().bits() != 0b00 {}
    }
    if crm.ctrl().read().pllen().bit_is_set() {
        crm.ctrl().modify(|_, w| w.pllen().clear_bit());
        while crm.ctrl().read().pllstbl().bit_is_set() {}
    }

    crm.ctrl().modify(|_, w| w.hicken().set_bit());
    while crm.ctrl().read().hickstbl().bit_is_clear() {}

    flash.psr().write(|w| unsafe { w.bits(0x153) });

    // PLLRCS=0 (HICK/2 = 4 MHz PLL entry) -> PLLMULT = 00_1101 -> 4 MHz * 15 = 60 MHz
    crm.cfg().modify(|_, w| unsafe {
        w.pllrcs()
            .clear_bit()
            .pllhextdiv()
            .clear_bit()
            .pllmult3_0()
            .bits(0b1101)
            .pllmult5_4()
            .bits(0b00)
    });

    crm.ctrl().modify(|_, w| w.pllen().set_bit());
    while crm.ctrl().read().pllstbl().bit_is_clear() {}

    crm.cfg().modify(|_, w| unsafe { w.sclksel().bits(0b10) });
    while crm.cfg().read().sclksts().bits() != 0b10 {}
}
