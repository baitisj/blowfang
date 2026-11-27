/// Threshold debouncer for digital inputs (keys, squelch, power sense): a new level is reported
/// once it has held for `THRESHOLD` samples after the one that changed it.
pub struct Debouncer {
    stable: bool,
    candidate: bool,
    count: u8,
}

impl Debouncer {
    const THRESHOLD: u8 = 4;

    pub fn new(initial: bool) -> Self {
        Debouncer {
            stable: initial,
            candidate: initial,
            count: Self::THRESHOLD,
        }
    }

    pub fn sample(&mut self, raw: bool) -> Option<bool> {
        if raw == self.candidate {
            if self.count < Self::THRESHOLD {
                self.count += 1;
            }
        } else {
            self.candidate = raw;
            self.count = 0;
        }

        if self.count >= Self::THRESHOLD && self.stable != self.candidate {
            self.stable = self.candidate;
            Some(self.stable)
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Debouncer;

    #[test]
    fn flips_after_four_more_matching_samples() {
        let mut d = Debouncer::new(false);
        assert_eq!(d.sample(true), None);
        assert_eq!(d.sample(true), None);
        assert_eq!(d.sample(true), None);
        assert_eq!(d.sample(false), None); // glitch resets count
        assert_eq!(d.sample(true), None);
        assert_eq!(d.sample(true), None);
        assert_eq!(d.sample(true), None);
        assert_eq!(d.sample(true), None);
        assert_eq!(d.sample(true), Some(true));
        assert_eq!(d.sample(true), None);
    }
}
