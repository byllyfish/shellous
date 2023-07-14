"Unit tests for the Prompt class."

import asyncio
import os
import sys

import pytest

from shellous import cooked, sh
from shellous.prompt import Prompt

_PS1 = ">>> "
_NO_ECHO = cooked(echo=False)


_requires_unix = pytest.mark.skipif(sys.platform == "win32", reason="requires unix")

_requires_pty = pytest.mark.skipif(
    os.environ.get("SHELLOUS_LOOP_TYPE") == "uvloop" or sys.platform == "win32",
    reason="requires pty",
)


@_requires_pty
async def test_prompt_python_pty():
    "Test the prompt class with the Python REPL (PTY)."
    cmd = sh(sys.executable).stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO) as run:
        repl = Prompt(run, _PS1, default_timeout=3.0)

        greeting = await repl.send()
        assert "Python" in greeting

        result = await repl.send("print('abc')")
        assert result == "abc"

        await repl.send("exit()")

    assert run.result().exit_code == 0


async def test_prompt_python_interactive():
    "Test the prompt class with the Python REPL (non-PTY using -i)."
    cmd = (
        sh(sys.executable, "-i").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)
    )

    async with cmd as run:
        repl = Prompt(run, _PS1, default_timeout=3.0)

        greeting = await repl.send()
        assert "Python" in greeting

        result = await repl.send("print('abc')")
        assert result == "abc"

        await repl.send("exit()")

    assert run.result().exit_code == 0


@_requires_pty
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


@_requires_pty
async def test_prompt_python_timeout():
    "Test the prompt class with the Python REPL."
    cmd = sh(sys.executable).stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO) as run:
        repl = Prompt(run, _PS1)

        greeting = await repl.send()
        assert "Python" in greeting

        with pytest.raises(asyncio.TimeoutError):
            await repl.send("import time; time.sleep(1)", timeout=0.1)

        await repl.send("exit()")

    assert run.result().exit_code == 0


@_requires_pty
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


@_requires_unix
async def test_prompt_unix_shell():
    "Test the prompt class with a shell (PTY no echo)."
    cmd = sh("sh").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO).env(PS1="$", TERM="dummy") as run:
        repl = Prompt(run, "$")

        greeting = await repl.send()
        assert greeting == ""

        result = await repl.send("echo 123")
        assert result == "123"

        await repl.send("exit")

    assert run.result().exit_code == 0


@_requires_pty
async def test_prompt_unix_shell_echo():
    "Test the prompt class with a shell (PTY default cooked mode)."
    cmd = sh("sh").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=True).env(PS1="$", TERM="dummy") as run:
        repl = Prompt(run, "$")

        greeting = await repl.send()
        assert greeting == ""

        result = await repl.send("echo 123")
        assert result == "echo 123\n123"

        await repl.send("exit")

    assert run.result().exit_code == 0


@_requires_unix
async def test_prompt_unix_shell_interactive():
    "Test the prompt class with an interactive shell (non-PTY, forced)."
    cmd = sh("sh", "-i").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.env(PS1="$", TERM="dummy") as run:
        repl = Prompt(run, "$")

        greeting = await repl.send()
        assert "job control" in greeting  # expect message about job control

        result = await repl.send("echo 123")
        assert result == "echo 123\n123"

        result = await repl.send("exit")
        assert result == "exit\nexit\n"

    assert run.result().exit_code == 0


async def test_prompt_asyncio_repl():
    "Test the prompt class with the asyncio REPL."

    cmd = (
        sh(sys.executable, "-m", "asyncio")
        .stdin(sh.CAPTURE)
        .stdout(sh.CAPTURE)
        .stderr(sh.STDOUT)
    )

    async with cmd as run:
        repl = Prompt(run, ">>> ")

        greeting = await repl.send()
        assert "asyncio" in greeting

        extra = await repl.send()
        assert "import asyncio" in extra

        result = await repl.send("print('hello')")
        assert result == "hello"

        repl.close()

    assert run.result().exit_code == 0
