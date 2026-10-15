"""PyInstaller entry point for the first-run bootstrap executable."""

from data90.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
