use cortex_m::peripheral::SYST;

pub const SYSCLK_HZ: u32 = super::clock::SCLK_HZ;

#[inline]
pub fn ms(syst: &mut SYST, ms: u32) {
    radio_common::delay::ms(syst, SYSCLK_HZ, ms);
}

#[inline]
pub fn us(syst: &mut SYST, us: u32) {
    radio_common::delay::us(syst, SYSCLK_HZ, us);
}
