"""python3 -m sim.soapyfang: first, everything the session needs that the machine might lack, all
named at once with one Arch install line (hamlet), before the Pluto is touched"""
import importlib.util
import shutil
import sys


def missing(argv):
    need = [("unicorn", "python-unicorn", "unicorn"), ("numpy", "python-numpy", "numpy")]
    out = [(pkg, pip) for mod, pkg, pip in need if importlib.util.find_spec(mod) is None]
    src = next((argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--source"),
               next((a.split("=", 1)[1] for a in argv if a.startswith("--source=")), "synthetic"))
    if src.startswith("pluto") and not all(shutil.which(t) for t in ("iio_readdev", "iio_attr")):
        out.append(("libiio", None))                  # its command-line tools
    if src.startswith("soapy:") and importlib.util.find_spec("SoapySDR") is None:
        out.append(("soapysdr", None))
    return out


def check(argv):
    """stop here, naming everything missing, rather than part way into a session"""
    gone = missing(argv)
    if gone:
        names = ", ".join(p for p, _ in gone)
        pip = " ".join(q for _, q in gone if q)
        raise SystemExit(f"SoapyFang can't start: missing {names}.\n  Arch: sudo pacman -S --needed "
                         + " ".join(p for p, _ in gone) + (f"\n  elsewhere: pip install {pip}" if pip else ""))


if __name__ == "__main__":
    check(sys.argv[1:])
    from .workbench import main
    main()
