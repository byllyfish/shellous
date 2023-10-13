import sys
from pathlib import Path

from shellous import sh

_DIR = Path(__file__).parent
_PY = sh(sys.executable).env(PYTHONPATH=_DIR.parents[1])


async def test_example1():
    "Test the example1 program."
    result = await _PY(_DIR / "example1.py")
    assert result == "arbitrary\r\nYou typed: arbitrary\r\n\n"
