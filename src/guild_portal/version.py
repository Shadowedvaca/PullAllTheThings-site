"""Authoritative application and deployment identity."""

from pathlib import Path
import re


_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"
APP_VERSION = _VERSION_FILE.read_text(encoding="utf-8").strip()

if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", APP_VERSION):
    raise RuntimeError(f"Invalid semantic version in {_VERSION_FILE.name}")
