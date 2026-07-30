"""Make the repo root importable as `custom_components.aat_multiroom.*`
without needing to install the package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
