use core::cell::UnsafeCell;
use core::sync::atomic::{AtomicUsize, Ordering};

/// Lock-free single-producer / single-consumer byte ring buffer.
///
/// `N` must be a power of two (holds up to `N - 1` bytes). Uses only atomic loads and stores,
/// so it works on both Cortex-M0 (`thumbv6m`) and Cortex-M4 (`thumbv7em`) without disabling
/// interrupts or using `RefCell`.
pub struct SpscRing<const N: usize> {
    buf: UnsafeCell<[u8; N]>,
    head: AtomicUsize,
    tail: AtomicUsize,
}

unsafe impl<const N: usize> Sync for SpscRing<N> {}

impl<const N: usize> SpscRing<N> {
    const MASK: usize = {
        assert!(N.is_power_of_two() && N >= 2);
        N - 1
    };

    pub const fn new() -> Self {
        SpscRing {
            buf: UnsafeCell::new([0; N]),
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
        }
    }

    /// Producer side (e.g. USART RX ISR): append a byte, dropping it if the ring is full.
    #[inline]
    pub fn push(&self, byte: u8) {
        let head = self.head.load(Ordering::Relaxed);
        let next = (head + 1) & Self::MASK;
        if next == self.tail.load(Ordering::Acquire) {
            return;
        }
        unsafe { (*self.buf.get())[head] = byte };
        self.head.store(next, Ordering::Release);
    }

    /// Consumer side (e.g. main loop): pop the oldest byte, or `None` if empty.
    #[inline]
    pub fn pop(&self) -> Option<u8> {
        let tail = self.tail.load(Ordering::Relaxed);
        let head = self.head.load(Ordering::Acquire);
        if tail == head {
            return None;
        }
        let byte = unsafe { (*self.buf.get())[tail] };
        self.tail.store((tail + 1) & Self::MASK, Ordering::Release);
        Some(byte)
    }
}

#[cfg(test)]
mod tests {
    use super::SpscRing;

    #[test]
    fn fifo_and_drop_on_full() {
        let q = SpscRing::<4>::new();
        assert_eq!(q.pop(), None);
        q.push(10);
        q.push(20);
        q.push(30);
        q.push(40); // full (capacity N - 1 = 3): dropped
        assert_eq!(q.pop(), Some(10));
        assert_eq!(q.pop(), Some(20));
        q.push(50);
        assert_eq!(q.pop(), Some(30));
        assert_eq!(q.pop(), Some(50));
        assert_eq!(q.pop(), None);
    }
}
