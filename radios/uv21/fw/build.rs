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

/// SURF (radios/uv21/surf, MIT C, sharing c/common/surf_core.inc with the K6 and the RT-950)
/// compiled with clang (`CC` overrides) and linked in as one object
fn build_surf(out: &PathBuf) {
    let src = PathBuf::from("../surf/uv21_surf.c");
    let obj = out.join("uv21_surf.o");
    let cc = env::var("CC").unwrap_or_else(|_| "clang".to_string());
    let status = Command::new(&cc)
        .args([
            "--target=thumbv7em-none-eabi",
            "-mcpu=cortex-m4",
            "-mthumb",
            "-mfloat-abi=soft",
            "-Oz",
            "-ffreestanding",
            "-fno-builtin",
            "-fno-unwind-tables",
            "-fno-asynchronous-unwind-tables",
            "-ffunction-sections",
            "-fdata-sections",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-c",
        ])
        .arg(&src)
        .arg("-o")
        .arg(&obj)
        .status()
        .expect("clang for SURF (radios/uv21/surf)");
    assert!(status.success(), "SURF failed to compile");
    println!("cargo:rustc-link-arg={}", obj.display());
    println!("cargo:rerun-if-changed=../surf/uv21_surf.c");
    println!("cargo:rerun-if-changed=../surf/uv21_surf.h");
    println!("cargo:rerun-if-changed=../../../c/common/surf_core.inc");
    println!("cargo:rerun-if-env-changed=CC");
}

fn main() {
    let out = &PathBuf::from(env::var_os("OUT_DIR").unwrap());
    File::create(out.join("memory.x"))
        .unwrap()
        .write_all(include_bytes!("memory.x"))
        .unwrap();
    println!("cargo:rustc-link-search={}", out.display());
    println!("cargo:rerun-if-changed=memory.x");
    println!("cargo:rerun-if-changed=build.rs");

    build_surf(out);

    println!("cargo:rustc-env=GIT_VERSION={}", git_version());
    watch_git_dir();
}
