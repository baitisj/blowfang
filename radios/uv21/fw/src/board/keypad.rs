// UV-21 Pro keypad (bring-up key rounds, 11-23): columns PB0, PB4, PB9, PB10, PB11 driven low one
// at a time; rows PC13, PC14, PF6, PB14 read with pull-ups. The three side keys ground a row on
// their own, so they read in phase 0, with no column driven (see device/keypad.rs's table).
use super::pin::{pin, Pin};
use at32f421_pac as pac;

const COLS: [Pin; 5] = [pin(b'B', 0), pin(b'B', 4), pin(b'B', 9), pin(b'B', 10), pin(b'B', 11)];
const ROWS: [Pin; 4] = [pin(b'C', 13), pin(b'C', 14), pin(b'F', 6), pin(b'B', 14)];

pub fn init_keypad_pins(_gpiob: &pac::Gpiob) {
    for c in COLS {
        c.set(true);
        c.output();
    }
    for r in ROWS {
        r.input(1);
    }
}

pub fn set_keypad_rows_idle(_gpiob: &pac::Gpiob) {
    for c in COLS {
        c.set(true);
    }
}

/// phase 0: no column driven (the side keys); phase n: column n - 1 low
pub fn set_keypad_row(gpiob: &pac::Gpiob, phase: u8) {
    set_keypad_rows_idle(gpiob);
    if (1..=5).contains(&phase) {
        COLS[phase as usize - 1].set(false);
    }
}

/// the first row reading low (index into ROWS), if any
pub fn read_keypad_column(_gpiob: &pac::Gpiob) -> Option<u8> {
    ROWS.iter().position(|r| !r.read()).map(|i| i as u8)
}

/// The UV-5RH's second side key has a pin of its own; the UV-21's side keys are all on rows.
pub fn init_side_key2_pin(_gpioa: &pac::Gpioa) {}

pub fn read_side_key2(_gpioa: &pac::Gpioa) -> bool {
    false
}
