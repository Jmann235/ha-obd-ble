"""Make `elm` and `vehicles` importable without Home Assistant.

Mirrors the sys.path arrangement used by scripts/bench.py: the obd_ble
package directory itself goes on the path, so its HA-importing __init__.py
never executes.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "obd_ble"))
