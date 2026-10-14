"""Module entry point for ``python -m dicom_inventory``."""

from .gui.application import run


if __name__ == "__main__":
    raise SystemExit(run())
