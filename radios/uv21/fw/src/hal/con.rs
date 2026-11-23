//! UV-21 Pro: a few always-on console lines on USART1 (115200 8N1), in release builds too, so the
//! bench can tell from the cable alone that an image booted and what it found. Plain bytes and hex
//! only: core::fmt would cost kilobytes of a nearly full app area.

use super::uart;

pub fn puts(s: &str) {
    uart::write_bytes(s.as_bytes());
}

pub fn hex(v: u32, digits: u32) {
    for i in (0..digits).rev() {
        uart::write_byte(b"0123456789abcdef"[(v >> (4 * i) & 15) as usize]);
    }
}

/// "name=value" with a leading space
pub fn kv_hex(name: &str, v: u32, digits: u32) {
    puts(" ");
    puts(name);
    puts("=");
    hex(v, digits);
}

pub fn nl() {
    puts("\r\n");
}
