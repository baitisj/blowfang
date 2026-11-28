use crate::board;
use crate::drivers::display_spec;
use display_interface::WriteOnlyDataCommand;
#[cfg(not(banded))]
use display_interface::DataFormat;

/// LCD electronic-volume register values for contrast levels 0-4.
pub const CONTRAST_VOLUMES: [u8; 5] = [37, 41, 45, 48, 51];

#[cfg(not(banded))]
pub type Lcd<'a, DI> = st7565::ST7565<
    DI,
    display_spec::Sc5260Spec,
    st7565::modes::GraphicsMode<'a, 128, 8>,
    128,
    64,
    8,
>;
/// The `banded` image keeps no copy of the screen at all: the driver in raw mode, and every
/// screen sent a page (128 bytes of stack) at a time as it is drawn (draw_pages)
#[cfg(banded)]
pub type Lcd<'a, DI> = st7565::ST7565<DI, display_spec::Sc5260Spec, st7565::modes::RawMode, 128, 64, 8>;

pub struct Display<'a, DI: WriteOnlyDataCommand> {
    lcd: Option<Lcd<'a, DI>>,
    #[cfg(banded)]
    _a: core::marker::PhantomData<&'a ()>,
}

/// What the full-Aura screens (built but never run in the `banded` image: main's full-Aura loop
/// after the pager-only one) draw on there: nothing
#[cfg(banded)]
pub struct NoScreen;
#[cfg(banded)]
static mut NO_SCREEN: NoScreen = NoScreen;
#[cfg(banded)]
impl embedded_graphics::geometry::OriginDimensions for NoScreen {
    fn size(&self) -> embedded_graphics::geometry::Size {
        embedded_graphics::geometry::Size::new(128, 64)
    }
}
#[cfg(banded)]
impl embedded_graphics::draw_target::DrawTarget for NoScreen {
    type Color = embedded_graphics::pixelcolor::BinaryColor;
    type Error = core::convert::Infallible;
    fn draw_iter<I>(&mut self, _: I) -> Result<(), Self::Error> {
        Ok(())
    }
}

impl<'a, DI: WriteOnlyDataCommand> Display<'a, DI> {
    pub fn new(lcd: Lcd<'a, DI>) -> Self {
        Self {
            lcd: Some(lcd),
            #[cfg(banded)]
            _a: core::marker::PhantomData,
        }
    }

    #[cfg(banded)]
    pub fn flush(&mut self) {}

    /// Full Aura's contrast setting, built but never run in the `banded` image
    #[cfg(banded)]
    pub fn set_contrast(&mut self, _level: u8) {}

    #[cfg(banded)]
    pub fn as_draw_target(&mut self) -> &mut NoScreen {
        unsafe { &mut *core::ptr::addr_of_mut!(NO_SCREEN) }
    }

    #[cfg(not(banded))]
    pub fn flush(&mut self) {
        if let Some(ref mut lcd) = self.lcd {
            lcd.flush().ok();
        }
    }

    /// Apply a contrast level (0-4). The st7565 crate keeps its command
    /// interface private, so we detach the SPI interface, push the two
    /// command bytes raw, and attach it back.
    #[cfg(not(banded))]
    pub fn set_contrast(&mut self, level: u8) {
        let lcd = self.lcd.take().unwrap();
        let (detached, mut interface) = lcd.release_display_interface();
        interface
            .send_commands(DataFormat::U8(&[
                0x81,
                CONTRAST_VOLUMES[level.min(4) as usize],
            ]))
            .ok();
        self.lcd = Some(detached.attach_display_interface(interface));
    }

    /// Give the UI layer a `&mut` reference to the raw ST7565 for drawing.
    #[cfg(not(shared_lcd))]
    pub fn as_draw_target(&mut self) -> &mut Lcd<'a, DI> {
        self.lcd.as_mut().unwrap()
    }

    /// Give the UI layer a `&mut` reference to the raw ST7565 for drawing: a blank frame, in a
    /// page buffer made afresh in the idle PWM ring (`shared_page_buffer`). The driver is
    /// rebuilt around it, so the buffer it held before (which a playback may have written over
    /// since) is never read again. Every caller draws a whole screen and then flushes it, all
    /// of it: the controller gets the 1 KB, about 1.5 ms of SPI.
    #[cfg(all(shared_lcd, not(banded)))]
    pub fn as_draw_target(&mut self) -> &mut Lcd<'a, DI> {
        let (old, interface) = self.lcd.take().unwrap().release_display_interface();
        drop(old);
        let lcd = st7565::ST7565::new(interface, display_spec::Sc5260Spec)
            .into_graphics_mode(shared_page_buffer());
        self.lcd.insert(lcd)
    }

    /// Draws the screen a controller page (8 pixel rows, 128 bytes, bit 0 the top row) at a
    /// time: `draw` fills each page's bytes, cleared first, and each goes out as soon as it is
    /// drawn. For a RAM module's screen, while the module has the shared block (the ring, the
    /// page buffer's home, included) to itself.
    #[cfg(banded)]
    pub fn draw_pages(&mut self, mut draw: impl FnMut(u8, &mut [u8; 128])) {
        let lcd = self.lcd.as_mut().unwrap();
        let col = <display_spec::Sc5260Spec as st7565::DisplaySpecs<128, 64, 8>>::COLUMN_OFFSET;
        let mut buf = [0u8; 128];
        for page in 0..8u8 {
            buf.iter_mut().for_each(|b| *b = 0);
            draw(page, &mut buf);
            lcd.set_page(page).ok();
            lcd.set_column(col).ok();
            lcd.write_pixel_data(&buf).ok();
            grab::page(page, &buf);
        }
    }

    #[cfg(all(shared_lcd, not(feature = "lean"), not(banded)))]
    pub fn draw_pages(&mut self, mut draw: impl FnMut(u8, &mut [u8; 128])) {
        let (detached, mut interface) = self.lcd.take().unwrap().release_display_interface();
        let col = <display_spec::Sc5260Spec as st7565::DisplaySpecs<128, 64, 8>>::COLUMN_OFFSET;
        let mut buf = [0u8; 128];
        for page in 0..8u8 {
            buf.iter_mut().for_each(|b| *b = 0);
            draw(page, &mut buf);
            interface
                .send_commands(DataFormat::U8(&[0xB0 | page, 0x10 | (col >> 4), col & 0x0F]))
                .ok();
            interface.send_data(DataFormat::U8(&buf)).ok();
        }
        self.lcd = Some(detached.attach_display_interface(interface));
    }
}

/// The ST7565 page buffer (1,120 bytes with its dirty marks) of the images that play voice from
/// SPI codebooks: in the PWM ring (hal::arena), which nothing uses while the screen is drawn
/// (no redraw while a message plays or streams), instead of main's stack frame for good. Zeroed
/// here, before a reference to it exists; the driver marks it all dirty when it takes it.
#[cfg(all(shared_lcd, not(banded)))]
pub fn shared_page_buffer() -> &'static mut st7565::GraphicsPageBuffer<128, 8> {
    type Buf = st7565::GraphicsPageBuffer<128, 8>;
    const _: () = assert!(core::mem::size_of::<Buf>() <= crate::hal::arena::RING_BYTES);
    let p = crate::hal::arena::lcd() as *mut u32;
    // word stores, no memset: compiler_builtins' mem functions cost flash
    for i in 0..core::mem::size_of::<Buf>() / 4 {
        unsafe { core::ptr::write_volatile(p.add(i), 0) };
    }
    unsafe { &mut *(p as *mut Buf) }
}

pub struct Backlight {
    gpiof: &'static kd32f328_pac::gpiof::RegisterBlock,
}

impl Backlight {
    pub fn new(gpiof: &'static kd32f328_pac::gpiof::RegisterBlock) -> Self {
        Self { gpiof }
    }

    pub fn on(&self) {
        board::set_lcd_backlight(self.gpiof, true);
    }

    pub fn off(&self) {
        board::set_lcd_backlight(self.gpiof, false);
    }
}

/// a screen tap: page (0-7), its 128 bytes as sent (bit 0 the top row), the module's context
pub type Hook = unsafe extern "C" fn(i32, *const u8, *mut u8);

/// The screen-grab hook (module API level 17, 2026-11-28; Jeff: "something super lightweight that
/// provides hooks for a framebuffer if needed for testing"): no copy of the screen, only a
/// function that sees each page draw_pages sends, set by a RAM module while it runs
/// (app::pager mod_screen) and cleared when it returns. c/k6/mod/scrgrab.c keeps the 1 KB in its
/// own scratch and prints it in c/common/scrgrab.h's lines.
#[cfg(banded)]
pub mod grab {
    use core::ptr::{addr_of, addr_of_mut};
    pub type Hook = super::Hook;
    static mut HOOK: Option<(Hook, *mut u8)> = None;

    pub fn set(h: Option<(Hook, *mut u8)>) {
        unsafe { *addr_of_mut!(HOOK) = h };
    }

    #[inline(never)]
    pub fn page(page: u8, buf: &[u8; 128]) {
        if let Some((f, ctx)) = unsafe { *addr_of!(HOOK) } {
            unsafe { f(page as i32, buf.as_ptr(), ctx) };
        }
    }
}
