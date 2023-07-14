"Unit tests for the Prompt class."

import sys

import pytest

from shellous import cooked, sh
from shellous.prompt import Prompt

_PS1 = ">>> "
_NO_ECHO = cooked(echo=False)


async def test_prompt_python():
    "Test the prompt class with the Python REPL."
    cmd = sh(sys.executable).stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO) as run:
        repl = Prompt(run, _PS1, default_timeout=3.0)

        greeting = await repl.send()
        assert "Python" in greeting

        result = await repl.send("print('abc')")
        assert result == "abc"

        await repl.send("exit()")

    assert run.result().exit_code == 0


async def test_prompt_python_ps1():
    "Test the Python REPL but change the prompt to something unique."
    alt_ps1 = "????"
    cmd = sh(sys.executable).stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO) as run:
        repl = Prompt(run, alt_ps1)

        greeting = await repl.send(f"import sys; sys.ps1='{alt_ps1}'")
        assert _PS1 in greeting

        result = await repl.send("print('def')")
        assert result == "def"

        await repl.send("exit()")

    assert run.result().exit_code == 0


async def test_prompt_python_timeout():
    "Test the prompt class with the Python REPL."
    cmd = sh(sys.executable).stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO) as run:
        repl = Prompt(run, _PS1)

        greeting = await repl.send()
        assert "Python" in greeting

        with pytest.raises(TimeoutError):
            await repl.send("import time; time.sleep(1)", timeout=0.1)

        await repl.send("exit()")

    assert run.result().exit_code == 0


async def test_prompt_python_missing_newline():
    "Test the prompt class with the Python REPL."
    cmd = sh(sys.executable).stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO) as run:
        repl = Prompt(run, _PS1)

        greeting = await repl.send()
        assert "Python" in greeting

        result = await repl.send("print(3, end='.')")
        assert result == "3."

        await repl.send("exit()")

    assert run.result().exit_code == 0


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
async def test_prompt_unix_shell():
    "Test the prompt class with a shell."
    cmd = sh("sh").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO).env(PS1="$", TERM="dummy") as run:
        repl = Prompt(run, "$")

        greeting = await repl.send()
        assert greeting == ""

        result = await repl.send("echo 123")
        assert result == "123"

        await repl.send("exit")

    assert run.result().exit_code == 0
