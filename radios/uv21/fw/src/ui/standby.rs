use super::{font55, icons, TextBuf, SCREEN_H, SCREEN_W};
use crate::app::{self, ChannelDisplayMode};
use crate::device::radio::{Modulation, SubAudio};
use core::fmt::Write as _;
use embedded_graphics::mono_font::{
    ascii::{FONT_5X8, FONT_6X10, FONT_9X18},
    MonoFont, MonoTextStyle,
};
use embedded_graphics::pixelcolor::Rgb565;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{PrimitiveStyle, Rectangle};
use embedded_graphics::text::Text;

const BG: Rgb565 = Rgb565::BLACK;
const FG: Rgb565 = Rgb565::WHITE;
const ACCENT: Rgb565 = Rgb565::new(0, 55, 28);
const STATUS_BG: Rgb565 = Rgb565::new(2, 6, 12);
const AMBER: Rgb565 = Rgb565::new(31, 50, 0);
const ALERT: Rgb565 = Rgb565::new(31, 8, 4);
const GREEN: Rgb565 = Rgb565::new(0, 55, 6);
const DIM: Rgb565 = Rgb565::new(13, 26, 13);
const HAIR: Rgb565 = Rgb565::new(6, 12, 6);
const SLAVE: Rgb565 = Rgb565::new(20, 40, 20);

const MARGIN: i32 = 6;
const RIGHT_MARGIN: i32 = 6;
const STRIPE_W: i32 = 3;

const STATUS_HEIGHT: i32 = 12;
const ROW1_H: i32 = 12;
const ROW2_H: i32 = 22;
const ROW3_H: i32 = 12;
const BAND_HEIGHT: i32 = ROW1_H + ROW2_H + ROW3_H;
const BAND_TOP0: i32 = STATUS_HEIGHT + 1;
// UV-21 lean receiver (port 6): one band, the one the radio listens on; no second VFO
const METER_RULE_Y: i32 = BAND_TOP0 + BAND_HEIGHT;
const METER_TOP: i32 = METER_RULE_Y + 2;
const METER_H: i32 = 14;
/// UV-21: the portrait panel leaves room under the meter for lines of 5x5 subpixel text
const INFO_RULE_Y: i32 = METER_TOP + METER_H + 1;
const INFO_TOP: i32 = INFO_RULE_Y + 3;
const INFO_LINES: i32 = (SCREEN_H - INFO_TOP) / font55::LINE_H;
const CYAN: Rgb565 = Rgb565::new(14, 54, 31);

const TINY_W: i32 = FONT_5X8.character_size.width as i32;
const SMALL_W: i32 = FONT_6X10.character_size.width as i32;
const BIG_W: i32 = FONT_9X18.character_size.width as i32;

const CONTENT_X: i32 = STRIPE_W;
const LABEL_X: i32 = MARGIN + 22;

#[derive(Clone, Copy, PartialEq)]
pub(super) struct Snapshot {
    power_save: bool,
    key_locked: bool,
    master_index: usize,
    channel_input_len: usize,
    freq_input_len: usize,
    tx_prohibited: bool,
    no_channels_notice: bool,
    sides: [SideSnapshot; 2],
}

pub(super) struct Cache {
    structural: Option<Snapshot>,
    battery: Option<(u8, u16)>,
    rx_status: Option<RxStatusState>,
    meter: Option<MeterState>,
    info_rssi: Option<i32>,
}

impl Cache {
    pub fn new() -> Self {
        Cache {
            structural: None,
            battery: None,
            rx_status: None,
            meter: None,
            info_rssi: None,
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
enum RxStatusText {
    None,
    Rx,
    LastSignal,
}

#[derive(Clone, Copy, PartialEq)]
struct RxStatusState {
    text: RxStatusText,
}

#[derive(Clone, Copy, PartialEq)]
struct MeterState {
    level: u8,
    live: bool,
    dbm: Option<i32>,
    s_number: u8,
    over_s9: i32,
}

#[derive(Clone, Copy, PartialEq)]
struct SideSnapshot {
    freq_hz: u32,
    is_channel_mode: bool,
    channel_num: u16,
    modulation: Modulation,
    subaudio_rx: SubAudio,
    wide_band: bool,
}

impl Snapshot {
    fn overlay_active(&self) -> bool {
        self.tx_prohibited || self.no_channels_notice
    }

    fn flags(&self) -> (bool, bool) {
        (self.power_save, self.key_locked)
    }

    fn capture(app: &app::App) -> Self {
        let master_index = app.master_index();
        let side = |i: usize| -> SideSnapshot {
            SideSnapshot {
                freq_hz: app.side_freq_hz(i),
                is_channel_mode: app.side_is_channel_mode(i),
                channel_num: app.side_channel_num(i),
                modulation: app.side_modulation(i),
                subaudio_rx: app.side_subaudio_rx(i),
                wide_band: app.side_wide_band(i),
            }
        };
        Snapshot {
            power_save: app.power_save_active(),
            key_locked: app.is_key_locked(),
            master_index,
            channel_input_len: app.channel_input_len(),
            freq_input_len: app.freq_input_len(),
            tx_prohibited: app.tx_prohibited(),
            no_channels_notice: app.no_channels_notice(),
            sides: [side(0), side(1)],
        }
    }
}

fn fill<D>(lcd: &mut D, x: i32, y: i32, w: i32, h: i32, color: Rgb565)
where
    D: DrawTarget<Color = Rgb565>,
{
    if w <= 0 || h <= 0 {
        return;
    }
    Rectangle::new(Point::new(x, y), Size::new(w as u32, h as u32))
        .into_styled(PrimitiveStyle::with_fill(color))
        .draw(lcd)
        .ok();
}

fn text<D>(
    lcd: &mut D,
    s: &str,
    font: &MonoFont,
    x: i32,
    baseline: i32,
    color: Rgb565,
) where
    D: DrawTarget<Color = Rgb565>,
{
    Text::new(s, Point::new(x, baseline), MonoTextStyle::new(font, color))
        .draw(lcd)
        .ok();
}

fn text_right<D>(
    lcd: &mut D,
    s: &str,
    font: &MonoFont,
    baseline: i32,
    color: Rgb565,
) where
    D: DrawTarget<Color = Rgb565>,
{
    let w = s.len() as i32 * font.character_size.width as i32;
    text(lcd, s, font, SCREEN_W - RIGHT_MARGIN - w, baseline, color);
}


fn full_redraw<D>(lcd: &mut D, app: &app::App, snap: &Snapshot)
where
    D: DrawTarget<Color = Rgb565>,
{
    fill(lcd, 0, 0, SCREEN_W, SCREEN_H, BG);
    fill(lcd, 0, 0, SCREEN_W, STATUS_HEIGHT, STATUS_BG);
    fill(lcd, 0, STATUS_HEIGHT, SCREEN_W, 1, ACCENT);
    fill(lcd, MARGIN, METER_RULE_Y, SCREEN_W - 2 * MARGIN, 1, HAIR);

    draw_info(lcd);
    draw_flags(lcd, app);
    let i = snap.master_index;
    draw_stripe(lcd);
    draw_row1(lcd, app, i, BAND_TOP0);
    draw_row2(lcd, app, i, BAND_TOP0);
    draw_row3(lcd, app, i, BAND_TOP0);

    if app.tx_prohibited() {
        draw_overlay(lcd, "TX PROHIBITED", ALERT);
    } else if app.no_channels_notice() {
        draw_overlay(lcd, "NO CHANNELS", AMBER);
    }
}

pub fn draw_standby<D>(lcd: &mut D, app: &app::App, cache: &mut Cache)
where
    D: DrawTarget<Color = Rgb565>,
{
    let snap = Snapshot::capture(app);

    let overlay_active = snap.overlay_active();
    let prev_overlay_active = cache
        .structural
        .as_ref()
        .map(|p| p.overlay_active())
        .unwrap_or(false);
    let overlay_content_changed = cache.structural.as_ref().map_or(true, |p| {
        p.tx_prohibited != snap.tx_prohibited
            || p.no_channels_notice != snap.no_channels_notice
    });
    let first_frame = cache.structural.is_none();

    let did_full_redraw = if overlay_active {
        first_frame || !prev_overlay_active || overlay_content_changed
    } else {
        first_frame || prev_overlay_active
    };

    if did_full_redraw {
        full_redraw(lcd, app, &snap);
    } else if !overlay_active {
        let prev = cache.structural.as_ref().unwrap(); // Some, since !first_frame here

        let master_changed = prev.master_index != snap.master_index;

        if prev.flags() != snap.flags() {
            draw_flags(lcd, app);
        }

        let i = snap.master_index;
        let top = BAND_TOP0;
        let p = &prev.sides[i];
        let s = &snap.sides[i];

        if master_changed
            || prev.channel_input_len != snap.channel_input_len
            || (p.is_channel_mode, p.channel_num)
                != (s.is_channel_mode, s.channel_num)
        {
            draw_row1(lcd, app, i, top);
        }
        if master_changed
            || prev.freq_input_len != snap.freq_input_len
            || p.freq_hz != s.freq_hz
            || (p.is_channel_mode, p.channel_num)
                != (s.is_channel_mode, s.channel_num)
        {
            draw_row2(lcd, app, i, top);
        }
        if master_changed
            || (p.modulation, p.subaudio_rx, p.wide_band)
                != (s.modulation, s.subaudio_rx, s.wide_band)
        {
            draw_row3(lcd, app, i, top);
        }
    }
    // else: overlay_active and unchanged from last tick -- already fully
    // painted, nothing structural to touch.

    cache.structural = Some(snap);

    draw_battery(lcd, app, &mut cache.battery, did_full_redraw);
    draw_rx_status(
        lcd,
        app,
        app.master_index(),
        BAND_TOP0,
        &mut cache.rx_status,
        did_full_redraw,
    );
    draw_meter(lcd, app, &mut cache.meter, did_full_redraw);
    draw_info_rssi(lcd, app, &mut cache.info_rssi, did_full_redraw);
}

fn info_line<D>(lcd: &mut D, line: i32, s: &str, color: Rgb565)
where
    D: DrawTarget<Color = Rgb565>,
{
    if line < INFO_LINES {
        font55::draw(lcd, MARGIN, INFO_TOP + line * font55::LINE_H, s, color, BG);
    }
}

/// UV-21: the static lines of the info panel under the meter
fn draw_info<D>(lcd: &mut D)
where
    D: DrawTarget<Color = Rgb565>,
{
    if INFO_LINES <= 0 {
        return;
    }
    fill(lcd, MARGIN, INFO_RULE_Y, SCREEN_W - 2 * MARGIN, 1, HAIR);
    info_line(lcd, 0, "RX ONLY   SPI FLASH READ ONLY", GREEN);
    let mut t: TextBuf<32> = TextBuf::new();
    let _ = write!(t, "BUILD {}", app::FIRMWARE_VERSION);
    info_line(lcd, 2, t.as_str(), DIM);
    info_line(lcd, 3, "A/B  BACK TO SURF", AMBER);
}

/// UV-21: the live signal level, whether or not the squelch is open
fn draw_info_rssi<D>(lcd: &mut D, app: &app::App, last: &mut Option<i32>, force: bool)
where
    D: DrawTarget<Color = Rgb565>,
{
    let dbm = app.rssi_dbm();
    if INFO_LINES <= 1 || (!force && *last == Some(dbm)) {
        return;
    }
    *last = Some(dbm);
    let mut t: TextBuf<32> = TextBuf::new();
    let _ = write!(t, "RSSI {:4} DBM {}", dbm, if app.audio_open() { "OPEN  " } else { "      " });
    info_line(lcd, 1, t.as_str(), CYAN);
}

fn draw_overlay<D>(lcd: &mut D, label: &str, color: Rgb565)
where
    D: DrawTarget<Color = Rgb565>,
{
    let width = label.len() as i32 * SMALL_W + 16;
    let x = (SCREEN_W - width) / 2;
    let y = SCREEN_H / 2 - 11;
    fill(lcd, x, y, width, 22, BG);
    icons::draw_outline(
        lcd,
        Point::new(x, y),
        Size::new(width as u32, 22),
        color,
    );
    text(
        lcd,
        label,
        &FONT_6X10,
        x + 8,
        y + FONT_6X10.baseline as i32 + 6,
        color,
    );
}

// status bar

const FLAGS_W: i32 = 100;

fn draw_flags<D>(lcd: &mut D, app: &app::App)
where
    D: DrawTarget<Color = Rgb565>,
{
    fill(lcd, 0, 0, FLAGS_W, STATUS_HEIGHT, STATUS_BG);
    let baseline = FONT_5X8.baseline as i32 + 2;
    let mut x = 4;
    if app.power_save_active() {
        text(lcd, "PS", &FONT_5X8, x, baseline, DIM);
        x += 3 * TINY_W;
    }
    if app.is_key_locked() {
        icons::draw_padlock(lcd, x, 2, AMBER);
    }
}

fn draw_battery<D>(
    lcd: &mut D,
    app: &app::App,
    last: &mut Option<(u8, u16)>,
    force: bool,
) where
    D: DrawTarget<Color = Rgb565>,
{
    let state = (app.battery_bars(), app.battery_voltage_cv());
    if !force && *last == Some(state) {
        return;
    }
    *last = Some(state);
    let (bars, cv) = state;

    let icon_x = SCREEN_W - RIGHT_MARGIN - icons::BATTERY_W;
    fill(
        lcd,
        FLAGS_W,
        0,
        SCREEN_W - FLAGS_W,
        STATUS_HEIGHT,
        STATUS_BG,
    );

    let mut volts: TextBuf<8> = TextBuf::new();
    write!(volts, "{}.{:02}V", cv / 100, cv % 100).ok();
    let volts = volts.as_str();
    text(
        lcd,
        volts,
        &FONT_5X8,
        icon_x - 4 - volts.len() as i32 * TINY_W,
        FONT_5X8.baseline as i32 + 2,
        DIM,
    );
    let charge = match bars {
        0 | 1 => ALERT,
        2 => AMBER,
        _ => GREEN,
    };
    icons::draw_battery(lcd, icon_x, 1, bars, DIM, charge);
}

// bands

fn draw_stripe<D>(lcd: &mut D)
where
    D: DrawTarget<Color = Rgb565>,
{
    fill(lcd, 0, BAND_TOP0, STRIPE_W, BAND_HEIGHT, ACCENT);
}

fn draw_rx_status<D>(
    lcd: &mut D,
    app: &app::App,
    i: usize,
    top: i32,
    last: &mut Option<RxStatusState>,
    force: bool,
) where
    D: DrawTarget<Color = Rgb565>,
{
    let is_watching = i == app.watching_index();

    let text_kind = if is_watching && (app.is_monitor() || app.rssi_open()) {
        if app.rx_blink_on() {
            RxStatusText::Rx
        } else {
            RxStatusText::None
        }
    } else if app.last_signal_side() == Some(i) {
        RxStatusText::LastSignal
    } else {
        RxStatusText::None
    };

    let state = RxStatusState { text: text_kind };
    if !force && *last == Some(state) {
        return;
    }
    *last = Some(state);

    fill(lcd, CONTENT_X, top, LABEL_X - CONTENT_X, ROW1_H, BG);
    let (label, color) = match text_kind {
        RxStatusText::Rx => ("RX", GREEN),
        RxStatusText::LastSignal => (">>", AMBER),
        RxStatusText::None => return,
    };
    text(
        lcd,
        label,
        &FONT_6X10,
        MARGIN,
        top + FONT_6X10.baseline as i32 + 2,
        color,
    );
}

fn draw_row1<D>(lcd: &mut D, app: &app::App, i: usize, top: i32)
where
    D: DrawTarget<Color = Rgb565>,
{
    fill(lcd, LABEL_X, top, SCREEN_W - LABEL_X, ROW1_H, BG);
    let is_master = i == app.master_index();
    let baseline = top + FONT_6X10.baseline as i32 + 2;

    if is_master && app.channel_input_len() > 0 {
        draw_channel_number_input(lcd, app, baseline);
        return;
    }

    let mut label: TextBuf<8> = TextBuf::new();
    if app.side_is_channel_mode(i) {
        write!(label, "M{:03}", app.side_channel_num(i)).ok();
    } else {
        write!(label, "VFO").ok();
    }
    text(
        lcd,
        label.as_str(),
        &FONT_6X10,
        LABEL_X,
        baseline,
        if is_master { ACCENT } else { DIM },
    );

    if app.channel_display_mode() == ChannelDisplayMode::Frequency {
        let name = app.side_name_str(i);
        if app.side_is_channel_mode(i) && !name.is_empty() {
            let name = &name[..name.len().min(12)];
            text_right(
                lcd,
                name,
                &FONT_5X8,
                top + FONT_5X8.baseline as i32 + 3,
                DIM,
            );
        }
    }
}

fn draw_row2<D>(lcd: &mut D, app: &app::App, i: usize, top: i32)
where
    D: DrawTarget<Color = Rgb565>,
{
    let y = top + ROW1_H;
    fill(lcd, CONTENT_X, y, SCREEN_W - CONTENT_X, ROW2_H, BG);
    let is_master = i == app.master_index();
    let baseline = y + FONT_9X18.baseline as i32 + 3;
    let channel_mode = app.side_is_channel_mode(i);
    let name = app.side_name_str(i);
    let has_name = channel_mode && !name.is_empty();
    let fg = if is_master { FG } else { SLAVE };

    let freq_hz = app.side_freq_hz(i);

    if is_master && app.freq_input_len() > 0 {
        draw_freq_input(lcd, app, baseline);
    } else {
        match app.channel_display_mode() {
            ChannelDisplayMode::NameFreq if has_name => {
                let shown = &name[..name.len().min(16)];
                let name_y = y + FONT_6X10.baseline as i32 + 1;
                text_right(lcd, shown, &FONT_6X10, name_y, fg);
                let mut freq_line: TextBuf<12> = TextBuf::new();
                write!(
                    freq_line,
                    "{:3}.{:05}",
                    freq_hz / 1_000_000,
                    (freq_hz % 1_000_000) / 10
                )
                .ok();
                text_right(
                    lcd,
                    freq_line.as_str(),
                    &FONT_6X10,
                    name_y + FONT_6X10.character_size.height as i32,
                    fg,
                );
            }
            ChannelDisplayMode::Name if has_name => {
                let shown = &name[..name.len().min(16)];
                text_right(lcd, shown, &FONT_9X18, baseline, fg);
            }
            _ => draw_frequency(lcd, freq_hz, baseline, fg),
        }
    }
}

fn draw_row3<D>(lcd: &mut D, app: &app::App, i: usize, top: i32)
where
    D: DrawTarget<Color = Rgb565>,
{
    let y = top + ROW1_H + ROW2_H;
    fill(lcd, CONTENT_X, y, SCREEN_W - CONTENT_X, ROW3_H, BG);
    draw_mode_line(lcd, app, i, y + FONT_5X8.baseline as i32 + 2);
}

pub(crate) fn draw_frequency<D>(
    lcd: &mut D,
    freq_hz: u32,
    baseline_y: i32,
    color: Rgb565,
) where
    D: DrawTarget<Color = Rgb565>,
{
    let mhz = freq_hz / 1_000_000;
    let frac = (freq_hz % 1_000_000) / 10; // 5 fractional digits (kHz + 2 extra)

    let mut head: TextBuf<8> = TextBuf::new();
    write!(head, "{:3}.{:03}", mhz, frac / 100).ok();
    let mut tail: TextBuf<4> = TextBuf::new();
    write!(tail, "{:02}", frac % 100).ok();

    let head_w = head.as_str().len() as i32 * BIG_W;
    let tail_w = tail.as_str().len() as i32 * SMALL_W;
    let x = SCREEN_W - RIGHT_MARGIN - tail_w - head_w;

    text(lcd, head.as_str(), &FONT_9X18, x, baseline_y, color);
    text(
        lcd,
        tail.as_str(),
        &FONT_6X10,
        x + head_w,
        baseline_y - FONT_9X18.baseline as i32 + FONT_6X10.baseline as i32,
        color,
    );
}

/// Mid-entry VFO frequency: `"xxx.x--"`
fn draw_freq_input<D>(lcd: &mut D, app: &app::App, baseline_y: i32)
where
    D: DrawTarget<Color = Rgb565>,
{
    let len = app.freq_input_len();
    let mut buf: TextBuf<8> = TextBuf::new();
    for pos in 0..6usize {
        if pos == 3 {
            write!(buf, ".").ok();
        }
        if pos < len {
            write!(buf, "{}", app.freq_input_digit(pos) as u32).ok();
        } else {
            write!(buf, "-").ok();
        }
    }
    let w = buf.as_str().len() as i32 * BIG_W;
    text(
        lcd,
        buf.as_str(),
        &FONT_9X18,
        SCREEN_W - RIGHT_MARGIN - w,
        baseline_y,
        AMBER,
    );
}

fn draw_channel_number_input<D>(lcd: &mut D, app: &app::App, baseline_y: i32)
where
    D: DrawTarget<Color = Rgb565>,
{
    let len = app.channel_input_len();
    let mut buf: TextBuf<8> = TextBuf::new();
    write!(buf, "M").ok();
    for pos in 0..3usize {
        if pos + len < 3 {
            write!(buf, "-").ok();
        } else {
            write!(buf, "{}", app.freq_input_digit(pos + len - 3) as u32).ok();
        }
    }
    text(lcd, buf.as_str(), &FONT_6X10, LABEL_X, baseline_y, AMBER);
}

/// The squelch tone the receiver waits for, if any (the 5RH's TX tone modes are gone: the UV-21
/// image never transmits)
fn rx_tone_label(rx: SubAudio) -> &'static str {
    match rx {
        SubAudio::None => "",
        SubAudio::Ctcss(_) => "TSQL",
        SubAudio::Dcs { .. } => "DCS",
    }
}

fn draw_mode_line<D>(lcd: &mut D, app: &app::App, i: usize, baseline_y: i32)
where
    D: DrawTarget<Color = Rgb565>,
{
    let modulation = match app.side_modulation(i) {
        Modulation::Fm => "FM",
        Modulation::Am => "AM",
        Modulation::Usb => "USB",
        // Unreachable app-side (CW out of scope for this v1); kept only
        // because `Modulation` is shared with `drivers::fd6818b`.
        Modulation::Cw => "CW",
        Modulation::Cwf => "CWF",
    };
    let fields = [
        (modulation, DIM),
        (rx_tone_label(app.side_subaudio_rx(i)), ACCENT),
        (if app.side_wide_band(i) { "WIDE" } else { "NAR" }, DIM),
    ];

    let mut x = MARGIN;
    for (field, color) in fields {
        if field.is_empty() {
            continue;
        }
        text(lcd, field, &FONT_5X8, x, baseline_y, color);
        x += (field.len() as i32 + 1) * TINY_W;
    }
}

// bottom meter

const SEG_PITCH: i32 = 5;
const SEG_W: i32 = 4;
const SEG_MAX_H: i32 = 10;
const S_METER_SEGS: u8 = 13;
const MIC_BAR_SEGS: u8 = 18;
const S_METER_BAR_X: i32 =
    SCREEN_W - RIGHT_MARGIN - S_METER_SEGS as i32 * SEG_PITCH + 1;
const MIC_BAR_X: i32 = MARGIN + 22;

fn draw_seg<D>(lcd: &mut D, x: i32, base_y: i32, i: u8, fixed: Option<Rgb565>)
where
    D: DrawTarget<Color = Rgb565>,
{
    let h = (i as i32 + 1).min(SEG_MAX_H);
    let color = fixed.unwrap_or(if i < 9 {
        GREEN
    } else if i < 12 {
        AMBER
    } else {
        ALERT
    });
    fill(
        lcd,
        x + i as i32 * SEG_PITCH,
        base_y - h + 1,
        SEG_W,
        h,
        color,
    );
}

fn draw_bar<D>(
    lcd: &mut D,
    x: i32,
    base_y: i32,
    level: u8,
    total: u8,
    fixed: Option<Rgb565>,
) where
    D: DrawTarget<Color = Rgb565>,
{
    for i in 0..total {
        if i < level {
            draw_seg(lcd, x, base_y, i, fixed);
        } else {
            let h = (i as i32 + 1).min(SEG_MAX_H);
            fill(
                lcd,
                x + i as i32 * SEG_PITCH,
                base_y - h + 1,
                SEG_W,
                h,
                HAIR,
            );
        }
    }
}

fn meter_state(app: &app::App) -> MeterState {
    if app.audio_open() {
        let dbm = app.rssi_dbm();
        MeterState {
            level: app.s_meter_level(),
            live: true,
            dbm: Some(dbm),
            s_number: app.s_meter_s_number(),
            over_s9: app.s_meter_over_s9_dbm(),
        }
    } else {
        match app.last_signal_peak() {
            Some((_, dbm)) => MeterState {
                    level: app::s_meter_level_of(dbm),
                live: false,
                dbm: Some(dbm),
                s_number: 0,
                over_s9: 0,
            },
            None => MeterState {
                    level: 0,
                live: false,
                dbm: None,
                s_number: 0,
                over_s9: 0,
            },
        }
    }
}

fn draw_meter<D>(
    lcd: &mut D,
    app: &app::App,
    last: &mut Option<MeterState>,
    force: bool,
) where
    D: DrawTarget<Color = Rgb565>,
{
    let state = meter_state(app);
    let prev = *last;
    if !force && prev == Some(state) {
        return;
    }
    *last = Some(state);

    let mode_changed = force || prev.is_none();
    let text_changed = mode_changed
        || prev.map_or(true, |p| {
            (p.dbm, p.s_number, p.over_s9, p.live)
                != (state.dbm, state.s_number, state.over_s9, state.live)
        });
    let level_changed = mode_changed
        || prev
            .map_or(true, |p| (p.level, p.live) != (state.level, state.live));

    if mode_changed {
        fill(lcd, 0, METER_TOP, SCREEN_W, METER_H, BG);
    }
    let base_y = METER_TOP + METER_H - 2;
    let baseline = METER_TOP + FONT_5X8.baseline as i32 + 3;

    if text_changed {
        fill(lcd, 0, METER_TOP, S_METER_BAR_X - 3, METER_H, BG);
        let mut line: TextBuf<20> = TextBuf::new();
        if !state.live {
            line.write_str("LAST ").ok();
        }
        match state.dbm {
            None => {
                line.write_str("--").ok();
            }
            Some(dbm) => {
                write!(line, "{dbm}dBm").ok();
                if state.live {
                    if state.over_s9 > 0 {
                        write!(line, " +{}", state.over_s9).ok();
                    } else {
                        write!(line, " S{}", state.s_number).ok();
                    }
                }
            }
        }
        text(
            lcd,
            line.as_str(),
            &FONT_5X8,
            MARGIN,
            baseline,
            if state.live { FG } else { DIM },
        );
    }
    if level_changed {
        if state.live {
            draw_bar(
                lcd,
                S_METER_BAR_X,
                base_y,
                state.level,
                S_METER_SEGS,
                None,
            );
        } else {
            draw_bar(lcd, S_METER_BAR_X, base_y, 0, S_METER_SEGS, None);
            if state.level > 0 {
                draw_seg(lcd, S_METER_BAR_X, base_y, state.level - 1, None);
            }
        }
    }
}
