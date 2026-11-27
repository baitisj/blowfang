//! VOICE MSG screen (app/pager.rs): link settings, message progress, the
//! last packet's sync / CRC / magic, and after a playback the decoder's time
//! per 20 ms frame and the free RAM, for the first on-air tests.
//!
//! ```text
//! VOICE MSG   439.50000
//! F L 1800 i-x-s+ e+ +0     (PHY, frames, baud, chip invert, soft invert, byte swap, emphasis, dB)
//! ID 42  640/640  80/80
//! SYNC P CRC ok A7 TO 0
//! ok 81 bad 2 FREE 3120
//! MENU play   PTT test
//! ```
//!
//! Key 6, the raw view (the last packet as read, and its bit errors against the spec's
//! test message):
//!
//! ```text
//! R0 58A31D9AEDC33324
//! R8 FB58E17D78974628
//! SYNC N A7 64 av3.5
//! BE   3 s+x+ 00010359
//! ```
//!
//! `#` types a frequency (kHz, like Aura's VFO entry); the top line shows it:
//!
//! ```text
//! FREQ 439.5__ MHz
//! ```

use super::TextBuf;
use crate::app;
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::Rectangle;

fn line<D>(lcd: &mut D, y: i32, t: &str)
where
    D: DrawTarget<Color = BinaryColor>,
{
    // the stock 5x7 font (ui::glyph), caps on the rows FONT_6X10's took at baseline y
    super::glyph::text(lcd, 1, y - 6, t, false, true);
}

/// The module list (MENU, app/pager.rs): EXPLORE's title bar in the 5x7, six rows in the 4x6
/// (the selected one inverted), the keys in the 4x6, as the editor (Jeff's layout D)
///
/// ```text
/// MODULES 3/27
/// EXPLORE
/// ARENA
/// SURF
/// AIS SHIPS
/// AIS CLOCK
/// APRS CLOCK
/// MENU-RUN EXIT-BACK
/// ```
#[cfg(all(feature = "explore", feature = "spilib"))]
pub fn draw_menu<D>(lcd: &mut D)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let (_, sel, rows) = app::pager::menu_state();
    lcd.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 64)), BinaryColor::Off).ok();
    // EXPLORE's title bar (the 5x7 in a 10-row bar), then the rows as the editor draws them
    // (Jeff's layout D, 2026-11-14): the 4x6, 7 rows apart, the selected one inverted
    lcd.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 10)), BinaryColor::On).ok();
    let mut b: TextBuf<24> = TextBuf::new();
    out!(b, "MODULES {}/{}", sel + 1, rows);
    super::glyph::text(lcd, 1, 1, b.as_str(), false, false);
    let top = if sel < 3 { 0 } else { (sel - 2).min(rows.saturating_sub(MENU_ROWS)) };
    for k in 0..MENU_ROWS {
        let i = top + k;
        if i >= rows {
            break;
        }
        let y = 12 + 7 * k as i32;
        let mut name = [0u8; 12];
        let n = app::pager::menu_row(i, &mut name);
        let t = crate::out::ascii(&name[..n]);
        if i == sel {
            lcd.fill_solid(&Rectangle::new(Point::new(0, y - 1), Size::new(128, 7)), BinaryColor::On).ok();
        }
        super::glyph::text(lcd, 1, y, t, true, i != sel);
    }
    super::glyph::scroll_bar(lcd, 11, 7 * MENU_ROWS as u32, top as u32, MENU_ROWS as u32, rows as u32);
    super::glyph::text(lcd, 1, 58, "MENU-RUN EXIT-BACK", true, true);
}

/// the module list's rows on screen
const MENU_ROWS: u8 = 6;

/// The explore images' VOICE MSG: the RAM arena (the release plan, Jeff 2026-11-13), two lines
/// and a third only while it's busy. The serial report keeps the link details.
///
/// ```text
///   BLOWFANG RAM ARENA
///   EXIT TO EXPLORER
///   0 TO STAY HERE       (after key 0: STAYING HERE. / EXIT TO EXPLORE, no fallback)
///   MOD 12/48            (or PLAYING  EXIT STOP, or PAGES MISSING)
/// ```
#[cfg(feature = "explore")]
fn draw_arena<D>(lcd: &mut D, app: &app::App)
where
    D: DrawTarget<Color = BinaryColor>,
{
    use app::pager::Busy;
    line(lcd, 19, "  BLOWFANG RAM ARENA");
    let busy = app::pager::arena_busy(app);
    let from = app::pager::play_from();
    // a receiver module's message playing: its caller (module API level 11, Jeff 2026-11-19)
    if matches!(busy, app::pager::Busy::Playing) && !from.is_empty() {
        let mut b: TextBuf<24> = TextBuf::new();
        out!(b, "  FROM {}", crate::out::ascii(from));
        line(lcd, 29, b.as_str());
    } else if app::pager::arena_stay() {
        line(lcd, 29, "  STAYING HERE.");
        line(lcd, 39, "  EXIT TO EXPLORE");
    } else {
        line(lcd, 29, "  EXIT TO EXPLORER");
        line(lcd, 39, "  0 TO STAY HERE");
    }
    let mut b: TextBuf<24> = TextBuf::new();
    match busy {
        Busy::Module(have, n) => {
            out!(b, "  MOD {}/{}", have, n);
        }
        Busy::Playing => {
            out!(b, "  PLAYING  EXIT STOP");
        }
        Busy::PagesMissing => {
            out!(b, "  PAGES MISSING");
        }
        Busy::Idle => {}
    }
    line(lcd, 49, b.as_str());
}

pub fn draw_pager<D>(lcd: &mut D, app: &app::App)
where
    D: DrawTarget<Color = BinaryColor>,
{
    lcd.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 64)), BinaryColor::Off).ok();
    let p = app.pager();
    // the arena, unless a key asked for the details: # (a frequency), 6 (the raw view)
    #[cfg(feature = "explore")]
    if !p.freq_entry && !p.raw_view && !p.cal_pending {
        draw_arena(lcd, app);
        return;
    }
    let st = &p.status;

    let mut b: TextBuf<24> = TextBuf::new();
    if let Some(d) = app.pager_freq_digits() {
        // typing a frequency: 6 digits of kHz, e.g. FREQ 439.5__ MHz
        out!(b, "FREQ ");
        for i in 0..6 {
            if i == 3 {
                out!(b, ".");
            }
            match d.get(i) {
                Some(&v) => out!(b, "{}", v),
                None => out!(b, "_"),
            };
        }
        out!(b, " MHz");
    } else {
        let hz = app.master_freq_hz();
        #[cfg(feature = "hiss")]
        let title = if app::pager::hiss_band_on() { "VOICE MSG" } else { "NO HISS  " };
        #[cfg(not(feature = "hiss"))]
        let title = "VOICE MSG";
        out!(b, "{} {:>4}.{:05}", title, hz / 1_000_000, (hz % 1_000_000) / 10);
    }
    line(lcd, 9, b.as_str());

    let mut b: TextBuf<24> = TextBuf::new();
    out!(
        b,
        "{} {} {} i{}x{}s{} e{} {:+}",
        if p.d2400 { 'D' } else { 'F' }, // the rung: F/1800 by default, D/2400 for a session
        if p.xl { 'X' } else { 'L' },
        p.baud(), // the REG_72 rate in force (F 1800 by default)
        if p.invert { '+' } else { '-' },
        if p.soft_inv { '+' } else { '-' },
        if p.swap { '+' } else { '-' },
        if p.emphasis { '+' } else { '-' },
        p.vol_db()
    );
    line(lcd, 19, b.as_str());

    if p.raw_view {
        // key 6: the last packet's first 16 bytes as the FIFO gave them (after the s swap,
        // before the x inversion), for comparing with the transmitted packet
        for (row, y) in [(0usize, 29), (1, 39)] {
            let mut b: TextBuf<24> = TextBuf::new();
            out!(b, "R{:<2}", row * 8);
            for v in &p.raw[row * 8..row * 8 + 8] {
                out!(b, "{:02X}", v);
            }
            line(lcd, y, b.as_str());
        }
        // the sync, magic byte and length, and the mean bit errors per packet against the
        // spec's test message since the last link change (calibration packets only)
        let mut b: TextBuf<24> = TextBuf::new();
        out!(b, "SYNC {} {:02X} {}", p.last_sync as char, p.last_magic, p.raw_len);
        if p.ber_n > 0 {
            let m = p.ber_sum * 10 / p.ber_n as u32;
            out!(b, " av{}.{}", m / 10, m % 10);
        }
        line(lcd, 49, b.as_str());
        // the last packet's bit errors, the s and x of the closest reading, and the errors
        // in each eighth of the packet: 0-9, a-z for 10-35, + above
        let mut b: TextBuf<24> = TextBuf::new();
        let e = &p.ber;
        if e.errors < 0 {
            out!(b, "BE -");
        } else {
            out!(
                b,
                "BE{:>4} s{}x{} ",
                e.errors,
                if e.swap != 0 { '+' } else { '-' },
                if e.invert != 0 { '+' } else { '-' }
            );
            for &n in e.block.iter() {
                let c = match n {
                    0..=9 => (b'0' + n) as char,
                    10..=35 => (b'a' + n - 10) as char,
                    _ => '+',
                };
                out!(b, "{}", c);
            }
        }
        line(lcd, 60, b.as_str());
        return;
    }

    let mut b: TextBuf<24> = TextBuf::new();
    if st.have != 0 {
        out!(
            b,
            "ID {:02X} {:>4}/{:<4}{:>3}/{}",
            st.msg_id, st.frames_got, st.stored, st.packets_got, st.packets_total
        );
    } else {
        out!(b, "no message ({}s max)", p.store_frames / 50);
    }
    line(lcd, 29, b.as_str());

    let mut b: TextBuf<24> = TextBuf::new();
    if !p.chip_ok {
        out!(b, "RF chip not FD6818");
    } else if p.cb < 1 {
        out!(b, "NO CODEBOOK ({})", p.cb);
    } else if p.last_result == i8::MIN {
        out!(b, "SYNC {} listening", p.last_sync as char);
    } else {
        let crc = match p.last_result {
            -1 => "BAD",
            -2 => "ok?",
            _ => "ok",
        };
        out!(b, "SYNC {} CRC {} {:02X} TO {}", p.last_sync as char, crc, p.last_magic, p.timeouts);
    }
    line(lcd, 39, b.as_str());

    let mut b: TextBuf<24> = TextBuf::new();
    if p.dec_max > 0 {
        // decoder time per 20 ms frame, ms (100 us ticks); 20 ms = all of the CPU
        out!(
            b,
            "DEC {}.{}/{}.{}ms {}",
            p.dec_mean_x10 / 100,
            (p.dec_mean_x10 / 10) % 10,
            p.dec_max / 10,
            p.dec_max % 10,
            p.ram_free
        );
    } else {
        // and UTC once a module (aisclk, aprsclk) has set the clock, else the image's sotto
        // commit, as on the serial report's BOOT/CFG lines
        out!(b, "ok {} bad {} ", st.good_packets, st.bad_packets);
        match crate::app::pager::clk_now() {
            Some((ms, _, _)) => {
                let s = ms / 1000;
                out!(b, "{:02}:{:02}:{:02}Z", s / 3600, s / 60 % 60, s % 60)
            }
            None => out!(b, "{}", crate::app::pager::build_short()),
        };
    }
    line(lcd, 49, b.as_str());

    let footer = if p.freq_entry {
        "MENU set  EXIT del"
    } else if p.cal_pending {
        "TX test packet..."
    } else if p.play_pending {
        "PLAYING  EXIT stop"
    } else if p.cal_result == b'X' {
        "TX not allowed here"
    } else {
        "5 play  PTT test"
    };
    line(lcd, 60, footer);
}

/// a RAM module's screen (module API level 6, app/pager.rs ModScreen): line 0 as an inverted
/// title bar, lines 1-5 below at the explorer's baselines (y = 9, 19 ... 59), a bar after a
/// line's text, and the module's waterfall rows over lines 2-4 (not in the `lean` images)
#[cfg(all(not(feature = "lean"), not(shared_lcd)))]
pub fn draw_mod_screen<D>(lcd: &mut D, s: &app::pager::ModScreen)
where
    D: DrawTarget<Color = BinaryColor>,
{
    use app::pager::{MOD_COLS, MOD_GLYPH_COLS, MOD_LINES, WF_TOP};
    lcd.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 64)), BinaryColor::Off).ok();
    lcd.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 11)), BinaryColor::On).ok();
    let wf = s.wf_rows > 0 && !s.wf.is_null();
    for i in 0..MOD_LINES {
        if wf && (2..5).contains(&i) {
            continue;
        }
        let t = &s.text[i];
        let mut n = 0;
        while n < MOD_COLS && t[n] != 0 {
            n += 1;
        }
        let y = 9 + 10 * i as i32;
        let ink = if i != 0 { BinaryColor::On } else { BinaryColor::Off };
        // the title in FONT_6X10, the lines in the stock 5x7 (Jeff 2026-11-14)
        if i == 0 {
            super::glyph::title(lcd, 1, y - 7, &t[..n], false);
        } else {
            super::glyph::text_b(lcd, 1, y - 6, &t[..n], false, true);
        }
        // the module's glyphs (level 9): 0x80 + k, 6 columns from the cell's x - 1, rows y - 8 ..
        for (k, &b) in t[..n].iter().enumerate() {
            let g = b.wrapping_sub(0x80) as i32;
            if b < 0x80 || g >= s.gl_n {
                continue;
            }
            let x0 = 6 * k as i32;
            let px = (0..MOD_GLYPH_COLS as i32).flat_map(move |c| {
                let m = unsafe { *s.gl.add(MOD_GLYPH_COLS * g as usize + c as usize) } & 0x3FF;
                (0..10).filter(move |&r| m >> r & 1 != 0).map(move |r| Pixel(Point::new(x0 + c, y - 8 + r), ink))
            });
            lcd.draw_iter(px).ok();
        }
        let (mut num, mut den) = s.bar[i];
        let x0 = 1 + 6 * n as i32 + if n > 0 { 3 } else { 0 };
        if den > 0 && x0 < 118 {
            while den > 0xFFFF {
                den >>= 1;
                num >>= 1;
            }
            let w = 126 - x0;
            let fill = (w as u32 * num / den.max(1)) as i32;
            let c = if i == 0 { BinaryColor::Off } else { BinaryColor::On };
            // the box's outline, 1 pixel inside it
            for (x, y, w, h) in [(x0, y - 7, w + 1, 1), (x0, y, w + 1, 1), (x0, y - 7, 1, 8), (x0 + w, y - 7, 1, 8)] {
                lcd.fill_solid(&Rectangle::new(Point::new(x, y), Size::new(w as u32, h)), c).ok();
            }
            if fill > 0 {
                lcd.fill_solid(&Rectangle::new(Point::new(x0, y - 7), Size::new(fill as u32, 8)), c).ok();
            }
        }
    }
    if wf {
        let (rows, head) = (s.wf_rows, s.wf_head);
        let px = (0..rows).flat_map(|r| {
            let row = unsafe { s.wf.add((((head + r) % rows) * 16) as usize) };
            (0..128).filter_map(move |x| {
                let b = unsafe { *row.add((x >> 3) as usize) };
                if b & (0x80 >> (x & 7)) != 0 {
                    Some(Pixel(Point::new(x, WF_TOP + r), BinaryColor::On))
                } else {
                    None
                }
            })
        });
        lcd.draw_iter(px).ok();
    }
}

/// draw_mod_screen's picture, one controller page (pixel rows 8 * page .. 8 * page + 7, bit 0
/// the top one) at a time, for device::display's draw_pages: the images whose page buffer is
/// in the PWM ring, which a running module may hold. Pixel for pixel the same screen (the same
/// font, baselines, bars and waterfall), drawn straight into the page's 128 bytes: a pass
/// touches only the lines that reach its rows, and no embedded-graphics code is built for it.
#[cfg(all(not(feature = "lean"), shared_lcd))]
pub fn draw_mod_page(buf: &mut [u8; 128], page: u8, s: &app::pager::ModScreen) {
    use app::pager::{MOD_COLS, MOD_GLYPH_COLS, MOD_LINES, WF_TOP};
    let y0 = 8 * page as i32;
    // rows top .. top + h of columns x .. x + w, lit or dark, clipped to the page
    let rect = |buf: &mut [u8; 128], x: i32, top: i32, w: i32, h: i32, on: bool| {
        let (a, b) = ((top - y0).max(0), (top + h - y0).min(8));
        let (x0, x1) = (x.max(0), (x + w).min(128));
        if a >= b || x0 >= x1 {
            return;
        }
        let m = ((0xFFu16 << a) & (0xFFu16 >> (8 - b))) as u8;
        for c in &mut buf[x0 as usize..x1 as usize] {
            *c = if on { *c | m } else { *c & !m };
        }
    };
    rect(buf, 0, 0, 128, 11, true); // the title bar
    let wf = s.wf_rows > 0 && !s.wf.is_null();
    for i in 0..MOD_LINES {
        let y = 9 + 10 * i as i32; // baseline: glyph rows y - 6 .. y, a bar's y - 7 .. y
        if (wf && (2..5).contains(&i)) || y + 2 <= y0 || y - 8 >= y0 + 8 {
            continue; // (a module glyph's rows: y - 8 .. y + 1)
        }
        let on = i != 0;
        let t = &s.text[i];
        let mut n = 0;
        while n < MOD_COLS && t[n] != 0 {
            n += 1; // mod_lcd_text keeps only 0x20-0x7E
        }
        if i == 0 {
            super::glyph::title_page(buf, y0, 1, y - 7, &t[..n], false); // FONT_6X10 (Jeff 2026-11-14)
        } else {
            super::glyph::text_page(buf, y0, 1, y - 6, &t[..n], on);
        }
        // the module's glyphs (level 9): 0x80 + k, 6 columns from the cell's x - 1, rows y - 8 ..
        let sh = y - 8 - y0; // the glyph's bit 0 lands on page bit sh (-9 .. 7 here)
        for (k, &b) in t[..n].iter().enumerate() {
            let g = b.wrapping_sub(0x80) as usize;
            if b < 0x80 || g as i32 >= s.gl_n {
                continue;
            }
            for c in 0..MOD_GLYPH_COLS {
                let m = (unsafe { *s.gl.add(MOD_GLYPH_COLS * g + c) } & 0x3FF) as u32;
                let m = if sh >= 0 { m << sh } else { m >> -sh } as u8;
                let p = &mut buf[6 * k + c];
                *p = if on { *p | m } else { *p & !m };
            }
        }
        let (mut num, mut den) = s.bar[i];
        let x0 = 1 + 6 * n as i32 + if n > 0 { 3 } else { 0 };
        if den > 0 && x0 < 118 {
            while den > 0xFFFF {
                den >>= 1;
                num >>= 1;
            }
            let w = 126 - x0;
            let fill = (w as u32 * num / den.max(1)) as i32;
            // its outline (a 1-pixel stroke on the rectangle's edge), then the filled part
            rect(buf, x0, y - 7, w + 1, 1, on);
            rect(buf, x0, y, w + 1, 1, on);
            rect(buf, x0, y - 7, 1, 8, on);
            rect(buf, x0 + w, y - 7, 1, 8, on);
            rect(buf, x0, y - 7, fill, 8, on);
        }
    }
    if wf {
        let (rows, head) = (s.wf_rows, s.wf_head);
        for r in (y0 - WF_TOP).max(0)..(y0 + 8 - WF_TOP).min(rows) {
            let row = unsafe { s.wf.add((((head + r) % rows) * 16) as usize) };
            let bit = 1u8 << (WF_TOP + r - y0);
            for x in 0..128usize {
                if unsafe { *row.add(x >> 3) } & (0x80 >> (x & 7)) != 0 {
                    buf[x] |= bit;
                }
            }
        }
    }
}
