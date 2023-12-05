"Test example programs that use the Prompt class."

import os
import sys
from pathlib import Path

import pytest

from shellous import sh

# True if we're using uvloop.
_IS_UVLOOP = os.environ.get("SHELLOUS_LOOP_TYPE") == "uvloop"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or _IS_UVLOOP, reason="requires pty"
)

_DIR = Path(__file__).parent
_PY = sh(sys.executable).env(PYTHONPATH=_DIR.parents[1]).set(timeout=10.0)

_EXAMPLE1 = _DIR / "example1.py"
_EXAMPLE2 = _DIR / "example2.py"
_EXAMPLE3 = _DIR / "example3.py"


async def test_example1():
    "Test the example1 program."
    output = await _PY(_EXAMPLE1)
    assert output == "arbitrary\r\nYou typed: arbitrary\r\n\n"


async def test_example2():
    "Test the example2 program."
    output = await _PY(_EXAMPLE2)
    assert output == "arbitrary\r\nYou typed: arbitrary\r\n\n"


async def test_example3():
    "Test the example3 program."
    output = await _PY(_EXAMPLE3)
    assert output == "arbitrary\r\nYou typed: arbitrary\r\n\n"


async def test_no_echo():
    "Test the prompt's no_echo function (for code coverage)."
    cmd = sh(_DIR / "fake_prompter.sh").set(pty=True)

    async with cmd.prompt("prompt> ") as cli:
        assert cli.echo

        await cli.expect("Name: ")
        await cli.command("name", prompt="Password: ")
        await cli.command("abc123", no_echo=True)

        cli.echo = False
        output = await cli.command("some thing")
        assert output == "You typed: some thing\r\n"


async def test_fail():
    "Test the prompt when the program fails."
    cmd = sh(_DIR / "fake_prompter.sh").set(pty=True)

    async with cmd.result("--fail").prompt() as cli:
        try:
            await cli.expect("*NOTHING*")
        except EOFError:
            assert cli.pending == "Failure requested.\r\n"

    assert not bool(cli.result)
    assert cli.result is not None and cli.result.exit_code == 1
