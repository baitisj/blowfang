//! UV-21: 5x5 capitals drawn on the panel's subpixels ("5x5 RGB2"). The ST7735S panel's stripes
//! run R, G, B left to right, and each font column takes two stripes, so a character cell (five
//! columns and a one-column gap) is exactly four pixels wide: 32 characters across the 128-pixel
//! portrait screen. A 1-2-3-2-1 filter over the stripes, through a gamma table, keeps the colour
//! fringes down. A line is six pixel rows: five for the glyphs and one gap.
//!
//! There is no frame buffer (16 KB of RAM), so each pixel row of a line goes straight to the
//! panel, blended between the foreground and background colours.
use crate::drivers::st7735::St7735;

pub const CHAR_W: i32 = 4;
pub const LINE_H: i32 = 6;

const MAX_PX: usize = super::SCREEN_W as usize;
/// filter sum (0-9) to brightness
const GAMMA: [u8; 10] = [0, 94, 129, 155, 176, 195, 212, 228, 242, 255];

/// ' ' to '_' (0x20-0x5F); five rows top to bottom, bit 4 = the leftmost column. Glyphs the
/// font lacks are '?'.
const GLYPHS: [[u8; 5]; 64] = [
    [0x00, 0x00, 0x00, 0x00, 0x00], //  
    [0x04, 0x04, 0x04, 0x00, 0x04], // !
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for "
    [0x0a, 0x1f, 0x0a, 0x1f, 0x0a], // #
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for $
    [0x11, 0x02, 0x04, 0x08, 0x11], // %
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for &
    [0x04, 0x04, 0x00, 0x00, 0x00], // '
    [0x02, 0x04, 0x04, 0x04, 0x02], // (
    [0x08, 0x04, 0x04, 0x04, 0x08], // )
    [0x00, 0x15, 0x0e, 0x15, 0x00], // *
    [0x00, 0x04, 0x0e, 0x04, 0x00], // +
    [0x00, 0x00, 0x00, 0x04, 0x08], // ,
    [0x00, 0x00, 0x0e, 0x00, 0x00], // -
    [0x00, 0x00, 0x00, 0x00, 0x04], // .
    [0x01, 0x02, 0x04, 0x08, 0x10], // /
    [0x0e, 0x13, 0x15, 0x19, 0x0e], // 0
    [0x04, 0x0c, 0x04, 0x04, 0x0e], // 1
    [0x1e, 0x01, 0x0e, 0x10, 0x1f], // 2
    [0x1e, 0x01, 0x0e, 0x01, 0x1e], // 3
    [0x12, 0x12, 0x1f, 0x02, 0x02], // 4
    [0x1f, 0x10, 0x1e, 0x01, 0x1e], // 5
    [0x0e, 0x10, 0x1e, 0x11, 0x0e], // 6
    [0x1f, 0x01, 0x02, 0x04, 0x04], // 7
    [0x0e, 0x11, 0x0e, 0x11, 0x0e], // 8
    [0x0e, 0x11, 0x0f, 0x01, 0x0e], // 9
    [0x00, 0x04, 0x00, 0x04, 0x00], // :
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for ;
    [0x02, 0x04, 0x08, 0x04, 0x02], // <
    [0x00, 0x0e, 0x00, 0x0e, 0x00], // =
    [0x08, 0x04, 0x02, 0x04, 0x08], // >
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ?
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for @
    [0x0e, 0x11, 0x1f, 0x11, 0x11], // A
    [0x1e, 0x11, 0x1e, 0x11, 0x1e], // B
    [0x0f, 0x10, 0x10, 0x10, 0x0f], // C
    [0x1e, 0x11, 0x11, 0x11, 0x1e], // D
    [0x1f, 0x10, 0x1e, 0x10, 0x1f], // E
    [0x1f, 0x10, 0x1e, 0x10, 0x10], // F
    [0x0f, 0x10, 0x13, 0x11, 0x0e], // G
    [0x11, 0x11, 0x1f, 0x11, 0x11], // H
    [0x1f, 0x04, 0x04, 0x04, 0x1f], // I
    [0x1f, 0x02, 0x02, 0x12, 0x0c], // J
    [0x11, 0x12, 0x1c, 0x12, 0x11], // K
    [0x10, 0x10, 0x10, 0x10, 0x1f], // L
    [0x11, 0x1b, 0x15, 0x11, 0x11], // M
    [0x11, 0x19, 0x15, 0x13, 0x11], // N
    [0x0e, 0x11, 0x11, 0x11, 0x0e], // O
    [0x1e, 0x11, 0x1e, 0x10, 0x10], // P
    [0x0e, 0x11, 0x15, 0x12, 0x0d], // Q
    [0x1e, 0x11, 0x1e, 0x12, 0x11], // R
    [0x0f, 0x10, 0x0e, 0x01, 0x1e], // S
    [0x1f, 0x04, 0x04, 0x04, 0x04], // T
    [0x11, 0x11, 0x11, 0x11, 0x0e], // U
    [0x11, 0x11, 0x11, 0x0a, 0x04], // V
    [0x11, 0x11, 0x15, 0x1b, 0x11], // W
    [0x11, 0x0a, 0x04, 0x0a, 0x11], // X
    [0x11, 0x0a, 0x04, 0x04, 0x04], // Y
    [0x1f, 0x02, 0x04, 0x08, 0x1f], // Z
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for [
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for backslash
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for ]
    [0x0e, 0x11, 0x06, 0x00, 0x04], // ? for ^
    [0x00, 0x00, 0x00, 0x00, 0x1f], // _
];

fn glyph(c: u8) -> &'static [u8; 5] {
    let c = c.to_ascii_uppercase();
    let i = if (0x20..0x60).contains(&c) { c - 0x20 } else { b'?' - 0x20 };
    &GLYPHS[i as usize]
}

fn mix(bg: u8, fg: u8, v: u8) -> u8 {
    (bg as i32 + (fg as i32 - bg as i32) * v as i32 / 255) as u8
}

/// The width in pixels of `s`, including the one pixel the last glyph's filter spills into.
pub fn width(s: &str) -> i32 {
    s.len() as i32 * CHAR_W + 1
}

/// Packs one glyph row `r` (0-4) into 1-bit-per-stripe coverage (49 B vs 388 B),
/// with two leading zero stripes for the 1-2-3-2-1 filter.
fn pack_cov(bytes: &[u8], r: usize, cov: &mut [u8; (MAX_PX * 3 + 4 + 7) / 8]) {
    cov.fill(0);
    if r >= 5 {
        return;
    }
    let lim = MAX_PX * 3 + 4;
    for (k, &c) in bytes.iter().enumerate() {
        let bits = glyph(c)[r];
        for col in 0..5 {
            if bits & (0x10 >> col) != 0 {
                let st = (k * 6 + col) * 2 + 2;
                if st + 1 < lim {
                    cov[st >> 3] |= 1 << (st & 7);
                    cov[(st + 1) >> 3] |= 1 << ((st + 1) & 7);
                }
            }
        }
    }
}

#[inline(always)]
fn bit(cov: &[u8; (MAX_PX * 3 + 4 + 7) / 8], i: usize) -> u8 {
    (cov[i >> 3] >> (i & 7)) & 1
}

/// Draws `s` with its top-left corner at (x, y), all six rows of the line (so it overwrites what
/// was there), clipped to the screen. Returns the width drawn. One window for the whole line,
/// each pixel filtered on the fly from a 49-byte 1-bit coverage row.
pub fn draw(lcd: &mut St7735, x: i32, y: i32, s: &str, fg: u16, bg: u16) -> i32 {
    let w = width(s).min(super::SCREEN_W - x);
    if w <= 0 || s.is_empty() {
        return 0;
    }
    let x0 = x.max(0);
    let y0 = y.max(0);
    let x1 = (x + w).min(super::SCREEN_W);
    let y1 = (y + LINE_H).min(super::SCREEN_H);
    if x1 <= x0 || y1 <= y0 {
        return w;
    }
    let (fr, fg_c, fb) = (((fg >> 11) & 0x1F) as u8, ((fg >> 5) & 0x3F) as u8, (fg & 0x1F) as u8);
    let (br, bg_c, bb) = (((bg >> 11) & 0x1F) as u8, ((bg >> 5) & 0x3F) as u8, (bg & 0x1F) as u8);
    let bytes = s.as_bytes();
    let mut cov = [0u8; (MAX_PX * 3 + 4 + 7) / 8];
    lcd.begin(x0 as u16, y0 as u16, (x1 - x0) as u16, (y1 - y0) as u16);
    for py in y0..y1 {
        let r = (py - y) as usize;
        if r >= 5 {
            for _ in x0..x1 {
                lcd.stream_px(bg);
            }
            continue;
        }
        pack_cov(bytes, r, &mut cov);
        for px_x in x0..x1 {
            let base = 3 * (px_x - x) as usize + 2;
            let mut f = [0u8; 3];
            for (k, fk) in f.iter_mut().enumerate() {
                let i = base + k;
                *fk = bit(&cov, i - 2)
                    + 2 * bit(&cov, i - 1)
                    + 3 * bit(&cov, i)
                    + 2 * bit(&cov, i + 1)
                    + bit(&cov, i + 2);
            }
            let r5 = mix(br, fr, GAMMA[f[0] as usize]) as u16;
            let g6 = mix(bg_c, fg_c, GAMMA[f[1] as usize]) as u16;
            let b5 = mix(bb, fb, GAMMA[f[2] as usize]) as u16;
            lcd.stream_px((r5 << 11) | (g6 << 5) | b5);
        }
    }
    lcd.end();
    w
}
