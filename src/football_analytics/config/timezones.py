"""Make the bundled IANA timezone database visible to zoneinfo."""

import sys
from importlib.util import find_spec
from pathlib import Path
from zoneinfo import reset_tzpath


def configure_timezone_search_path() -> None:
    """Point zoneinfo at tzdata files in source, PyInstaller, or Nuitka builds."""
    candidates: list[Path] = []
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(Path(bundle_dir) / "tzdata" / "zoneinfo")
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        candidates.append(Path(sys.executable).resolve().parent / "tzdata" / "zoneinfo")

    tzdata_spec = find_spec("tzdata")
    if tzdata_spec and tzdata_spec.origin:
        candidates.append(Path(tzdata_spec.origin).resolve().parent / "zoneinfo")

    for candidate in candidates:
        if candidate.is_dir():
            reset_tzpath([str(candidate)])
            return
