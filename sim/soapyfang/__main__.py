try:
    import unicorn  # noqa: F401
except ImportError:
    raise SystemExit("SoapyFang needs the unicorn emulator: sudo pacman -S python-unicorn (Arch) or pip install unicorn")
from .workbench import main  # noqa: E402

main()
