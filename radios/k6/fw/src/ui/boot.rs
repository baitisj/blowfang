#[cfg(not(feature = "pager-only"))]
use super::TextBuf;
#[cfg(not(feature = "pager-only"))]
use crate::flash_map::Settings;
#[cfg(not(feature = "pager-only"))]
use core::fmt::Write as _;
use embedded_graphics::draw_target::DrawTarget;
use embedded_graphics::mono_font::{ascii::FONT_6X10, MonoFont};
#[cfg(not(feature = "pager-only"))]
use embedded_graphics::mono_font::MonoTextStyle;
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::Rectangle;
#[cfg(not(feature = "pager-only"))]
use embedded_graphics::text::Text;
#[cfg(not(feature = "pager-only"))]
use embedded_graphics::Pixel;

/// The headline font. The pager-only images draw only the FORMAT FLASH? and RELEASE ALL KEYS
/// screens (no boot screen, main.rs), in 6x10's cells: ProFont 14 cost them 4,080 bytes of
/// glyphs for those two screens (2026-11-10). They draw the stock 5x7 font from the SPI flash
/// there (put, ui::glyph)
#[cfg(not(feature = "pager-only"))]
const BIG: &MonoFont = &profont::PROFONT_14_POINT;
#[cfg(feature = "pager-only")]
const BIG: &MonoFont = &FONT_6X10;

const FIRMWARE_VERSION: &str = env!("GIT_VERSION");

#[cfg(not(feature = "pager-only"))]
fn ascii_line(raw: &[u8; 16]) -> &str {
    let end = raw
        .iter()
        .position(|&b| b == 0x00 || b == 0xFF)
        .unwrap_or(raw.len());
    core::str::from_utf8(&raw[..end]).unwrap_or("")
}

fn clear<D>(lcd: &mut D)
where
    D: DrawTarget<Color = BinaryColor>,
{
    lcd.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 64)), BinaryColor::Off).ok();
}

#[cfg(not(feature = "pager-only"))]
/// Falls back to a generic greeting when the user hasn't set custom text.
fn text_lines(settings: &Settings) -> (&str, &str) {
    let l1 = ascii_line(&settings.boot_text_line1);
    let l2 = ascii_line(&settings.boot_text_line2);
    if l1.is_empty() && l2.is_empty() {
        ("UV-K6x", "Aura")
    } else {
        (l1, l2)
    }
}

/// Boot display mode 3: stored logo bitmap.
#[cfg(not(feature = "pager-only"))]
pub fn draw_logo<D>(lcd: &mut D, page_buf: &[u8; crate::flash_map::BOOT_LOGO_SIZE])
where
    D: DrawTarget<Color = BinaryColor>,
{
    clear(lcd);
    for page in 0..8usize {
        let row = &page_buf[page * 128..page * 128 + 128];
        let pixels = row.iter().enumerate().flat_map(move |(col, &byte)| {
            (0..8usize)
                .filter(move |bit| byte & (1 << bit) != 0)
                .map(move |bit| {
                    Pixel(
                        Point::new(col as i32, (page * 8 + bit) as i32),
                        BinaryColor::On,
                    )
                })
        });
        lcd.draw_iter(pixels).ok();
    }
}

/// `text` from x with its baseline at y in `font`; the pager-only images use the stock 5x7
/// font in its place (ui::glyph), with the caps on the same rows as 6x10's
#[cfg_attr(feature = "pager-only", allow(unused_variables))]
fn put<D>(lcd: &mut D, font: &MonoFont, x: i32, y: i32, text: &str, c: BinaryColor)
where
    D: DrawTarget<Color = BinaryColor>,
{
    #[cfg(feature = "pager-only")]
    super::glyph::text(lcd, x, y - 6, text, false, c.is_on());
    #[cfg(not(feature = "pager-only"))]
    Text::new(text, Point::new(x, y), MonoTextStyle::new(font, c)).draw(lcd).ok();
}

fn draw_centered_big<D>(lcd: &mut D, text: &str, baseline_y: i32)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let width = text.len() as i32 * BIG.character_size.width as i32;
    let x = (128 - width) / 2;
    put(lcd, BIG, x, baseline_y, text, BinaryColor::On);
}

fn draw_centered_small<D>(lcd: &mut D, text: &str, baseline_y: i32)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let width = text.len() as i32 * FONT_6X10.character_size.width as i32;
    let x = (128 - width) / 2;
    put(lcd, &FONT_6X10, x, baseline_y, text, BinaryColor::On);
}

fn draw_firmware_banner<D>(lcd: &mut D, text: &str, top: i32)
where
    D: DrawTarget<Color = BinaryColor>,
{
    const PAD: i32 = 6;
    let text_width = text.len() as i32 * FONT_6X10.character_size.width as i32;
    let width = text_width + PAD * 2;
    let x = (128 - width) / 2;
    let height = FONT_6X10.character_size.height;
    lcd.fill_solid(&Rectangle::new(Point::new(x, top), Size::new(width as u32, height)), BinaryColor::On).ok();
    put(lcd, &FONT_6X10, x + PAD, top + FONT_6X10.baseline as i32, text, BinaryColor::Off);
}

fn draw_footer<D>(lcd: &mut D)
where
    D: DrawTarget<Color = BinaryColor>,
{
    // a 1-pixel line across the screen
    lcd.fill_solid(&Rectangle::new(Point::new(0, 40), Size::new(128, 1)), BinaryColor::On).ok();

    draw_firmware_banner(lcd, "UV-K6x Aura", 40);

    draw_centered_small(lcd, FIRMWARE_VERSION, 61);
}

fn draw_headlines<D>(lcd: &mut D, l1: &str, l2: &str)
where
    D: DrawTarget<Color = BinaryColor>,
{
    clear(lcd);
    draw_centered_big(lcd, l1, 20);
    draw_centered_big(lcd, l2, 35);
    draw_footer(lcd);
}

/// Boot display mode 2: two lines of custom text.
#[cfg(not(feature = "pager-only"))]
pub fn draw_message<D>(lcd: &mut D, settings: &Settings)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let (l1, l2) = text_lines(settings);
    draw_headlines(lcd, l1, l2);
}

pub fn draw_release_keys<D>(lcd: &mut D)
where
    D: DrawTarget<Color = BinaryColor>,
{
    draw_headlines(lcd, "RELEASE", "ALL KEYS");
}

pub fn draw_format_prompt<D>(lcd: &mut D)
where
    D: DrawTarget<Color = BinaryColor>,
{
    clear(lcd);
    draw_centered_big(lcd, "FORMAT", 20);
    draw_centered_big(lcd, "FLASH?", 35);
    // a 1-pixel line across the screen
    lcd.fill_solid(&Rectangle::new(Point::new(0, 40), Size::new(128, 1)), BinaryColor::On).ok();
    draw_firmware_banner(lcd, "MENU=ERASE", 40);
    draw_centered_small(lcd, "BACKUP FIRST!!!", 61);
}

/// Boot display mode 1: "VOLTAGE" / actual battery voltage, f4hwn-style.
#[cfg(not(feature = "pager-only"))]
pub fn draw_voltage<D>(lcd: &mut D, voltage_cv: u16)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let mut line: TextBuf<16> = TextBuf::new();
    write!(line, "{}.{:02}V", voltage_cv / 100, voltage_cv % 100).ok();
    draw_headlines(lcd, "VOLTAGE", line.as_str());
}
