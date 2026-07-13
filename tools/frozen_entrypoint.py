"""PyInstaller script entry point that preserves package-relative imports."""

from soft_actuator_testing.bootstrap import main


if __name__ == "__main__":
    raise SystemExit(main())
