"Test example programs that use the Prompt class."

import sys
from pathlib import Path

import pytest

from shellous import sh

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix")


_DIR = Path(__file__).parent
_PY = sh(sys.executable).env(PYTHONPATH=_DIR.parents[1]).set(timeout=10.0)

_EXAMPLE1 = _DIR / "example1.py"


async def test_example1():
    "Test the example1 program."
    output = await _PY(_EXAMPLE1)
    assert output == "arbitrary\r\nYou typed: arbitrary\r\n\n"
