use std::env;
use std::fs::File;
use std::io::Write;
use std::path::PathBuf;
use std::process::Command;

fn git_version() -> String {
    let commit = Command::new("git")
        .args(["rev-parse", "--short=8", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    let Some(commit) = commit else {
        return "unknown".to_string();
    };
    let dirty = Command::new("git")
        .args(["status", "--porcelain"])
        .output()
        .is_ok_and(|o| !o.stdout.is_empty());
    if dirty {
        format!("{commit}-dirty")
    } else {
        commit
    }
}

fn watch_git_dir() {
    let Ok(out) = Command::new("git")
        .args(["rev-parse", "--git-dir"])
        .output()
    else {
        return;
    };
    if !out.status.success() {
        return;
    }
    let dir = PathBuf::from(String::from_utf8_lossy(&out.stdout).trim());
    println!("cargo:rerun-if-changed={}", dir.join("HEAD").display());
    println!("cargo:rerun-if-changed={}", dir.join("index").display());
}

fn main() {
    let out = &PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let feat = |f: &str| env::var_os(format!("CARGO_FEATURE_{f}")).is_some();
    // hal::ram0 also takes sotto's C statics (libsotto_k6.a's .bss, 444 B: the hiss image's stack
    // was the arena's limit, 2026-11-19), in the voice + EXPLORE image too since the leaner
    // pager-only App left room there (level 16). Not lean: its library keeps the decoder's
    // memory in a static of its own, too big for RAM0, and it has the stack to spare
    let mut mem = include_str!("memory.x.in").to_string();
    if let Ok(len) = env::var("K6_FLASH_LEN") {
        mem = mem.replace("LENGTH = 120K", &format!("LENGTH = {len}"));
    }
    println!("cargo:rerun-if-env-changed=K6_FLASH_LEN");
    if !feat("LEAN") {
        mem = mem.replace("/* @SOTTO_BSS@", "*libsotto_k6.a:*(.bss .bss.* COMMON);\n    /*");
    }
    File::create(out.join("memory.x"))
        .unwrap()
        .write_all(mem.as_bytes())
        .unwrap();
    println!("cargo:rustc-link-search={}", out.display());
    println!("cargo:rerun-if-changed=memory.x.in");
    println!("cargo:rerun-if-changed=build.rs");

    // sotto voice pager (src/app/pager.rs): the decoder and packet code, a C
    // static library built by sotto's tools/k6_build.sh and copied to lib/
    let lib = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("lib");
    println!("cargo:rustc-link-search=native={}", lib.display());
    println!("cargo:rustc-link-lib=static=sotto_k6");
    println!("cargo:rerun-if-changed=lib/libsotto_k6.a");

    // `shared_lcd`: the screen's page buffer in the idle PWM ring (hal::arena, device::display)
    // in the images that play voice from SPI codebooks: pager-only, not lean (the install and
    // MCU-flash table images), not EXPLORE (its sweep keeps the ring while it draws)
    println!("cargo:rustc-check-cfg=cfg(shared_lcd)");
    if feat("PAGER_ONLY") && !feat("LEAN") && (!feat("EXPLORE") || feat("VOICE_EXPLORE")) {
        println!("cargo:rustc-cfg=shared_lcd");
    }
    // `banded`: every screen drawn a controller page (8 rows) at a time, 128 bytes of stack
    // (ui::Band, ui::screen!), no page buffer at all: the 8 kHz voice + EXPLORE image, whose
    // EXPLORE keeps its sweep and Listen for ID state in the ring (the shared page buffer's home)
    println!("cargo:rustc-check-cfg=cfg(banded)");
    if feat("VOICE_EXPLORE") {
        println!("cargo:rustc-cfg=banded");
    }
    // `small_arena`: the shared block is only the store and the ring (hal::arena): the lean
    // images and EXPLORE without voice
    println!("cargo:rustc-check-cfg=cfg(small_arena)");
    if feat("LEAN") || (feat("EXPLORE") && !feat("VOICE_EXPLORE")) {
        println!("cargo:rustc-cfg=small_arena");
    }

    // The 8 kHz voice + EXPLORE image runs EXPLORE's sweep and Listen for ID from code pages in
    // SPI flash (app::pager::explore `page`, sotto's tools/k6_pages.py): its build ID in MCU flash
    // (k6_build_id, the note's 8 bytes), which each page made for it ends with, and the image's
    // functions the pages call kept although nothing here calls them (K6_PAGE_IMPORTS: a linker
    // script of EXTERN lines that build.sh writes)
    println!("cargo:rerun-if-env-changed=K6_PAGE_IMPORTS");
    if feat("VOICE_EXPLORE") {
        File::create(out.join("k6id.x"))
            .unwrap()
            .write_all(
                b"SECTIONS {\n  .k6_id : ALIGN(4) { k6_build_id = . + 16; KEEP(*(.note.gnu.build-id)) } > FLASH\n} INSERT AFTER .rodata;\n",
            )
            .unwrap();
        println!("cargo:rustc-link-arg=--build-id=fast");
        println!("cargo:rustc-link-arg=-Tk6id.x");
        if let Some(x) = env::var_os("K6_PAGE_IMPORTS") {
            println!("cargo:rustc-link-arg=-T{}", PathBuf::from(x).display());
        }
    }

    println!("cargo:rustc-env=GIT_VERSION={}", git_version());
    watch_git_dir();
}
