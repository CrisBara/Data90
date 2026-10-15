"""Resolve project and local configuration paths in source and frozen builds."""

import sys
from pathlib import Path


def is_frozen_build() -> bool:
    """Recognize applications frozen by PyInstaller or compiled by Nuitka."""
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _candidate_roots() -> tuple[Path, ...]:
    candidates = [Path.cwd()]
    if is_frozen_build():
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend((executable_dir, *executable_dir.parents))
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir))
    else:
        candidates.append(Path(__file__).resolve().parents[3])
    return tuple(dict.fromkeys(path.resolve() for path in candidates))


def project_root() -> Path:
    """Find the project directory containing the bootstrap script and Alembic config."""
    for root in _candidate_roots():
        if (root / "scripts" / "bootstrap.py").is_file() and (root / "alembic.ini").is_file():
            return root
    return _candidate_roots()[0]


def environment_file() -> Path:
    """Use the local project .env independently of the executable's launch directory."""
    project_env = project_root() / ".env"
    if project_env.is_file():
        return project_env
    for root in _candidate_roots():
        candidate = root / ".env"
        if candidate.is_file():
            return candidate
    return project_root() / ".env"


def alembic_resource_root() -> Path:
    """Return the directory that contains alembic.ini and the migration scripts."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        bundled = Path(bundle_dir)
        if (bundled / "alembic.ini").is_file() and (bundled / "alembic").is_dir():
            return bundled
    return project_root()
