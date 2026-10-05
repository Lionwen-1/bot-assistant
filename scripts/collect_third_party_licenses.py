"""Copy licenses that match the Python environment used for this EXE build."""

from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
import shutil
import sys


def collect(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    python_root = Path(sys.base_prefix)
    python_license = python_root / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError(f"Python license not found: {python_license}")
    shutil.copyfile(python_license, destination / "Python-LICENSE.txt")

    pillow = importlib.metadata.distribution("Pillow")
    pillow_license = next(
        (entry for entry in pillow.files or ()
         if entry.name == "LICENSE" and "licenses" in entry.parts),
        None,
    )
    if pillow_license is None:
        raise FileNotFoundError("Pillow license missing from installed distribution")
    shutil.copyfile(pillow.locate_file(pillow_license), destination / "Pillow-LICENSE.txt")

    tkinter_license = next(iter(sorted(python_root.glob("tcl/tk*/license.terms"))), None)
    if tkinter_license is None:
        raise FileNotFoundError(f"Tcl/Tk license not found under {python_root / 'tcl'}")
    shutil.copyfile(tkinter_license, destination / "Tcl-Tk-LICENSE.txt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    collect(parser.parse_args().destination)
