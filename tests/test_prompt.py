"Unit tests for the Prompt class."

import asyncio
import os
import platform
import re
import sys

import pytest

from shellous import cooked, sh
from shellous.prompt import Prompt

# True if we are running on PyPy.
_IS_PYPY = platform.python_implementation() == "PyPy"

# True if we're running on alpine linux.
_IS_ALPINE = os.path.exists("/etc/alpine-release")

# True if we're running on FreeBSD.
_IS_FREEBSD = sys.platform.startswith("freebsd")

# True if we're running on MacOS.
_IS_MACOS = sys.platform == "darwin"

# True if we're in Github Actions.
_IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") is not None

# True if we're using uvloop.
_IS_UVLOOP = os.environ.get("SHELLOUS_LOOP_TYPE") == "uvloop"

# The interactive prompt on PyPY3 is ">>>> ".
if _IS_PYPY:
    _PS1 = ">>>> "
else:
    _PS1 = ">>> "

_NO_ECHO = cooked(echo=False)

_requires_unix = pytest.mark.skipif(sys.platform == "win32", reason="requires unix")

_requires_pty = pytest.mark.skipif(
    _IS_UVLOOP or sys.platform == "win32",
    reason="requires pty",
)


@_requires_pty
async def test_prompt_python_pty():
    "Test the prompt class with the Python REPL (PTY)."
    cmd = sh(sys.executable).stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.set(pty=_NO_ECHO) as run:
        repl = Prompt(
            run,
            default_prompt=_PS1,
            default_timeout=3.0,
        )

        greeting, _ = await repl.expect()
        assert "Python" in greeting

        result = await repl.command("print('abc')")
        if _IS_MACOS and _IS_GITHUB_ACTIONS:
            assert result == "print('abc')\r\nabc\r\n"
        else:
            assert result == "abc\r\n"

        await repl.command("exit()")

    assert run.result().exit_code == 0


async def test_prompt_python_interactive():
    "Test the prompt class with the Python REPL (non-PTY using -i)."
    cmd = (
        sh(sys.executable, "-i").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)
    )

    async with cmd as run:
        repl = Prompt(
            run,
            default_prompt=_PS1,
            default_timeout=3.0,
            normalize_newlines=True,
        )

        greeting, _ = await repl.expect()
        assert "Python" in greeting

        result = await repl.command("print('abc')")
        assert result == "abc\n"

        await repl.command("exit()")

    assert run.result().exit_code == 0


async def test_prompt_python_interactive_ps1():
    "Test the Python REPL but change the prompt to something unique."
    alt_ps1 = "????"
    cmd = (
        sh(sys.executable, "-i").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)
    )

    async with cmd as run:
        repl = Prompt(run, default_prompt=alt_ps1, normalize_newlines=True)

        greeting = await repl.command(f"import sys; sys.ps1='{alt_ps1}'")
        assert _PS1 in greeting

        result = await repl.command("print('def')")
        assert result == "def\n"

        await repl.command("exit()")

    assert run.result().exit_code == 0


async def test_prompt_python_timeout():
    "Test the prompt class with the Python REPL."
    cmd = (
        sh(sys.executable, "-i").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)
    )

    async with cmd as run:
        repl = Prompt(run, default_prompt=_PS1)

        greeting, _ = await repl.expect()
        assert "Python" in greeting

        with pytest.raises(asyncio.TimeoutError):
            await repl.command("import time; time.sleep(1)", timeout=0.1)

        await repl.command("exit()")

    assert run.result().exit_code == 0


async def test_prompt_python_missing_newline():
    "Test the prompt class with the Python REPL."
    cmd = (
        sh(sys.executable, "-i").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)
    )

    async with cmd as run:
        repl = Prompt(run, default_prompt=_PS1, normalize_newlines=True)

        greeting, _ = await repl.expect()
        assert "Python" in greeting

        result = await repl.command("print(3, end='.')")
        assert result == "3."

        await repl.command("exit()")

    assert run.result().exit_code == 0


@_requires_pty
async def test_prompt_unix_shell():
    "Test the prompt class with a shell (PTY no echo)."
    cmd = (
        sh("sh")
        .stdin(sh.CAPTURE)
        .stdout(sh.CAPTURE)
        .stderr(sh.STDOUT)
        .set(pty=_NO_ECHO, inherit_env=False)
    )

    async with cmd.env(PS1="$", TERM="dumb") as run:
        repl = Prompt(run, default_prompt="$")

        greeting, _ = await repl.expect()
        if _IS_FREEBSD:
            # FIXME: FreeBSD is complaining that it can't access tty?
            assert "job control" in greeting
        else:
            assert greeting == ""

        result = await repl.command("echo 123")
        if _IS_FREEBSD:
            # FIXME: I don't understand why FreeBSD is still echoing?
            assert result == "echo 123\r\n123\r\n"
        else:
            assert result == "123\r\n"

        await repl.command("exit")

    assert run.result().exit_code == 0


@_requires_pty
async def test_prompt_unix_shell_echo():
    "Test the prompt class with a shell (PTY default cooked mode)."
    cmd = (
        sh("sh")
        .stdin(sh.CAPTURE)
        .stdout(sh.CAPTURE)
        .stderr(sh.STDOUT)
        .set(pty=True, inherit_env=False)
    )

    async with cmd.env(PS1="$", TERM="dumb") as run:
        repl = Prompt(run, default_prompt="$")

        greeting, _ = await repl.expect()
        if _IS_FREEBSD:
            # FIXME: FreeBSD is complaining that it can't access tty?
            assert "job control" in greeting
        else:
            assert greeting == ""

        result = await repl.command("echo 123")
        if _IS_ALPINE:
            # Alpine is including terminal escape chars.
            assert result == "\x1b[6necho 123\r\n123\r\n"
        else:
            assert result == "echo 123\r\n123\r\n"

        await repl.command("exit")

    assert run.result().exit_code == 0


@_requires_unix
async def test_prompt_unix_shell_interactive():
    "Test the prompt class with an interactive shell (non-PTY, forced)."
    cmd = (
        sh("sh", "-i")
        .stdin(sh.CAPTURE)
        .stdout(sh.CAPTURE)
        .stderr(sh.STDOUT)
        .set(inherit_env=False)
    )

    async with cmd.env(PS1="$", TERM="dumb") as run:
        repl = Prompt(run, default_prompt="$")

        greeting, _ = await repl.expect()
        assert (
            greeting == "" or "job control" in greeting
        )  # expect message about job control (sometimes?)

        result = await repl.command("echo 123")

        if _IS_MACOS and sys.version_info[:2] >= (3, 10) and not _IS_UVLOOP:
            # On MacOS with Python 3.10 or later, the result is 'echo 123\n123'.
            # (Unless we are using uvloop.) Result is "123" on MacOS in
            # Python 3.9, or when using uvloop with later Python versions.
            assert result == "echo 123\n123\n"
        else:
            assert result == "123\n"

        await repl.command("exit")

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
        repl = Prompt(run, default_prompt=">>> ", normalize_newlines=True)

        greeting, _ = await repl.expect()
        assert "asyncio" in greeting

        extra, _ = await repl.expect()
        assert "import asyncio" in extra

        result = await repl.command("print('hello')")
        assert result == "hello\n"

        repl.close()

    assert run.result().exit_code == 0


@_requires_pty
async def test_prompt_unix_eof():
    "Test the prompt class with a shell (PTY default cooked mode)."
    cmd = (
        sh("sh")
        .stdin(sh.CAPTURE)
        .stdout(sh.CAPTURE)
        .stderr(sh.STDOUT)
        .set(pty=True, inherit_env=False)
    )

    async with cmd.env(PS1="> ", TERM="dumb") as run:
        repl = Prompt(run, default_prompt="> ")

        greeting, _ = await repl.expect()
        if _IS_FREEBSD:
            # FIXME: FreeBSD is complaining that it can't access tty?
            assert "job control" in greeting
        else:
            assert greeting == ""

        result = await repl.command("echo 123")
        if _IS_ALPINE:
            # Alpine is including terminal escape chars.
            assert result == "\x1b[6necho 123\r\n123\r\n"
        else:
            assert result == "echo 123\r\n123\r\n"

        repl.close()  # In PTY mode, this sends ^D to close....

    assert run.result().exit_code == 0


def _escape(s: str) -> str:
    "Escape the white space in the string."
    return s.encode("unicode-escape").decode()


async def test_prompt_python_ps1_newline():
    "Test the Python REPL but change the prompt to something that has CR-LF."
    ps1 = "<<\r\n>>"
    ps1_esc = _escape(ps1)
    ps1_normalized = ps1.replace("\r\n", "\n")

    cmd = (
        sh(sys.executable, "-i").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)
    )

    async with cmd as run:
        repl = Prompt(run, default_prompt=ps1_normalized, normalize_newlines=True)
        print(repl._default_prompt)

        greeting = await repl.command(f"import sys; sys.ps1='{ps1_esc}'", timeout=3.0)
        assert _PS1 in greeting

        result = await repl.command("print('def')")
        assert result == "def\n"

        await repl.command("exit()")

    assert run.result().exit_code == 0


async def test_prompt_asyncio_repl_expect():
    "Test the prompt class with the asyncio REPL and the expect() function."
    cmd = (
        sh(sys.executable, "-m", "asyncio")
        .stdin(sh.CAPTURE)
        .stdout(sh.CAPTURE)
        .stderr(sh.STDOUT)
    )

    prompt = re.compile(">>> ")

    async with cmd as run:
        repl = Prompt(run, normalize_newlines=True, default_timeout=3.0)

        greeting, x = await repl.expect(prompt)
        assert "asyncio" in greeting
        assert x[0] == ">>> "

        extra, x = await repl.expect(prompt)
        assert "import asyncio" in extra
        assert x[0] == ">>> "

        await repl.send("print('hello')")
        result, x = await repl.expect(prompt)
        assert result == "hello\n"
        assert x[0] == ">>> "

        repl.close()
        result, x = await repl.expect(prompt)
        assert result == "\nexiting asyncio REPL...\n"
        assert x is None

    assert run.result().exit_code == 0


async def test_prompt_python_ps1_unicode():
    "Test the Python REPL but change the prompt to an Emoji. Use chunk_size=1."
    ps1 = "<<\U0001F603>>"
    ps1_esc = _escape(ps1)

    cmd = (
        sh(sys.executable, "-i").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)
    )

    prompt = re.compile(ps1)

    async with cmd as run:
        repl = Prompt(run, normalize_newlines=True, chunk_size=1)

        await repl.send(f"import sys; sys.ps1='{ps1_esc}'", timeout=3.0)
        greeting, m = await repl.expect(prompt)
        assert _PS1 in greeting
        assert m[0] == ps1

        await repl.send("print('def')")
        result, m = await repl.expect(prompt)
        assert result == "def\n"
        assert m[0] == ps1

        await repl.send("exit()")
        result = await repl.read_all()
        assert result == ""

    assert run.result().exit_code == 0
