use at32f421_pac as pac;

/// UV-21 Pro: the external crystal doesn't start (bring-up image, 11-23), so the PLL runs from
/// the internal 8 MHz clock, which enters the PLL halved: 4 MHz x 30 = 120 MHz (matching the
/// UV-5RH's 120 MHz SCLK without HEXT; the bootloader leaves 4 MHz x 12 = 48 MHz).
pub const SCLK_HZ: u32 = 120_000_000;

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

    // 3 Flash wait states (WTCYC = 0b011) + prefetch enabled for 120 MHz SCLK.
    flash.psr().write(|w| unsafe { w.bits(0x153) });

    // PLLRCS=0 (HICK/2 = 4 MHz PLL entry) -> PLL_FREF=0 (4 MHz range),
    // PLLMULT = 29 = 0b01_1101 (CRM_PLL_MULT_30) -> 4 MHz * 30 = 120 MHz.
    // ADCDIV = 0b010 (CRM_ADC_DIV_6) -> 120 MHz / 6 = 20 MHz (AT32F421 ADC max is 28 MHz).
    crm.pll().modify(|_, w| unsafe { w.pll_fref().bits(0) });
    crm.cfg().modify(|_, w| unsafe {
        w.pllrcs()
            .clear_bit()
            .pllhextdiv()
            .clear_bit()
            .pllmult3_0()
            .bits(0b1101)
            .pllmult5_4()
            .bits(0b01)
            .adcdiv1_0()
            .bits(0b10)
            .adcdiv2()
            .clear_bit()
    });

    crm.ctrl().modify(|_, w| w.pllen().set_bit());
    while crm.ctrl().read().pllstbl().bit_is_clear() {}

    // Enable auto-step mode while switching SCLK directly from 8 MHz HICK to 120 MHz PLL.
    crm.misc2().modify(|_, w| unsafe { w.auto_step_en().bits(0b11) });
    crm.cfg().modify(|_, w| unsafe { w.sclksel().bits(0b10) });
    while crm.cfg().read().sclksts().bits() != 0b10 {}
    crm.misc2().modify(|_, w| unsafe { w.auto_step_en().bits(0b00) });
}
