//! UV-21 Pro: what the firmware reads from the SPI flash (read only: drivers/norflash.rs
//! refuses writes): the settings record, for the backlight delay, and the calibration blocks.
//! The UV-5RH's VFO, channel, contact and FM-list records went with the VFO screen (port 14).
#![allow(dead_code)]

use crate::drivers::fd6818b::{Power, SquelchCal};
use crate::drivers::norflash::NorFlash;
use crate::flash_map::{self, addr};
use crate::hal::wear_leveled::WearLeveledRegion;

/// Global settings record.
const SETTINGS_REGION: WearLeveledRegion<{ flash_map::SETTINGS_BYTES }> =
    WearLeveledRegion::new(addr::RADIO_IMFOS_ADDR, 16);

pub struct Storage<'a> {
    pub(crate) norflash: NorFlash<'a>,
}

impl<'a> Storage<'a> {
    pub fn new(norflash: NorFlash<'a>) -> Self {
        Storage { norflash }
    }

    pub fn load_settings(&mut self) -> Option<flash_map::Settings> {
        SETTINGS_REGION
            .load(&mut self.norflash)
            .map(|b| flash_map::Settings::from_bytes(&b))
    }

    pub fn read_calibration(
        &mut self,
    ) -> [u8; crate::drivers::norflash::CAL_BLOCK_LEN] {
        let mut buf = [0u8; crate::drivers::norflash::CAL_BLOCK_LEN];
        self.norflash
            .read_bytes(crate::drivers::norflash::CAL_BLOCK_ADDR, &mut buf);
        buf
    }

    pub fn read_squelch_cal(&mut self) -> SquelchCal {
        let mut cal = SquelchCal::DEFAULT;
        self.norflash
            .read_bytes(addr::TH_SQL_TAB_ADDR, &mut cal.th_in);
        self.norflash
            .read_bytes(addr::TH_SQL_TAB_MUTE_ADDR, &mut cal.th_out);
        self.norflash
            .read_bytes(addr::OFFSET_SQL_U_400_ADDR, &mut cal.off_u400);
        self.norflash
            .read_bytes(addr::OFFSET_SQL_U_350_ADDR, &mut cal.off_u350);
        self.norflash
            .read_bytes(addr::OFFSET_SQL_V_136_ADDR, &mut cal.off_v136);
        self.norflash
            .read_bytes(addr::OFFSET_SQL_V_200_ADDR, &mut cal.off_v200);
        cal
    }

    fn read_pa_byte(&mut self, addr: u32) -> u8 {
        let mut buf = [0u8; 1];
        self.norflash.read_bytes(addr, &mut buf);
        buf[0]
    }

    fn pa_calibration_addr(freq_hz: u32, power: Power) -> Option<u32> {
        let base = match power {
            Power::High => addr::PA_TABLE_BASE_HIGH,
            Power::Mid => addr::PA_TABLE_BASE_MID,
            Power::Low => addr::PA_TABLE_BASE_LOW,
        };
        let mhz = freq_hz / 1_000_000;
        if freq_hz >= 400_000_000 {
            Some(base + (mhz - 400) / 10)
        } else if freq_hz >= 200_000_000 {
            Some(base + 0x20 + (mhz - 200) / 5)
        } else if freq_hz >= 130_000_000 {
            let idx = ((mhz - 130) / 3).min(15);
            Some(base + 0x10 + idx)
        } else {
            None
        }
    }

    /// Calibrated APC target byte for the given frequency+power, read
    /// straight from flash. Returns `None` if the band isn't calibrated;
    /// the caller decides what to do with the raw value.
    pub fn read_pa_calibration(
        &mut self,
        freq_hz: u32,
        power: Power,
    ) -> Option<u8> {
        let addr = Self::pa_calibration_addr(freq_hz, power)?;
        Some(self.read_pa_byte(addr))
    }

    pub fn read_battery_calibration(&mut self) -> [u8; addr::DEV_BATT_LEN] {
        let mut buf = [0u8; addr::DEV_BATT_LEN];
        self.norflash.read_bytes(addr::DEV_BATT_ADDR, &mut buf);
        if buf[0] == 0x00 || buf[0] == 0xFF {
            buf = [130, 139, 156, 165, 183, 0];
        }
        buf
    }

    pub fn read_raw(&mut self, addr: u32, buf: &mut [u8]) {
        self.norflash.read_bytes(addr, buf);
    }
}
