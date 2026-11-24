#![allow(dead_code)]
pub mod addr {

    pub const PA_TABLE_BASE_HIGH: u32 = 0xF000;
    pub const PA_TABLE_BASE_MID: u32 = 0xF040;
    pub const PA_TABLE_BASE_LOW: u32 = 0xF080;
    pub const PA_TABLE_ENTRY_LEN: usize = 16;

    pub const TH_SQL_TAB_ADDR: u32 = 0xF0C0;
    pub const TH_SQL_TAB_MUTE_ADDR: u32 = 0xF0D0;
    pub const TH_SQL_TAB_LEN: usize = 10;

    pub const OFFSET_SQL_U_400_ADDR: u32 = 0xF0E0;
    pub const OFFSET_SQL_V_136_ADDR: u32 = 0xF0F0;
    pub const OFFSET_SQL_V_200_ADDR: u32 = 0xF100;
    pub const OFFSET_SQL_U_350_ADDR: u32 = 0xF110;
    pub const OFFSET_SQL_LEN: usize = 16;

    pub const DEV_BATT_ADDR: u32 = 0xF200;
    pub const DEV_BATT_LEN: usize = 6;

    pub const MODULATION_CAL_ADDR: u32 =
        crate::drivers::norflash::CAL_BLOCK_ADDR;

    pub const BAND_ADDR: u32 = 0xF230;
    pub const BAND_LEN: usize = 21;

    pub const MODEL_ADDR: u32 = 0xF255;

    // the settings record (the UV-5RH's layout, same lineage as the K6's); the VFO, channel,
    // contact, FM and overlay areas went with the VFO screen (port 14)
    pub const RADIO_IMFOS_ADDR: u32 = 0x9000;
}

/// A coordinate outside its legal range is not a coordinate. Erased flash
/// reads back as `-1`, which would otherwise look like a position 0.00001
/// south of the equator.
const fn coord_or_unset(v: i32, limit: i32) -> i32 {
    if v >= -limit && v <= limit {
        v
    } else {
        COORD_NOT_SET
    }
}








#[derive(Clone, Copy)]
pub struct Settings {
    /// `sqlLevel`, 0-9.
    pub sql_level: u8,
    /// `tailSwitch`: squelch-tail elimination on TX release.
    pub tail_elimination: bool,
    /// `txBusyLock`: block PTT while the channel is busy.
    pub busy_lock: bool,
    /// `txForbid`: global TX inhibit.
    pub tx_forbid: bool,
    /// `keyAutoLock`: idle time before the keypad locks itself, 0 (off) - 3;
    /// lock delay is `value * 5` seconds.
    pub key_auto_lock: u8,
    /// `dualRxFlag` != 0: dual-standby enabled.
    pub dual_standby: bool,
    /// `voxSwitch`.
    pub vox_switch: bool,
    /// `voxLevel`, 1-9 (meaningless while `vox_switch` is off).
    pub vox_level: u8,
    /// `totLevel`, steps of 15s; 0 = disabled.
    pub tot_level: u8,
    /// `beepsSwitch`: audible tone on keypress.
    pub beeps_switch: bool,
    /// `txOffTone`: 0 = off, 1 = roger beep, 2 = MDC1200 burst.
    pub roger_tone: u8,
    /// `scarmble`: voice-inversion scramble group, 0 (off) - 3.
    pub scramble_level: u8,
    /// Repeater-access tone selection, 0-3: 1000/1450/1750/2100 Hz.
    pub rtone: u8,
    /// Channel/frequency scan resume behavior, 0-2 (Time/Carrier/Search).
    pub scan_mode: u8,
    /// Receiver incremental tuning (clarifier), USB only: units of 10Hz.
    pub rit_offset: i8,
    /// RX duty-cycle power-save level, 0 (off) - 4.
    pub save_level: u8,
    /// Automatic backlight-off delay, 0-4 (0 = always on).
    pub backlight_time: u8,
    /// Standby-screen channel readout, 0-2.
    pub channel_display_mode: u8,
    /// Auto-send our own ANI frame on PTT press.
    pub ani_tx: bool,
    /// `rptrl`: 0-10, steps of 100ms.
    pub rptrl: u8,
    /// `voxDelay`: 0-15, steps of 0.1s over 0.5-2.0s.
    pub vox_delay: u8,
    /// Raw ADC calibration point for battery-voltage readout.
    pub battery_cal_raw: u16,
    /// `app::keyfn::KeyFunction` index (0..=10) for the physical Side1
    /// (FM button, PB6), Side2 (flashlight button, PA15) and Band (SCAN
    /// key) buttons' short/long press. Added once `app/` existed to point
    /// at -- see this struct's own doc comment.
    pub side1_short: u8,
    pub side1_long: u8,
    pub side2_short: u8,
    pub side2_long: u8,
    pub band_short: u8,
    pub band_long: u8,
    /// `device::radio::BandLock` index.
    pub band_lock: u8,
    /// 0=None, 1=Msg, 2=Logo
    pub boot_display_mode: u8,
    /// Forced off whenever `boot_display_mode` is 0.
    pub boot_sound_enabled: bool,

    pub boot_text_line1: [u8; 16],
    pub boot_text_line2: [u8; 16],
    /// OpenGD77-compatible boot melody: 48 `(tone_index, duration)` pairs
    pub boot_tune: [(u8, u8); 48],
    /// Observer position, units of 1e-5 degrees (about 1.1 m), north and east
    /// positive. `COORD_NOT_SET` until the user enters one
    pub obs_lat: i32,
    pub obs_lon: i32,
}

/// `obs_lat`/`obs_lon` value meaning "the user has not set a position".
/// Out of range for both, so it can never collide with a real coordinate.
pub const COORD_NOT_SET: i32 = i32::MAX;

/// Size of the serialised Settings record in bytes.
pub const SETTINGS_BYTES: usize = 168;

const fn default_boot_tune() -> [(u8, u8); 48] {
    let mut t = [(0u8, 0u8); 48];
    t[0] = (1, 4); // A2  110Hz
    t[1] = (13, 4); // A3  220Hz
    t[2] = (20, 4); // E4  330Hz
    t[3] = (25, 4); // A4  440Hz
    t[4] = (32, 4); // E5  660Hz
    t[5] = (37, 10); // A5  880Hz, landing note
    t
}

impl Settings {
    pub const DEFAULT: Settings = Settings {
        sql_level: 3,
        tail_elimination: true,
        busy_lock: false,
        tx_forbid: false,
        key_auto_lock: 0,
        dual_standby: true,
        vox_switch: false,
        vox_level: 1,
        tot_level: 8,
        beeps_switch: true,
        roger_tone: 0,
        scramble_level: 0,
        rtone: 2,
        scan_mode: 1,
        rit_offset: 0,
        save_level: 0,
        backlight_time: 2,
        channel_display_mode: 2,
        ani_tx: false,
        rptrl: 0,
        vox_delay: 5, // 1.0s
        battery_cal_raw: 2537,
        side1_short: 5, // FM radio (physical "FM" button)
        side1_long: 0,  // None
        side2_short: 8, // Flashlight (physical flashlight button)
        side2_long: 0,  // None
        band_short: 6,  // Scan (physical "SCAN" button)
        band_long: 0,   // None
        band_lock: 0,
        boot_display_mode: 1,
        boot_sound_enabled: false,
        boot_text_line1: [0; 16],
        boot_text_line2: [0; 16],
        boot_tune: default_boot_tune(),
        obs_lat: COORD_NOT_SET,
        obs_lon: COORD_NOT_SET,
    };

    pub fn from_bytes(buf: &[u8; SETTINGS_BYTES]) -> Settings {
        Settings {
            sql_level: buf[0],
            tail_elimination: buf[1] != 0,
            busy_lock: buf[2] != 0,
            tx_forbid: buf[3] != 0,
            key_auto_lock: buf[4].min(3),
            dual_standby: buf[5] != 0,
            vox_switch: buf[6] != 0,
            vox_level: buf[7].clamp(1, 9),
            tot_level: buf[8],
            beeps_switch: buf[9] != 0,
            roger_tone: buf[10].min(2),
            scramble_level: buf[11],
            rtone: buf[12].min(3),
            scan_mode: buf[13].min(2),
            rit_offset: (buf[14] as i8).clamp(-127, 127),
            save_level: buf[15].min(4),
            backlight_time: buf[16].min(4),
            channel_display_mode: buf[17].min(2),
            ani_tx: buf[18] != 0,
            rptrl: buf[19].min(10),
            vox_delay: buf[20].min(15),
            battery_cal_raw: u16::from_le_bytes([buf[21], buf[22]]),
            side1_short: buf[23].min(10),
            side1_long: buf[24].min(10),
            side2_short: buf[25].min(10),
            side2_long: buf[26].min(10),
            band_short: buf[27].min(10),
            band_long: buf[28].min(10),
            band_lock: buf[29].min(9),
            boot_display_mode: buf[30].min(2),
            boot_sound_enabled: buf[31] != 0,
            obs_lat: coord_or_unset(
                i32::from_le_bytes([buf[32], buf[33], buf[34], buf[35]]),
                90_00000,
            ),
            obs_lon: coord_or_unset(
                i32::from_le_bytes([buf[36], buf[37], buf[38], buf[39]]),
                180_00000,
            ),
            boot_text_line1: {
                let mut l = [0u8; 16];
                l.copy_from_slice(&buf[40..56]);
                l
            },
            boot_text_line2: {
                let mut l = [0u8; 16];
                l.copy_from_slice(&buf[56..72]);
                l
            },
            boot_tune: {
                let mut t = [(0u8, 0u8); 48];
                for (i, pair) in t.iter_mut().enumerate() {
                    *pair = (buf[72 + i * 2], buf[72 + i * 2 + 1]);
                }
                t
            },
        }
    }

    pub fn to_bytes(self) -> [u8; SETTINGS_BYTES] {
        let mut buf = [0u8; SETTINGS_BYTES];
        buf[0] = self.sql_level;
        buf[1] = self.tail_elimination as u8;
        buf[2] = self.busy_lock as u8;
        buf[3] = self.tx_forbid as u8;
        buf[4] = self.key_auto_lock;
        buf[5] = self.dual_standby as u8;
        buf[6] = self.vox_switch as u8;
        buf[7] = self.vox_level;
        buf[8] = self.tot_level;
        buf[9] = self.beeps_switch as u8;
        buf[10] = self.roger_tone;
        buf[11] = self.scramble_level;
        buf[12] = self.rtone;
        buf[13] = self.scan_mode;
        buf[14] = self.rit_offset as u8;
        buf[15] = self.save_level;
        buf[16] = self.backlight_time;
        buf[17] = self.channel_display_mode;
        buf[18] = self.ani_tx as u8;
        buf[19] = self.rptrl;
        buf[20] = self.vox_delay;
        buf[21..23].copy_from_slice(&self.battery_cal_raw.to_le_bytes());
        buf[23] = self.side1_short;
        buf[24] = self.side1_long;
        buf[25] = self.side2_short;
        buf[26] = self.side2_long;
        buf[27] = self.band_short;
        buf[28] = self.band_long;
        buf[29] = self.band_lock;
        buf[30] = self.boot_display_mode;
        buf[31] = self.boot_sound_enabled as u8;
        buf[32..36].copy_from_slice(&self.obs_lat.to_le_bytes());
        buf[36..40].copy_from_slice(&self.obs_lon.to_le_bytes());
        buf[40..56].copy_from_slice(&self.boot_text_line1);
        buf[56..72].copy_from_slice(&self.boot_text_line2);
        for (i, &(tone, duration)) in self.boot_tune.iter().enumerate() {
            buf[72 + i * 2] = tone;
            buf[72 + i * 2 + 1] = duration;
        }
        buf
    }

    pub fn tot_seconds(&self) -> u16 {
        self.tot_level as u16 * 15
    }
}
