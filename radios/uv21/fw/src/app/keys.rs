use super::keyfn;
use super::{
    digit_value, App, ChVfoMode, Mode, CHANNEL_INPUT_DIGITS, VFO_INPUT_DIGITS,
};
use crate::device::keypad::{KeyEvent, KeyEventKind, KeyId};
use cortex_m::peripheral::SYST;

// top-level dispatch
pub(super) fn dispatch(app: &mut App, syst: &mut SYST, ev: KeyEvent) {
    if app.key_lock
        && !matches!(ev.key, KeyId::Side1 | KeyId::Side2)
        && !matches!((ev.key, ev.kind), (KeyId::Asterisk, KeyEventKind::Long))
    {
        return;
    }

    app.reset_key_idle();
    app.note_power_save_activity(syst);
    app.note_backlight_activity();

    if matches!(ev.kind, KeyEventKind::Single | KeyEventKind::Long) {
        app.radio.play_beep(syst);
    }

    match app.mode {
        Mode::Standby => dispatch_standby(app, syst, ev),
    }
}

fn dispatch_standby(app: &mut App, syst: &mut SYST, ev: KeyEvent) {
    match ev.kind {
        KeyEventKind::Single => {
            if let Some(d) = digit_value(ev.key) {
                let max_len = match app.sides[app.master].vfo_chan {
                    ChVfoMode::Channel => CHANNEL_INPUT_DIGITS,
                    ChVfoMode::Vfo => VFO_INPUT_DIGITS,
                };
                app.input.push(d);
                if app.input.len >= max_len {
                    app.commit_input(syst);
                }
                return;
            }
            match ev.key {
                KeyId::Up => app.step(syst, true),
                KeyId::Down => app.step(syst, false),
                KeyId::Exit => app.input.clear(),
                KeyId::Vm => app.toggle_vfo_channel(syst),
                KeyId::Ab => app.want_surf = true,
                KeyId::Menu => {
                    if app.sides[app.master].vfo_chan == ChVfoMode::Channel
                        && app.input.len > 0
                    {
                        app.commit_input(syst);
                    }
                }
                KeyId::Side1 | KeyId::Side2 | KeyId::Band => {
                    let func = keyfn::from_u8(short_function(app, ev.key));
                    keyfn::invoke(app, syst, func);
                }
                _ => {}
            }
        }
        KeyEventKind::Repeat => match ev.key {
            KeyId::Up => app.step(syst, true),
            KeyId::Down => app.step(syst, false),
            _ => {}
        },
        KeyEventKind::Long => match ev.key {
            KeyId::Asterisk => app.key_lock = !app.key_lock,
            KeyId::Side1 | KeyId::Side2 | KeyId::Band => {
                let func = keyfn::from_u8(long_function(app, ev.key));
                keyfn::invoke(app, syst, func);
            }
            _ => {}
        },
        _ => {}
    }
}

fn short_function(app: &App, key: KeyId) -> u8 {
    match key {
        KeyId::Side1 => app.settings.side1_short,
        KeyId::Side2 => app.settings.side2_short,
        KeyId::Band => app.settings.band_short,
        _ => 0,
    }
}

fn long_function(app: &App, key: KeyId) -> u8 {
    match key {
        KeyId::Side1 => app.settings.side1_long,
        KeyId::Side2 => app.settings.side2_long,
        KeyId::Band => app.settings.band_long,
        _ => 0,
    }
}

