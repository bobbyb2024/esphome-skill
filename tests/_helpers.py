import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "esphome-builder"
SCRIPT = SKILL / "scripts" / "esphome_dashboard.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("esphome_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def patched(module, **replacements):
    old = {name: getattr(module, name) for name in replacements}
    try:
        for name, value in replacements.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in old.items():
            setattr(module, name, value)
