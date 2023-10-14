import sys
from pathlib import Path

import pytest

from shellous import sh

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix")


_DIR = Path(__file__).parent
_PY = sh(sys.executable).env(PYTHONPATH=_DIR.parents[1])


async def test_example1():
    "Test the example1 program."
    output = await _PY(_DIR / "example1.py")
    assert output == "arbitrary\r\nYou typed: arbitrary\r\n\n"
