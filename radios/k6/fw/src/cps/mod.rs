mod frame;

use cortex_m::peripheral::syst::SystClkSource;
use cortex_m::peripheral::SYST;
use display_interface::WriteOnlyDataCommand;

use crate::app::App;
use crate::device::display::Display;
use crate::device::storage::Storage;
use crate::flash_map::{self, addr, MAX_SATELLITES, SAT_RECORD_SIZE};
use crate::hal::clock::SYSCLK_HZ;
use crate::hal::delay;
use crate::hal::uart::{self, DebugUart};
use crate::ui;

const HANDSHAKE: &[u8] = b"PROGRAMBF-K6AURA";
const ACK: u8 = 0x06;
const IDLE_TIMEOUT_MS: u32 = 2000;

/// Virtual CPS address base for satellite records.
/// Satellite n (0..19) lives at `SAT_CPS_BASE + n * SAT_RECORD_SIZE`.
const SAT_CPS_BASE: u32 = 0xD000;

pub struct HandshakeDetector {
    matched: usize,
    key: &'static [u8],
}

impl HandshakeDetector {
    pub const fn new() -> Self {
        HandshakeDetector { matched: 0, key: HANDSHAKE }
    }

    /// The cable session's detector (`run_cable_session`): blowfang's own string, so CHIRP's
    /// Aura driver never opens it and the installer never opens Aura's session
    #[cfg(feature = "spilib")]
    pub const fn cable() -> Self {
        HandshakeDetector { matched: 0, key: CABLE_HANDSHAKE }
    }

    /// Drains every byte currently buffered from USART1 looking for the
    /// handshake string. Returns `true` the instant it completes.
    pub fn poll(&mut self) -> bool {
        while let Some(byte) = uart::take_byte() {
            if byte == self.key[self.matched] {
                self.matched += 1;
                if self.matched == self.key.len() {
                    self.matched = 0;
                    return true;
                }
            } else {
                self.matched = usize::from(byte == self.key[0]);
            }
        }
        false
    }
}

/// Takes over the MCU for a CPS write session. Never returns: the session
/// always ends in `hal::txdog::reset()` (`SCB::sys_reset()`), either on an explicit end command or
/// after 2s of silence, so the rest of `main()` never needs to unwind
/// whatever partial state a session left behind.
pub fn run_session<DI: WriteOnlyDataCommand>(
    app: &mut App,
    syst: &mut SYST,
    display: &mut Display<'_, DI>,
    dbg: &mut DebugUart,
) -> ! {
    app.radio_mut().rf_off(syst);
    app.storage_mut().reset_channel_write_session();
    app.storage_mut().reset_sat_write_session();
    dbg.write_byte(ACK);

    ui::cps::draw_programming(display.as_draw_target());
    display.flush();

    syst.set_clock_source(SystClkSource::Core);
    syst.set_reload(SYSCLK_HZ / 1000 - 1);
    syst.clear_current();
    syst.enable_counter();

    let mut reader = frame::FrameReader::new();
    let mut idle_ms: u32 = 0;

    loop {
        if syst.has_wrapped() {
            idle_ms += 1;
            if idle_ms >= IDLE_TIMEOUT_MS {
                crate::hal::txdog::reset();
            }
        }

        let Some(byte) = uart::take_byte() else {
            continue;
        };
        idle_ms = 0;

        let frame = match reader.feed(byte) {
            frame::FeedResult::Pending => continue,
            frame::FeedResult::CrcError { addr } => {
                send_error(dbg, addr, frame::ERR_BAD_CRC);
                continue;
            }
            frame::FeedResult::Frame(frame) => frame,
        };

        match frame.cmd {
            frame::CMD_READ => handle_read(app, dbg, frame.addr),
            frame::CMD_WRITE => handle_write(app, dbg, frame.addr, &frame.data[..frame.len]),
            frame::CMD_READ_BOOT => handle_read_boot(dbg, frame.addr),
            frame::CMD_READ_LOGO => handle_read_logo(app, dbg, frame.addr),
            frame::CMD_WRITE_LOGO => {
                handle_write_logo(app, dbg, frame.addr, &frame.data[..frame.len])
            }
            frame::CMD_SSTV_ERASE => handle_sstv_erase(app, dbg),
            frame::CMD_SSTV_WRITE => {
                handle_sstv_write(app, dbg, frame.addr, &frame.data[..frame.len])
            }
            frame::CMD_READ_FLASH_RAW => handle_read_flash_raw(app.storage_mut(), dbg, frame.addr),
            frame::CMD_END => {
                send_response(dbg, frame::CMD_END, frame.addr, &[]);
                delay::ms(syst, 5); // let the ACK bytes drain out before reset
                crate::hal::txdog::reset();
            }
            _ => send_error(dbg, frame.addr, frame::ERR_BAD_LEN),
        }
    }
}

/// Minimal read-only session usable before `App` exists, during the
/// first-boot format-confirmation prompt in `main.rs`: understands only
/// the handshake, `CMD_READ_FLASH_RAW` and `CMD_END`, so a host tool can
/// back up the whole chip before anything gets erased. Unlike
/// `run_session`, this *returns* (rather than resetting the MCU) once the
/// host goes idle or sends `CMD_END`, there's no App/radio state to
/// unwind at this point in boot, so `main.rs` can just go back to waiting
/// for the MENU press.
pub fn run_preboot_dump_session(storage: &mut Storage, syst: &mut SYST, dbg: &mut DebugUart) {
    dbg.write_byte(ACK);

    syst.set_clock_source(SystClkSource::Core);
    syst.set_reload(SYSCLK_HZ / 1000 - 1);
    syst.clear_current();
    syst.enable_counter();

    let mut reader = frame::FrameReader::new();
    let mut idle_ms: u32 = 0;

    loop {
        if syst.has_wrapped() {
            idle_ms += 1;
            if idle_ms >= IDLE_TIMEOUT_MS {
                return;
            }
        }

        let Some(byte) = uart::take_byte() else {
            continue;
        };
        idle_ms = 0;

        let frame = match reader.feed(byte) {
            frame::FeedResult::Pending => continue,
            frame::FeedResult::CrcError { addr } => {
                send_error(dbg, addr, frame::ERR_BAD_CRC);
                continue;
            }
            frame::FeedResult::Frame(frame) => frame,
        };

        match frame.cmd {
            frame::CMD_READ_FLASH_RAW => handle_read_flash_raw(storage, dbg, frame.addr),
            frame::CMD_END => {
                send_response(dbg, frame::CMD_END, frame.addr, &[]);
                return;
            }
            _ => send_error(dbg, frame.addr, frame::ERR_BAD_LEN),
        }
    }
}

/// A `READ` request never carries data, so the response length is determined
/// purely by which region `wire_addr` falls into, not by anything the request
/// claims.
fn handle_read(app: &mut App, dbg: &mut DebugUart, wire_addr: u16) {
    let logical = wire_addr as u32;
    let mut buf = [0u8; frame::MAX_DATA];

    let len = if logical < addr::VFO_INFO_ADDR {
        read_channel(app, logical, &mut buf)
    } else if logical == addr::VFO_INFO_ADDR {
        let raw = app.storage_mut().load_vfo_raw().unwrap_or([0xFF; 64]);
        buf[..64].copy_from_slice(&raw);
        Some(64)
    } else if logical == addr::RADIO_IMFOS_ADDR {
        let settings = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        buf[..28].copy_from_slice(&settings.to_bytes()[..28]);
        Some(28)
    } else if logical == addr::FM_ADDR {
        let channels = app
            .storage_mut()
            .load_fm_channels()
            .unwrap_or([flash_map::FM_CHANNEL_EMPTY; flash_map::FM_CHANNEL_COUNT]);
        let n = flash_map::FM_CHANNEL_COUNT * 2;
        for (chunk, v) in buf[..n].chunks_exact_mut(2).zip(channels.iter()) {
            chunk.copy_from_slice(&v.to_le_bytes());
        }
        Some(n)
    } else if logical == addr::BOOT_TEXT_ADDR {
        let settings = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        buf[0] = settings.boot_display_mode;
        buf[1] = settings.boot_sound_enabled as u8;
        buf[2..18].copy_from_slice(&settings.boot_text_line1);
        buf[18..34].copy_from_slice(&settings.boot_text_line2);
        Some(34)
    } else if logical == addr::BOOT_TUNE_ADDR {
        let settings = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        for (i, &(tone, duration)) in settings.boot_tune.iter().enumerate() {
            buf[i * 2] = tone;
            buf[i * 2 + 1] = duration;
        }
        Some(settings.boot_tune.len() * 2)
    } else if logical == addr::BATTERY_CAL_ADDR {
        let settings = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        buf[..2].copy_from_slice(&settings.battery_cal_raw.to_le_bytes());
        Some(2)
    } else if logical == addr::APRS_SETTINGS_ADDR {
        let settings = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        buf[..52].copy_from_slice(&settings.to_bytes()[160..212]);
        Some(52)
    } else if (SAT_CPS_BASE..SAT_CPS_BASE + MAX_SATELLITES as u32 * SAT_RECORD_SIZE as u32)
        .contains(&logical)
        && logical
            .wrapping_sub(SAT_CPS_BASE)
            .is_multiple_of(SAT_RECORD_SIZE as u32)
    {
        let idx = (logical - SAT_CPS_BASE) as usize / SAT_RECORD_SIZE;
        let sats = app.storage_mut().load_satellites();
        let bytes = if let Some(sat) = sats[idx] {
            sat.to_bytes()
        } else {
            [0xFFu8; SAT_RECORD_SIZE]
        };
        let len = SAT_RECORD_SIZE;
        buf[..len].copy_from_slice(&bytes);
        Some(len)
    } else {
        None
    };

    match len {
        Some(len) => send_response(dbg, frame::CMD_READ, wire_addr, &buf[..len]),
        None => send_error(dbg, wire_addr, frame::ERR_BAD_ADDR),
    }
}

fn read_channel(app: &mut App, logical: u32, buf: &mut [u8; frame::MAX_DATA]) -> Option<usize> {
    if !logical.is_multiple_of(addr::CHAN_SIZE) {
        return None;
    }
    let num = (logical / addr::CHAN_SIZE) as u16;
    let len = addr::CHAN_SIZE as usize;
    buf[..len].copy_from_slice(&app.storage_mut().read_channel(num).to_bytes());
    Some(len)
}

fn handle_write(app: &mut App, dbg: &mut DebugUart, wire_addr: u16, data: &[u8]) {
    let logical = wire_addr as u32;

    let ok = if logical < addr::VFO_INFO_ADDR {
        write_channel(app, logical, data)
    } else if logical == addr::VFO_INFO_ADDR && data.len() == 64 {
        let raw: [u8; 64] = data.try_into().unwrap();
        app.storage_mut().save_vfo_raw(&raw);
        true
    } else if logical == addr::RADIO_IMFOS_ADDR && data.len() == 28 {
        let current = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        let mut raw = current.to_bytes();
        raw[..28].copy_from_slice(data);
        app.storage_mut()
            .save_settings(&flash_map::Settings::from_bytes(&raw));
        true
    } else if logical == addr::FM_ADDR && data.len() == flash_map::FM_CHANNEL_COUNT * 2 {
        let mut channels = [flash_map::FM_CHANNEL_EMPTY; flash_map::FM_CHANNEL_COUNT];
        for (v, pair) in channels.iter_mut().zip(data.chunks_exact(2)) {
            *v = u16::from_le_bytes([pair[0], pair[1]]);
        }
        app.storage_mut().save_fm_channels(&channels);
        true
    } else if logical == addr::BOOT_TEXT_ADDR && data.len() == 34 {
        let mut settings = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        settings.boot_display_mode = data[0].min(3);
        settings.boot_sound_enabled = data[1] != 0;
        settings.boot_text_line1.copy_from_slice(&data[2..18]);
        settings.boot_text_line2.copy_from_slice(&data[18..34]);
        app.storage_mut().save_settings(&settings);
        true
    } else if logical == addr::BOOT_TUNE_ADDR && data.len() == 96 {
        let mut settings = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        for (i, pair) in settings.boot_tune.iter_mut().enumerate() {
            *pair = (data[i * 2], data[i * 2 + 1]);
        }
        app.storage_mut().save_settings(&settings);
        true
    } else if logical == addr::BATTERY_CAL_ADDR && data.len() == 2 {
        let mut settings = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        settings.battery_cal_raw = u16::from_le_bytes([data[0], data[1]]);
        app.storage_mut().save_settings(&settings);
        true
    } else if logical == addr::APRS_SETTINGS_ADDR && data.len() == 52 {
        let current = app
            .storage_mut()
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        let mut raw = current.to_bytes();
        raw[160..212].copy_from_slice(data);
        app.storage_mut()
            .save_settings(&flash_map::Settings::from_bytes(&raw));
        true
    } else if (SAT_CPS_BASE..SAT_CPS_BASE + MAX_SATELLITES as u32 * SAT_RECORD_SIZE as u32)
        .contains(&logical)
        && logical
            .wrapping_sub(SAT_CPS_BASE)
            .is_multiple_of(SAT_RECORD_SIZE as u32)
        && data.len() == SAT_RECORD_SIZE
    {
        let idx = (logical - SAT_CPS_BASE) as usize / SAT_RECORD_SIZE;
        let record: [u8; SAT_RECORD_SIZE] = data.try_into().unwrap();
        app.storage_mut().write_satellite(idx, &record);
        true
    } else {
        false
    };

    if ok {
        send_response(dbg, frame::CMD_WRITE, wire_addr, &[]);
    } else {
        send_error(dbg, wire_addr, frame::ERR_BAD_ADDR);
    }
}

fn write_channel(app: &mut App, logical: u32, data: &[u8]) -> bool {
    if !logical.is_multiple_of(addr::CHAN_SIZE) || data.len() != addr::CHAN_SIZE as usize {
        return false;
    }
    let num = (logical / addr::CHAN_SIZE) as u16;
    let raw: [u8; addr::CHAN_SIZE as usize] = data.try_into().unwrap();
    app.storage_mut()
        .write_channel(num, &flash_map::Channel::from_bytes(&raw));
    true
}

const BOOTLOADER_BASE: u32 = 0x0800_0000;
const BOOTLOADER_LEN: u32 = 0x2000;
const BOOTLOADER_CHUNK: u32 = 32;

const BOOT_LOGO_CHUNK: u32 = 64;

fn handle_read_logo(app: &mut App, dbg: &mut DebugUart, wire_addr: u16) {
    let offset = wire_addr as u32;
    if !offset.is_multiple_of(BOOT_LOGO_CHUNK)
        || offset + BOOT_LOGO_CHUNK > flash_map::BOOT_LOGO_SIZE as u32
    {
        send_error(dbg, wire_addr, frame::ERR_BAD_ADDR);
        return;
    }
    let mut buf = [0u8; BOOT_LOGO_CHUNK as usize];
    app.storage_mut().read_boot_logo_chunk(offset, &mut buf);
    send_response(dbg, frame::CMD_READ_LOGO, wire_addr, &buf);
}

/// Writing offset 0 erases the logo's whole sector first (see
/// `Storage::erase_boot_logo`), so a host tool must always send chunks
/// starting from offset 0 and in order for a given image.
fn handle_write_logo(app: &mut App, dbg: &mut DebugUart, wire_addr: u16, data: &[u8]) {
    let offset = wire_addr as u32;
    let ok = offset.is_multiple_of(BOOT_LOGO_CHUNK)
        && data.len() == BOOT_LOGO_CHUNK as usize
        && offset + BOOT_LOGO_CHUNK <= flash_map::BOOT_LOGO_SIZE as u32;
    if !ok {
        send_error(dbg, wire_addr, frame::ERR_BAD_ADDR);
        return;
    }
    if offset == 0 {
        app.storage_mut().erase_boot_logo();
    }
    app.storage_mut().write_boot_logo_chunk(offset, data);
    send_response(dbg, frame::CMD_WRITE_LOGO, wire_addr, &[]);
}

fn handle_read_boot(dbg: &mut DebugUart, wire_addr: u16) {
    let offset = wire_addr as u32;
    if !offset.is_multiple_of(BOOTLOADER_CHUNK) || offset + BOOTLOADER_CHUNK > BOOTLOADER_LEN {
        send_error(dbg, wire_addr, frame::ERR_BAD_ADDR);
        return;
    }
    let mut buf = [0u8; BOOTLOADER_CHUNK as usize];
    let src = (BOOTLOADER_BASE + offset) as *const u8;
    unsafe { core::ptr::copy_nonoverlapping(src, buf.as_mut_ptr(), buf.len()) };
    send_response(dbg, frame::CMD_READ_BOOT, wire_addr, &buf);
}

/// XM25QH16C: 16Mbit = 2MiB
const FLASH_CHIP_SIZE: u32 = 0x20_0000;
/// `wire_addr` is a block index at this granularity, not a byte offset --
/// 2MB doesn't fit a 16-bit byte offset
const FLASH_RAW_CHUNK: u32 = 64;

fn handle_read_flash_raw(storage: &mut Storage, dbg: &mut DebugUart, wire_addr: u16) {
    let addr = wire_addr as u32 * FLASH_RAW_CHUNK;
    if addr + FLASH_RAW_CHUNK > FLASH_CHIP_SIZE {
        send_error(dbg, wire_addr, frame::ERR_BAD_ADDR);
        return;
    }
    let mut buf = [0u8; FLASH_RAW_CHUNK as usize];
    storage.read_raw(addr, &mut buf);
    send_response(dbg, frame::CMD_READ_FLASH_RAW, wire_addr, &buf);
}

fn send_response(dbg: &mut DebugUart, cmd: u8, addr: u16, data: &[u8]) {
    let mut out = [0u8; 8 + frame::MAX_DATA];
    let n = frame::encode(&mut out, cmd, addr, data);
    dbg.write_bytes(&out[..n]);
}

fn send_error(dbg: &mut DebugUart, addr: u16, code: u8) {
    send_response(dbg, frame::CMD_ERROR, addr, &[code]);
}

/// SSTV chunk size: 64 bytes per frame.
const SSTV_CHUNK: u32 = 64;

fn handle_sstv_erase(app: &mut App, dbg: &mut DebugUart) {
    app.storage_mut().erase_sstv_image();
    send_response(dbg, frame::CMD_SSTV_ERASE, 0, &[]);
}

fn handle_sstv_write(app: &mut App, dbg: &mut DebugUart, wire_addr: u16, data: &[u8]) {
    let chunk_idx = wire_addr as u32;
    if data.len() != SSTV_CHUNK as usize {
        send_error(dbg, wire_addr, frame::ERR_BAD_LEN);
        return;
    }
    let offset = chunk_idx * SSTV_CHUNK;
    app.storage_mut().write_sstv_chunk(offset, data);
    send_response(dbg, frame::CMD_SSTV_WRITE, wire_addr, &[]);
}

// ---- blowfang's cable session (docs/manual/install.md, tools/k6_install.py) ----
//
// The whole library load over the programming cable alone, for a K6 owner with no second
// radio: EXPLORE's code pages, the modules and the help decks go into the SPI module library
// (c/k6/k6_lib.h) as they do over the air, signed with the radio's key and checked by the same
// loader (km_packet: the tag, the serial), then written by the same kl_install (its sectors
// inside 0x110000-0x18FFFF only, read back). The cable adds no new trust and no new SPI write:
// it is one more way for the same signed chunks to reach km_packet.
//
// The PC opens it with CABLE_HANDSHAKE while the radio is in EXPLORE or the arena (main's
// pager-only loop polls for it, never while keyed). The radio answers ACK and takes over the
// MCU: receiver off, nothing transmits, and the PTT line (PA10, the cable's PC->radio wire,
// which the serial data pulls low) is never read, so the data can't key the radio. The 16
// handshake bytes take 1.4 ms, far shorter than the 5 samples ~10 ms apart PTT's debouncer
// wants. Frames are Aura's CPS frames (frame.rs: A6 cmd addr len data CRC-16/XMODEM), one at a
// time, each answered:
//
//   'V' info: "BLWF", protocol 1, library slots, the shared block's size (u16 LE), then the
//       image's 8-byte build ID (the voice + EXPLORE image; zeros in the others), which its
//       code pages must end with
//   'M' one module chunk: data = a whitened 64-byte L packet (modem/k6mod.py packets, magic
//       0xAB). Answer: r (km_packet's, i8), have, count, 0, then the install's result (i32 LE:
//       the image's length or k6_lib.h KL_*; 0 until the last chunk). An image without
//       KM_F_LIB checks (r=3: the key is right) and is dropped, never run
//   'D' 64 bytes of the SPI flash at addr * 64 (as Aura's CMD_READ_FLASH_RAW): read only, for
//       a backup and to verify the slots
//   'E' end: answered, then the radio restarts (the stock bootloader's UPDATE window follows
//       every reset, so tools/k6_install.py can flash an image next)
//
// No frame for CABLE_IDLE_MS also restarts it. Every session ends in a restart, so nothing it
// left in the shared block (EXPLORE's state, a page in the pool) is ever used again.

#[cfg(feature = "spilib")]
pub const CABLE_HANDSHAKE: &[u8] = b"PROGRAMBF-K6BLWF";
#[cfg(feature = "spilib")]
const CABLE_IDLE_MS: u32 = 5000;
#[cfg(feature = "spilib")]
const CMD_CABLE_INFO: u8 = 0x56; // 'V'
#[cfg(feature = "spilib")]
const CMD_CABLE_CHUNK: u8 = 0x4D; // 'M'

/// One frame to the PC through the report's queue (hal::uart), so a report line the loader
/// prints (K6 LIB put) never splits it; the PC skips the text between frames (no 0xA6 in it)
#[cfg(feature = "spilib")]
fn cable_send(cmd: u8, addr: u16, data: &[u8]) {
    let mut out = [0u8; 8 + FLASH_RAW_CHUNK as usize];
    let n = frame::encode(&mut out, cmd, addr, data);
    for &b in &out[..n] {
        uart::send_byte(b);
    }
}

#[cfg(feature = "spilib")]
pub fn run_cable_session<DI: WriteOnlyDataCommand>(
    app: &mut App,
    syst: &mut SYST,
    display: &mut Display<'_, DI>,
) -> ! {
    app.radio_mut().rf_off(syst);
    app.cable_begin();
    rl!("K6 CABLE on");
    uart::flush();
    uart::send_byte(ACK);
    crate::screen!(display, |lcd| ui::cps::draw_lines(lcd, "BLOWFANG", "CABLE LOAD..."));

    syst.set_clock_source(SystClkSource::Core);
    syst.set_reload(SYSCLK_HZ / 1000 - 1);
    syst.clear_current();
    syst.enable_counter();

    let mut reader = frame::FrameReader::new();
    let mut idle_ms: u32 = 0;
    loop {
        if syst.has_wrapped() {
            idle_ms += 1;
            if idle_ms >= CABLE_IDLE_MS {
                rl!("K6 CABLE idle");
                uart::flush();
                crate::hal::txdog::reset();
            }
        }
        let Some(byte) = uart::take_byte() else {
            continue;
        };
        idle_ms = 0;
        let f = match reader.feed(byte) {
            frame::FeedResult::Pending => continue,
            frame::FeedResult::CrcError { addr } => {
                cable_send(frame::CMD_ERROR, addr, &[frame::ERR_BAD_CRC]);
                continue;
            }
            frame::FeedResult::Frame(f) => f,
        };
        match f.cmd {
            CMD_CABLE_INFO => {
                let mut d = [0u8; 16];
                d[..4].copy_from_slice(b"BLWF");
                d[4] = 1;
                d[5] = 32;
                d[6..8].copy_from_slice(&(crate::hal::arena::ARENA_BYTES as u16).to_le_bytes());
                #[cfg(feature = "voice-explore")]
                {
                    extern "C" {
                        static k6_build_id: [u8; 8];
                    }
                    d[8..].copy_from_slice(unsafe { &*core::ptr::addr_of!(k6_build_id) });
                }
                cable_send(CMD_CABLE_INFO, f.addr, &d);
            }
            CMD_CABLE_CHUNK if f.len == 64 => {
                let (r, have, count, put) = app.cable_chunk(&f.data[..64]);
                let mut d = [r as u8, have, count, 0, 0, 0, 0, 0];
                d[4..].copy_from_slice(&put.to_le_bytes());
                cable_send(CMD_CABLE_CHUNK, f.addr, &d);
            }
            frame::CMD_READ_FLASH_RAW if (f.addr as u32) < FLASH_CHIP_SIZE / FLASH_RAW_CHUNK => {
                let mut buf = [0u8; FLASH_RAW_CHUNK as usize];
                app.storage_mut().read_raw(f.addr as u32 * FLASH_RAW_CHUNK, &mut buf);
                cable_send(frame::CMD_READ_FLASH_RAW, f.addr, &buf);
            }
            frame::CMD_END => {
                cable_send(frame::CMD_END, f.addr, &[]);
                rl!("K6 CABLE end");
                uart::flush();
                delay::ms(syst, 5);
                crate::hal::txdog::reset();
            }
            _ => cable_send(frame::CMD_ERROR, f.addr, &[frame::ERR_BAD_LEN]),
        }
    }
}
