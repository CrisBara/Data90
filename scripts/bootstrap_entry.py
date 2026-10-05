"""PyInstaller entry point for the first-run bootstrap executable."""

from football_analytics.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
