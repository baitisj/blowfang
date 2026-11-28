pub mod boot;
#[cfg(not(feature = "pager-only"))]
mod chanmgr;
#[cfg(not(feature = "pager-only"))]
mod contacts;
pub mod cps;
#[cfg(not(feature = "pager-only"))]
mod fm;
pub mod glyph;
pub mod icons;
#[cfg(not(feature = "pager-only"))]
mod launcher;
#[cfg(not(feature = "pager-only"))]
mod list;
mod pager;
#[cfg(not(feature = "pager-only"))]
mod scan;
#[cfg(not(feature = "pager-only"))]
mod scanqt;
#[cfg(not(feature = "pager-only"))]
mod search;
#[cfg(not(feature = "pager-only"))]
mod settings;
#[cfg(not(feature = "pager-only"))]
mod satellite;
#[cfg(not(feature = "pager-only"))]
mod spectrum;
#[cfg(not(feature = "pager-only"))]
mod sstv;
#[cfg(not(feature = "pager-only"))]
mod standby;

#[cfg(not(feature = "pager-only"))]
use chanmgr::draw_chanmgr;
#[cfg(not(feature = "pager-only"))]
use contacts::draw_contacts;
#[cfg(not(feature = "pager-only"))]
use fm::draw_fm;
#[cfg(not(feature = "pager-only"))]
use launcher::draw_app_menu;
use pager::draw_pager;
#[cfg(not(feature = "pager-only"))]
use satellite::draw_satellite;
#[cfg(not(feature = "pager-only"))]
use sstv::draw_sstv;
#[cfg(not(feature = "pager-only"))]
use scan::draw_scan;
#[cfg(not(feature = "pager-only"))]
use scanqt::draw_scanqt;
#[cfg(not(feature = "pager-only"))]
use search::draw_search;
#[cfg(not(feature = "pager-only"))]
use settings::draw_settings;
#[cfg(not(feature = "pager-only"))]
use spectrum::draw_spectrum;
#[cfg(not(feature = "pager-only"))]
use standby::draw_standby;

use crate::app;
use crate::device::display::Display;
use display_interface::WriteOnlyDataCommand;
#[cfg(banded)]
use embedded_graphics::{pixelcolor::BinaryColor, prelude::*, primitives::Rectangle};

// TextBuf
pub struct TextBuf<const N: usize> {
    buf: [u8; N],
    len: usize,
}

impl<const N: usize> TextBuf<N> {
    pub fn new() -> Self {
        TextBuf {
            buf: [0; N],
            len: 0,
        }
    }

    pub fn as_str(&self) -> &str {
        crate::out::ascii(&self.buf[..self.len])
    }
}

impl<const N: usize> crate::out::Sink for TextBuf<N> {
    fn bytes(&mut self, bytes: &[u8]) {
        let space = N - self.len;
        let n = bytes.len().min(space);
        self.buf[self.len..self.len + n].copy_from_slice(&bytes[..n]);
        self.len += n;
    }
}

impl<const N: usize> core::fmt::Write for TextBuf<N> {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        crate::out::Sink::bytes(self, s.as_bytes());
        Ok(())
    }
}

/// A RAM module's screen (module API level 6): app::pager's lcd_flush calls the function this
/// registers, which draws app::pager::MOD_SCREEN on `display` and flushes it. Modules run inside
/// the main loop's calls, never while it draws, so nothing else holds the display then. The
/// `lean` images (the codebook install, the -table fallbacks) have no room for it: lcd_flush
/// returns -1 there and the module keeps to its serial lines.
#[cfg(not(feature = "lean"))]
static mut MOD_DISPLAY: *mut () = core::ptr::null_mut();

#[cfg(not(feature = "lean"))]
unsafe fn mod_flush<DI: WriteOnlyDataCommand + 'static>() -> i32 {
    let d = unsafe { &mut *(*core::ptr::addr_of!(MOD_DISPLAY) as *mut Display<'static, DI>) };
    let s = unsafe { &*core::ptr::addr_of!(app::pager::MOD_SCREEN) };
    // the module has the ring (the page buffer's home): a page at a time, 128 bytes of stack
    // module API level 15: a help card while the module's help() shows one
    #[cfg(all(shared_lcd, banded))]
    let help = app::pager::help_on();
    #[cfg(all(shared_lcd, banded))]
    d.draw_pages(|page, buf| if help { app::pager::help_band(page, buf) } else { pager::draw_mod_page(buf, page, s) });
    #[cfg(all(shared_lcd, not(banded)))]
    d.draw_pages(|page, buf| pager::draw_mod_page(buf, page, s));
    #[cfg(not(shared_lcd))]
    {
        pager::draw_mod_screen(d.as_draw_target(), s);
        d.flush();
    }
    0
}

#[cfg(not(feature = "lean"))]
pub fn hook_mod_screen<DI: WriteOnlyDataCommand + 'static>(display: &mut Display<'_, DI>) {
    unsafe { *core::ptr::addr_of_mut!(MOD_DISPLAY) = display as *mut Display<'_, DI> as *mut () };
    app::pager::set_mod_lcd(mod_flush::<DI>);
    #[cfg(all(banded, feature = "pager-only"))]
    app::pager::set_mod_redraw(mod_redraw::<DI>);
}

/// module API level 17, redraw: the core's own screen again, through the screen tap
#[cfg(all(banded, feature = "pager-only", not(feature = "lean")))]
unsafe fn mod_redraw<DI: WriteOnlyDataCommand + 'static>(app: &app::App) {
    let d = unsafe { &mut *(*core::ptr::addr_of!(MOD_DISPLAY) as *mut Display<'static, DI>) };
    draw_pager_only(d, app);
}

#[cfg(feature = "lean")]
pub fn hook_mod_screen<DI: WriteOnlyDataCommand + 'static>(_display: &mut Display<'_, DI>) {}

/// Pager-only image: just the VOICE MSG screen, so the other screens aren't linked.
#[cfg(feature = "pager-only")]
pub fn draw_pager_only<DI: WriteOnlyDataCommand>(display: &mut Display<'_, DI>, app: &app::App) {
    // the page buffer is in the PWM ring (device::display): never while a message streams
    #[cfg(shared_lcd)]
    if app.pager_streaming() {
        return;
    }
    // the 8 kHz voice + EXPLORE image (`banded`): a band of 8 rows at a time, as every screen
    #[cfg(banded)]
    {
        let explore = app.exploring();
        #[cfg(all(feature = "explore", feature = "spilib"))]
        let menu = app::pager::menu_state().0;
        #[cfg(not(all(feature = "explore", feature = "spilib")))]
        let menu = false;
        // a help card: straight from SPI, a page at a time (app::pager help_band)
        let help = app::pager::help_on();
        display.draw_pages(|page, buf| {
            if help {
                app::pager::help_band(page, buf);
                return;
            }
            let band = &mut Band { buf, y0: 8 * page as i32 };
            #[cfg(all(feature = "explore", feature = "spilib"))]
            if menu {
                pager::draw_menu(band);
                return;
            }
            let _ = menu;
            if explore {
                app::pager::explore_draw(band, app);
            } else {
                draw_pager(band, app);
            }
        });
        return;
    }
    #[cfg(all(not(banded), feature = "explore", feature = "spilib"))]
    if app::pager::menu_state().0 {
        pager::draw_menu(display.as_draw_target());
        display.flush();
        return;
    }
    #[cfg(not(banded))]
    if app.exploring() {
        app::pager::explore_draw(display.as_draw_target(), app);
        display.flush();
        return;
    }
    draw_pager(display.as_draw_target(), app);
    display.flush();
}

/// One controller page of the screen (pixel rows y0 .. y0 + 7, bit 0 the top row) as a 128 x 64
/// draw target: what falls outside it is dropped, a glyph or a box that misses it at once. In
/// the `banded` image every screen is drawn once for each of the 8 (device::display
/// draw_pages, `screen!`): EXPLORE keeps its sweep's and Listen for ID's state in the ring, the
/// shared page buffer's home, and one draw target for all the screens builds the drawing code
/// (embedded-graphics' text, lines, boxes) once.
#[cfg(banded)]
pub struct Band<'a> {
    pub buf: &'a mut [u8; 128],
    pub y0: i32,
}

/// `screen!(display, |lcd| draw(lcd, ...))`: draw a whole screen and send it: in a band at a
/// time in the `banded` image (`draw` runs 8 times), else into the page buffer, then flush.
#[macro_export]
macro_rules! screen {
    ($display:expr, |$lcd:ident| $body:expr) => {{
        #[cfg(banded)]
        $display.draw_pages(|page, buf| {
            let $lcd = &mut $crate::ui::Band { buf, y0: 8 * page as i32 };
            $body;
        });
        #[cfg(not(banded))]
        {
            let $lcd = $display.as_draw_target();
            $body;
            $display.flush();
        }
    }};
}

#[cfg(banded)]
impl Band<'_> {
    #[inline(always)]
    fn put(&mut self, x: i32, y: i32, c: BinaryColor) {
        let dy = y - self.y0;
        if (0..8).contains(&dy) && (0..128).contains(&x) {
            let b = &mut self.buf[x as usize];
            *b = if c.is_on() { *b | 1 << dy } else { *b & !(1 << dy) };
        }
    }
    /// the rows of `area` this band holds, or None
    fn rows(&self, area: &Rectangle) -> Option<(i32, i32)> {
        let (a, b) = (area.top_left.y.max(self.y0), (area.top_left.y + area.size.height as i32).min(self.y0 + 8));
        if a < b && area.size.width > 0 {
            Some((a, b))
        } else {
            None
        }
    }
}

/// Its 8 rows of the 128 x 64 screen: ui::glyph skips the text lines outside them, and reads
/// no glyphs for those (embedded-graphics' own drawing never asks)
#[cfg(banded)]
impl Dimensions for Band<'_> {
    fn bounding_box(&self) -> Rectangle {
        Rectangle::new(Point::new(0, self.y0), Size::new(128, 8))
    }
}

#[cfg(banded)]
impl DrawTarget for Band<'_> {
    type Color = BinaryColor;
    type Error = core::convert::Infallible;

    fn draw_iter<I: IntoIterator<Item = Pixel<BinaryColor>>>(&mut self, px: I) -> Result<(), Self::Error> {
        for Pixel(p, c) in px {
            self.put(p.x, p.y, c);
        }
        Ok(())
    }

    fn fill_contiguous<I: IntoIterator<Item = BinaryColor>>(&mut self, area: &Rectangle, colors: I) -> Result<(), Self::Error> {
        let Some((_, b)) = self.rows(area) else { return Ok(()) };
        let (x0, y0, w) = (area.top_left.x, area.top_left.y, area.size.width as i32);
        for (k, c) in colors.into_iter().enumerate().take(((b - y0) * w) as usize) {
            self.put(x0 + k as i32 % w, y0 + k as i32 / w, c);
        }
        Ok(())
    }

    fn fill_solid(&mut self, area: &Rectangle, c: BinaryColor) -> Result<(), Self::Error> {
        let Some((a, b)) = self.rows(area) else { return Ok(()) };
        let (x0, x1) = (area.top_left.x.max(0), (area.top_left.x + area.size.width as i32).min(128));
        let m = ((0xFFu16 << (a - self.y0)) & (0xFFu16 >> (8 - (b - self.y0)))) as u8;
        for x in x0..x1 {
            let p = &mut self.buf[x as usize];
            *p = if c.is_on() { *p | m } else { *p & !m };
        }
        Ok(())
    }
}

#[cfg(not(feature = "pager-only"))]
pub fn draw<DI: WriteOnlyDataCommand>(display: &mut Display<'_, DI>, app: &mut app::App) {
    match app.mode() {
        app::Mode::AppMenu => draw_app_menu(display.as_draw_target(), &*app),
        app::Mode::Settings => draw_settings(display.as_draw_target(), &*app),
        app::Mode::ChanMgr => draw_chanmgr(display.as_draw_target(), app),
        app::Mode::Contacts => draw_contacts(display.as_draw_target(), app),
        app::Mode::Scan => draw_scan(display.as_draw_target(), &*app),
        app::Mode::Search => draw_search(display.as_draw_target(), &*app),
        app::Mode::ScanQt => draw_scanqt(display.as_draw_target(), &*app),
        app::Mode::Fm => draw_fm(display.as_draw_target(), &*app),
        app::Mode::Spectrum => draw_spectrum(display.as_draw_target(), &*app),
        app::Mode::Satellite | app::Mode::SatelliteTracking => {
            draw_satellite(display.as_draw_target(), app);
        }
        app::Mode::Sstv => draw_sstv(display.as_draw_target(), app),
        app::Mode::Pager => draw_pager(display.as_draw_target(), &*app),
        _ => draw_standby(display.as_draw_target(), &*app),
    }
    display.flush();
}
