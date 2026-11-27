use cortex_m::peripheral::syst::SystClkSource;
use cortex_m::peripheral::SYST;

/// Busy-wait `ms` milliseconds using the Cortex-M SysTick timer at `sysclk_hz`.
#[inline]
pub fn ms(syst: &mut SYST, sysclk_hz: u32, ms: u32) {
    let reload = sysclk_hz / 1000 - 1;
    syst.set_clock_source(SystClkSource::Core);
    syst.set_reload(reload);
    for _ in 0..ms {
        syst.clear_current();
        syst.enable_counter();
        while !syst.has_wrapped() {}
        syst.disable_counter();
    }
}

/// Busy-wait `us` microseconds using the Cortex-M SysTick timer at `sysclk_hz`.
#[inline]
pub fn us(syst: &mut SYST, sysclk_hz: u32, us: u32) {
    let cycles_per_us = sysclk_hz / 1_000_000;
    let reload = cycles_per_us.saturating_mul(us).max(1) - 1;
    syst.set_clock_source(SystClkSource::Core);
    syst.set_reload(reload);
    syst.clear_current();
    syst.enable_counter();
    while !syst.has_wrapped() {}
    syst.disable_counter();
}
