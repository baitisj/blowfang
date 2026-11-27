/// Minimal byte-addressed NOR flash interface for [`WearLeveledRegion`].
pub trait FlashStorage {
    const SECTOR_SIZE: u32;
    fn read_bytes(&mut self, addr: u32, buf: &mut [u8]);
    fn write_bytes(&mut self, addr: u32, data: &[u8]);
    fn erase_sector(&mut self, addr: u32);
}

/// CRC-16/XMODEM (polynomial 0x1021, initial value 0x0000).
pub fn crc16_xmodem(data: &[u8]) -> u16 {
    let mut crc: u16 = 0;
    for &byte in data {
        crc ^= (byte as u16) << 8;
        for _ in 0..8 {
            crc = if crc & 0x8000 != 0 {
                (crc << 1) ^ 0x1021
            } else {
                crc << 1
            };
        }
    }
    crc
}

const CLEAR_MASK: [u8; 8] = [0xFE, 0xFC, 0xF8, 0xF0, 0xE0, 0xC0, 0x80, 0x00];
const MAX_SCAN_BYTES: usize = 32;

/// Append-only wear-leveled record store occupying one NOR flash sector.
pub struct WearLeveledRegion<const PAYLOAD_LEN: usize> {
    base_addr: u32,
    header_size: u32,
}

impl<const PAYLOAD_LEN: usize> WearLeveledRegion<PAYLOAD_LEN> {
    const SLOT_SIZE: u32 = PAYLOAD_LEN as u32 + 2;

    pub const fn new(base_addr: u32, header_size: u32) -> Self {
        WearLeveledRegion {
            base_addr,
            header_size,
        }
    }

    fn max_slots<F: FlashStorage>(&self) -> u32 {
        let by_space = (F::SECTOR_SIZE - self.header_size) / Self::SLOT_SIZE;
        let by_header_bits = self.header_size * 8;
        by_space.min(by_header_bits)
    }

    fn scan_bytes<F: FlashStorage>(&self) -> usize {
        (self.max_slots::<F>() as usize).div_ceil(8)
    }

    fn slot_addr(&self, index: u32) -> u32 {
        self.base_addr + self.header_size + index * Self::SLOT_SIZE
    }

    fn find_free_slot<F: FlashStorage>(&self, flash: &mut F) -> u32 {
        let scan_len = self.scan_bytes::<F>();
        debug_assert!(scan_len <= MAX_SCAN_BYTES);

        let mut bitmap = [0u8; MAX_SCAN_BYTES];
        flash.read_bytes(self.base_addr, &mut bitmap[..scan_len]);

        for (i, &byte) in bitmap[..scan_len].iter().enumerate() {
            if byte != 0 {
                return i as u32 * 8 + byte.trailing_zeros();
            }
        }
        u32::MAX
    }

    fn read_slot<F: FlashStorage>(&self, flash: &mut F, index: u32) -> Option<[u8; PAYLOAD_LEN]> {
        let mut buf = [0u8; PAYLOAD_LEN];
        let mut crc_buf = [0u8; 2];
        let addr = self.slot_addr(index);
        flash.read_bytes(addr, &mut buf);
        flash.read_bytes(addr + PAYLOAD_LEN as u32, &mut crc_buf);

        if u16::from_le_bytes(crc_buf) == crc16_xmodem(&buf) {
            Some(buf)
        } else {
            None
        }
    }

    fn write_slot<F: FlashStorage>(&self, flash: &mut F, index: u32, payload: &[u8; PAYLOAD_LEN]) {
        let addr = self.slot_addr(index);
        flash.write_bytes(addr, payload);
        flash.write_bytes(
            addr + PAYLOAD_LEN as u32,
            &crc16_xmodem(payload).to_le_bytes(),
        );
    }

    pub fn load<F: FlashStorage>(&self, flash: &mut F) -> Option<[u8; PAYLOAD_LEN]> {
        let index = self.find_free_slot(flash);
        if index >= self.max_slots::<F>() {
            return None;
        }
        self.read_slot(flash, index)
    }

    pub fn save<F: FlashStorage>(&self, flash: &mut F, payload: &[u8; PAYLOAD_LEN]) {
        let index = self.find_free_slot(flash);
        let max_slots = self.max_slots::<F>();

        if index < max_slots && self.read_slot(flash, index).as_ref() == Some(payload) {
            return;
        }

        let has_room = matches!(index.checked_add(1), Some(next) if next < max_slots);
        if has_room {
            self.write_slot(flash, index + 1, payload);

            let marker_byte = index / 8;
            let bit = (index % 8) as usize;
            flash.write_bytes(self.base_addr + marker_byte, &[CLEAR_MASK[bit]]);
        } else {
            flash.erase_sector(self.base_addr);
            self.write_slot(flash, 0, payload);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{crc16_xmodem, FlashStorage, WearLeveledRegion};

    struct MemFlash {
        mem: [u8; 4096],
        erases: u32,
    }

    impl FlashStorage for MemFlash {
        const SECTOR_SIZE: u32 = 4096;
        fn read_bytes(&mut self, addr: u32, buf: &mut [u8]) {
            let a = addr as usize;
            buf.copy_from_slice(&self.mem[a..a + buf.len()]);
        }
        fn write_bytes(&mut self, addr: u32, data: &[u8]) {
            let a = addr as usize;
            for (dst, &src) in self.mem[a..a + data.len()].iter_mut().zip(data) {
                *dst &= src; // NOR flash can only clear 1 -> 0
            }
        }
        fn erase_sector(&mut self, addr: u32) {
            assert_eq!(addr, 0);
            self.mem.fill(0xFF);
            self.erases += 1;
        }
    }

    #[test]
    fn crc16_xmodem_known_vector() {
        assert_eq!(crc16_xmodem(b"123456789"), 0x31C3);
    }

    #[test]
    fn wear_leveled_round_trip_and_wrap() {
        let mut fl = MemFlash { mem: [0xFF; 4096], erases: 0 };
        let reg = WearLeveledRegion::<8>::new(0, 1); // 8 header bits -> 8 slots
        assert_eq!(reg.load(&mut fl), None);
        for i in 0u8..12 {
            let payload = [i; 8];
            reg.save(&mut fl, &payload);
            assert_eq!(reg.load(&mut fl), Some(payload));
        }
        assert!(fl.erases >= 1);
    }
}
