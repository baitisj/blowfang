//! Text output without core::fmt (2026-11-10). The serial report and the pager screens keep
//! their format strings, but `rl!` and `out!` hand them to `fmt` here, a small interpreter, with
//! the values as a plain array (two words each), in place of core::fmt's formatter, its integer
//! Display and hex impls and its digit table (about 3 KB of flash in the voice + EXPLORE image).
//! A line is one call and its text only, with no piece table.
//!
//! The forms it reads, giving the same text core::fmt gives:
//!
//! - `{}`: an integer (u8 to u32, usize, i8 to i32), `&str`, `char` or `bool`
//! - `{:x}` `{:X}` `{:#x}`, with a width and `0`: `{:02x}` `{:08x}` `{:#010x}`; `{:03o}` likewise
//! - `{:N}` `{:0N}` `{:>N}` `{:<N}` `{:+}` on integers
//! - `{{` and `}}`
//!
//! Named arguments in the string (`{crc:08x}`) are not read: pass them after it. Full Aura's
//! screens, built but never run in the pager images, keep core::fmt.

/// Where text goes.
pub trait Sink {
    fn bytes(&mut self, b: &[u8]);
}

/// for out!: any sink (a value, a `&mut` one, a `&mut dyn Sink` or a temporary) as
/// `&mut dyn Sink`
pub trait AsSink {
    fn dyn_sink(&mut self) -> &mut dyn Sink;
}

impl<S: Sink> AsSink for S {
    #[inline]
    fn dyn_sink(&mut self) -> &mut dyn Sink {
        self
    }
}

impl AsSink for dyn Sink + '_ {
    #[inline]
    fn dyn_sink(&mut self) -> &mut dyn Sink {
        self
    }
}

impl<S: Sink + ?Sized> Sink for &mut S {
    #[inline]
    fn bytes(&mut self, b: &[u8]) {
        (**self).bytes(b)
    }
}

/// The serial report: USART1 TX, queued and sent by the interrupt (hal::uart), so a line
/// doesn't hold the caller for its 87 us a character.
pub struct Uart;

impl Sink for Uart {
    fn bytes(&mut self, b: &[u8]) {
        for &c in b {
            crate::hal::uart::send_byte(c);
        }
    }
}

/// One value for `fmt`: a `&str` as its address and length, an integer or a char as its value
/// and INT (with the integer's bits and SIGNED) or CHAR.
#[derive(Clone, Copy)]
pub struct Arg(usize, u32);

const INT: u32 = 0x8000_0000;
const SIGNED: u32 = 0x100;
const CHAR: u32 = 0x4000_0000;

pub trait A {
    fn arg(self) -> Arg;
}

impl A for &str {
    #[inline]
    fn arg(self) -> Arg {
        Arg(self.as_ptr() as usize, self.len() as u32)
    }
}

impl A for bool {
    #[inline]
    fn arg(self) -> Arg {
        (if self { "true" } else { "false" }).arg()
    }
}

impl A for char {
    #[inline]
    fn arg(self) -> Arg {
        Arg(self as usize, CHAR)
    }
}

impl<T: A + Copy> A for &T {
    #[inline]
    fn arg(self) -> Arg {
        (*self).arg()
    }
}

macro_rules! ints {
    ($($t:ty : $k:expr)*) => {$(
        impl A for $t {
            #[inline]
            fn arg(self) -> Arg {
                Arg(self as i32 as u32 as usize, INT | $k)
            }
        }
    )*};
}

ints!(u8:8 u16:16 u32:32 usize:32 i8:SIGNED|8 i16:SIGNED|16 i32:SIGNED|32);

const HEX: u8 = 1;
const UPPER: u8 = 2;
const ZERO: u8 = 4;
const LEFT: u8 = 8;
const PREFIX: u8 = 16;
const PLUS: u8 = 32;
const OCT: u8 = 64;

fn pad(o: &mut dyn Sink, c: u8, n: usize) {
    for _ in 0..n {
        o.bytes(&[c]);
    }
}

/// Prints `f` with its `{}`s filled from `a`, in order.
#[inline(never)]
pub fn fmt(o: &mut dyn Sink, f: &str, a: &[Arg]) {
    let f = f.as_bytes();
    let mut i = 0;
    let mut lit = 0;
    let mut k = 0;
    while i < f.len() {
        let c = f[i];
        if c != b'{' && c != b'}' {
            i += 1;
            continue;
        }
        o.bytes(&f[lit..i]);
        i += 1;
        if f.get(i) == Some(&c) {
            // `{{` or `}}`: the second one starts the next text
            lit = i;
            i += 1;
            continue;
        }
        let (mut fl, mut w) = (0u8, 0usize);
        if f.get(i) == Some(&b':') {
            i += 1;
            while i < f.len() && f[i] != b'}' {
                match f[i] {
                    b'#' => fl |= PREFIX,
                    b'+' => fl |= PLUS,
                    b'<' => fl |= LEFT,
                    b'0' if w == 0 => fl |= ZERO,
                    d @ b'0'..=b'9' => w = w * 10 + (d - b'0') as usize,
                    b'x' => fl |= HEX,
                    b'X' => fl |= HEX | UPPER,
                    b'o' => fl |= OCT,
                    _ => {}
                }
                i += 1;
            }
        }
        i += 1; // the `}`
        lit = i;
        let Some(&Arg(v, t)) = a.get(k) else { continue };
        k += 1;
        if t & INT != 0 {
            num(o, v as u32, t, fl, w);
        } else if t & CHAR != 0 {
            let mut b = [0u8; 4];
            let s = char::from_u32(v as u32).unwrap_or('?').encode_utf8(&mut b);
            o.bytes(s.as_bytes());
        } else {
            o.bytes(unsafe { core::slice::from_raw_parts(v as *const u8, t as usize) });
        }
    }
    o.bytes(&f[lit..]);
}

/// One integer: the sign (or "0x"), then the digits, padded to `w` as core::fmt pads (zeros go
/// after the sign and the prefix, spaces before them or, for `<`, after the digits). Hex and
/// octal take the integer's own bits, as core::fmt's do for a negative one.
fn num(o: &mut dyn Sink, v: u32, t: u32, fl: u8, w: usize) {
    let bits = t & 0x3F;
    let neg = t & SIGNED != 0 && (v as i32) < 0 && fl & (HEX | OCT) == 0;
    let mut v = if fl & (HEX | OCT) != 0 {
        if bits < 32 { v & ((1 << bits) - 1) } else { v }
    } else if neg {
        (v as i32).unsigned_abs()
    } else {
        v
    };
    let mut d = [0u8; 11];
    let mut i = d.len();
    loop {
        i -= 1;
        if fl & HEX != 0 {
            let x = (v & 15) as u8;
            d[i] = if x < 10 {
                b'0' + x
            } else if fl & UPPER != 0 {
                b'A' - 10 + x
            } else {
                b'a' - 10 + x
            };
            v >>= 4;
        } else if fl & OCT != 0 {
            d[i] = b'0' + (v & 7) as u8;
            v >>= 3;
        } else {
            let q = v / 10;
            d[i] = b'0' + (v - q * 10) as u8;
            v = q;
        }
        if v == 0 {
            break;
        }
    }
    let head: &[u8] = if neg {
        b"-"
    } else if fl & PLUS != 0 {
        b"+"
    } else if fl & PREFIX != 0 {
        b"0x"
    } else {
        b""
    };
    let fill = w.saturating_sub(head.len() + d.len() - i);
    if fl & (ZERO | LEFT) == 0 {
        pad(o, b' ', fill);
    }
    o.bytes(head);
    if fl & ZERO != 0 {
        pad(o, b'0', fill);
    }
    o.bytes(&d[i..]);
    if fl & LEFT != 0 {
        pad(o, b' ', fill);
    }
}

/// `b` as text, up to its first byte that isn't ASCII: in place of core::str::from_utf8 (376 B
/// of flash), as everything the pager images show or report is ASCII (names, callsigns, cards).
#[inline(never)]
pub fn ascii(b: &[u8]) -> &str {
    let n = b.iter().position(|&c| c >= 0x80).unwrap_or(b.len());
    unsafe { core::str::from_utf8_unchecked(&b[..n]) }
}

/// `out!(sink, "format", values...)`: core::fmt's `write!`, without core::fmt.
#[macro_export]
macro_rules! out {
    ($w:expr, $f:literal $(, $a:expr)* $(,)?) => {{
        use $crate::out::AsSink as _;
        // (a match keeps a temporary sink alive to the end)
        match ($w).dyn_sink() {
            o => $crate::out::fmt(o, $f, &[$($crate::out::A::arg($a)),*]),
        }
        // never run: rustc checks the string against the values as for write!
        if false {
            let _ = core::format_args!($f $(, $a)*);
        }
    }};
}

/// `rl!("format", values...)`: one serial report line, then CR LF.
#[macro_export]
macro_rules! rl {
    ($f:literal $(, $a:expr)* $(,)?) => {{
        $crate::out::line($f, &[$($crate::out::A::arg($a)),*]);
        // never run: rustc checks the string against the values as for write!
        if false {
            let _ = core::format_args!($f $(, $a)*);
        }
    }};
}

#[inline(never)]
pub fn line(f: &str, a: &[Arg]) {
    fmt(&mut Uart, f, a);
    Uart.bytes(b"\r\n");
}

/// main's debug lines (DebugUart, which sends nothing in a release build): only in a debug build.
#[macro_export]
macro_rules! dln {
    ($w:expr, $f:literal $(, $a:expr)* $(,)?) => {{
        #[cfg(debug_assertions)]
        {
            $crate::out!($w, $f $(, $a)*);
            $crate::out!($w, "\n");
        }
        #[cfg(not(debug_assertions))]
        {
            let _ = &$w;
            $( let _ = &$a; )*
        }
    }};
}
