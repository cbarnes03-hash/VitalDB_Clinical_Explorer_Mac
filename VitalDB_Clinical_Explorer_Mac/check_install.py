"""Installation self-check used by install_windows.bat and run_windows.bat.

Exit code 0 = everything needed can be imported. Prints readable messages.
"""
import sys

MODULES = [
    ("numpy", "NumPy"),
    ("pandas", "pandas"),
    ("matplotlib", "Matplotlib"),
    ("PySide6.QtWidgets", "PySide6 (window toolkit)"),
    ("matplotlib.backends.backend_qtagg", "Matplotlib Qt backend"),
    ("vitaldb", "vitaldb (official VitalDB package)"),
]


def main() -> int:
    quiet = "--quiet" in sys.argv
    ok = True
    for mod, label in MODULES:
        try:
            __import__(mod)
            if not quiet:
                print(f"  OK       {label}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  MISSING  {label}: {type(exc).__name__}: {exc}")
    if ok:
        try:
            import os
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import vitaldb_explorer  # noqa: F401
            from vitaldb_explorer import data_access, processing, export  # noqa: F401
            if not quiet:
                print("  OK       VitalDB Clinical Explorer modules")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  PROBLEM  application files: {type(exc).__name__}: {exc}")
    if not quiet:
        print("Self-check passed." if ok else "Self-check FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
