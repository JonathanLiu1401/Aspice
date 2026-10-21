#!/usr/bin/env python3
"""Install the aspice student version's class-mode gate into a bridge-lite Python.

    <bridge python> scripts/install_student.py           # install (writes aspice_student.pth into site-packages)
    <bridge python> scripts/install_student.py --remove  # uninstall

Run it with the Python that has virtuoso_bridge installed (the one the `virtuoso-bridge` CLI uses).
"""
import sys
import sysconfig
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
PTH = Path(sysconfig.get_paths()["purelib"]) / "aspice_student.pth"


def main() -> int:
    if "--remove" in sys.argv:
        PTH.unlink(missing_ok=True)
        print(f"removed {PTH}")
        return 0
    PTH.write_text(f"import sys; sys.path.insert(0, {str(LIB)!r}); import classmode_patch; classmode_patch.install()\n")
    print(f"installed {PTH}: class mode is on for every virtuoso-bridge client in this Python "
          "(VB_CLASS_MODE=0 switches it off, VB_CLASS_ALLOW=sizing unlocks a category)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
