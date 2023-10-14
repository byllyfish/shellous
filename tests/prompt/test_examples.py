import sys
from pathlib import Path

import pytest

from shellous import sh

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix")


_DIR = Path(__file__).parent
_PY = sh(sys.executable).env(PYTHONPATH=_DIR.parents[1]).set(timeout=10.0)


async def test_example1():
    "Test the example1 program."
    err = bytearray()

    result = await _PY.result(_DIR / "example1.py").env(SHELLOUS_PROMPT=1).stderr(err)
    if err:
        print(err.decode())

    assert result
    assert result.output == "arbitrary\r\nYou typed: arbitrary\r\n\n"
