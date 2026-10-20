import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Scripts are run as `python scripts/<name>.py`; make them importable as top-level modules in tests.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
