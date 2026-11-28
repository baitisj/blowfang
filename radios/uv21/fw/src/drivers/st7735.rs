use crate::board;
use crate::hal::delay;
use at32f421_pac as pac;
use cortex_m::peripheral::SYST;

pub const WIDTH: u16 = 128;
pub const HEIGHT: u16 = 160;

pub struct St7735<'a> {
    gpiob: &'a pac::Gpiob,
    spi2: &'a pac::Spi2,
}

impl<'a> St7735<'a> {
    pub fn new(gpiob: &'a pac::Gpiob, spi2: &'a pac::Spi2) -> Self {
        St7735 { gpiob, spi2 }
    }

    fn spi_write_byte(&self, b: u8) {
        while self.spi2.sts().read().tdbe().bit_is_clear() {}
        self.spi2.dt().write(|w| unsafe { w.dt().bits(b as u16) });
    }

    fn spi_wait_idle(&self) {
        while self.spi2.sts().read().bf().bit_is_set() {}
    }

    fn write_command(&self, cmd: u8) {
        board::set_lcd_dc(self.gpiob, false);
        board::set_lcd_cs(self.gpiob, false);
        self.spi_write_byte(cmd);
        self.spi_wait_idle();
        board::set_lcd_cs(self.gpiob, true);
    }

    fn write_data(&self, bytes: &[u8]) {
        board::set_lcd_dc(self.gpiob, true);
        board::set_lcd_cs(self.gpiob, false);
        for &b in bytes {
            self.spi_write_byte(b);
        }
        self.spi_wait_idle();
        board::set_lcd_cs(self.gpiob, true);
    }

    fn command(&self, cmd: u8, args: &[u8]) {
        self.write_command(cmd);
        if !args.is_empty() {
            self.write_data(args);
        }
    }

    fn set_window(&self, x0: u16, y0: u16, x1: u16, y1: u16) {
        self.command(
            0x2A,
            &[(x0 >> 8) as u8, x0 as u8, (x1 >> 8) as u8, x1 as u8],
        ); // CASET
        self.command(
            0x2B,
            &[(y0 >> 8) as u8, y0 as u8, (y1 >> 8) as u8, y1 as u8],
        ); // RASET
    }

    #[inline]
    pub fn stream_px(&self, raw: u16) {
        self.spi_write_byte((raw >> 8) as u8);
        self.spi_write_byte(raw as u8);
    }

    /// Clips and fills an axis-aligned rectangle in one ST7735S RAMWR window.
    pub fn fill_rect(&self, x: i32, y: i32, w: i32, h: i32, color: u16) {
        let x0 = x.max(0);
        let y0 = y.max(0);
        let x1 = (x + w).min(WIDTH as i32);
        let y1 = (y + h).min(HEIGHT as i32);
        if x1 <= x0 || y1 <= y0 {
            return;
        }
        let rw = (x1 - x0) as u16;
        let rh = (y1 - y0) as u16;
        self.begin(x0 as u16, y0 as u16, rw, rh);
        let hi = (color >> 8) as u8;
        let lo = color as u8;
        for _ in 0..(rw as u32 * rh as u32) {
            self.spi_write_byte(hi);
            self.spi_write_byte(lo);
        }
        self.end();
    }

    /// Opens a w x h window at (x, y) for `stream`: its pixels go left to right, then down,
    /// until `end` (port 18: SURF's history strip, one window for all of it)
    pub fn begin(&self, x: u16, y: u16, w: u16, h: u16) {
        self.set_window(x, y, x + w - 1, y + h - 1);
        self.write_command(0x2C); // RAMWR
        board::set_lcd_dc(self.gpiob, true);
        board::set_lcd_cs(self.gpiob, false);
    }

    pub fn stream(&self, px: &[u16]) {
        for &c in px {
            self.spi_write_byte((c >> 8) as u8);
            self.spi_write_byte(c as u8);
        }
    }

    pub fn end(&self) {
        self.spi_wait_idle();
        board::set_lcd_cs(self.gpiob, true);
    }

    pub fn init(&self, syst: &mut SYST) {
        board::set_lcd_reset(self.gpiob, true);
        delay::ms(syst, 10);
        board::set_lcd_reset(self.gpiob, false);
        delay::ms(syst, 10);
        board::set_lcd_reset(self.gpiob, true);
        delay::ms(syst, 120);

        self.command(0x11, &[]); // SLPOUT
        delay::ms(syst, 120);

        self.command(0xB1, &[0x05, 0x3A, 0x3A]); // FRMCTR1
        self.command(0xB2, &[0x05, 0x3A, 0x3A]); // FRMCTR2
        self.command(0xB3, &[0x05, 0x3A, 0x3A, 0x05, 0x3A, 0x3A]); // FRMCTR3
        self.command(0xB4, &[0x03]); // INVCTR
        self.command(0xC0, &[0x62, 0x02, 0x04]); // PWCTR1
        self.command(0xC1, &[0xC0]); // PWCTR2
        self.command(0xC2, &[0x0D, 0x00]); // PWCTR3
        self.command(0xC3, &[0x8D, 0x6A]); // PWCTR4
        self.command(0xC4, &[0x8D, 0xEE]); // PWCTR5
        self.command(0xC5, &[0x12]); // VMCTR1
        self.command(
            0xE0,
            &[
                0x03, 0x1B, 0x12, 0x11, 0x3F, 0x3A, 0x32, 0x34, 0x2F, 0x2B,
                0x30, 0x3A, 0x00, 0x00, 0x01, 0x05,
            ],
        ); // GMCTRP1
        self.command(
            0xE1,
            &[
                0x03, 0x1B, 0x12, 0x11, 0x32, 0x2F, 0x2A, 0x2F, 0x2E, 0x2C,
                0x35, 0x3F, 0x00, 0x00, 0x01, 0x05,
            ],
        ); // GMCTRN1
        self.command(0xFC, &[0x8C]); // vendor extension, purpose unknown
        self.command(0x36, &[0x00]); // MADCTL: the UV-21 panel upright, portrait
        self.command(0x3A, &[0x05]); // COLMOD = RGB565
        delay::ms(syst, 10);
        self.command(0x29, &[]); // DISPON
    }
}

