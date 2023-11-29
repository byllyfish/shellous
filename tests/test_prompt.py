"Unit tests for the Prompt class."

import asyncio
import contextlib
import os
import platform
import re
import sys

import pytest

from shellous import cooked, sh

from .test_shellous import PIPE_MAX_SIZE, bulk_cmd, python_script

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

# Alpine is including some terminal escapes.
_TERM_ESCAPES = "\x1b[6n" if _IS_ALPINE else ""

_requires_unix = pytest.mark.skipif(sys.platform == "win32", reason="requires unix")

_requires_pty = pytest.mark.skipif(
    _IS_UVLOOP or sys.platform == "win32",
    reason="requires pty",
)


@_requires_pty
async def test_prompt_python_pty():
    "Test the prompt class with the Python REPL (PTY)."
    cmd = sh(sys.executable).stderr(sh.STDOUT).set(pty=_NO_ECHO)

    async with cmd.prompt(_PS1, timeout=3.0) as repl:
        greeting, _ = await repl.expect()
        assert "Python" in greeting
        assert repl.pending == ""

        result = await repl.command("print('abc')")
        if _IS_MACOS and _IS_GITHUB_ACTIONS:
            assert result == "print('abc')\r\nabc\r\n"
        else:
            assert result == "abc\r\n"

        await repl.command("exit()")
        assert repl.at_eof

    assert repl.result.exit_code == 0


async def test_prompt_python_interactive():
    "Test the prompt class with the Python REPL (non-PTY using -i)."
    cmd = sh(sys.executable, "-i").stderr(sh.STDOUT)

    async with cmd.prompt(_PS1, timeout=3.0, normalize_newlines=True) as repl:
        greeting, _ = await repl.expect()
        assert "Python" in greeting
        assert repl.pending == ""

        result = await repl.command("print('abc')")
        assert result == "abc\n"

        await repl.command("exit()")
        assert repl.at_eof

    assert repl.result.exit_code == 0


async def test_prompt_python_interactive_ps1():
    "Test the Python REPL but change the prompt to something unique."
    alt_ps1 = "????"
    cmd = sh(sys.executable, "-i").stderr(sh.STDOUT)

    async with cmd.prompt(alt_ps1, normalize_newlines=True) as repl:
        greeting = await repl.command(f"import sys; sys.ps1='{alt_ps1}'")
        assert _PS1 in greeting
        assert repl.pending == ""

        result = await repl.command("print('def')")
        assert result == "def\n"

        await repl.command("exit()")
        assert repl.at_eof

    assert repl.result.exit_code == 0


async def test_prompt_python_timeout():
    "Test the prompt class with the Python REPL."
    cmd = sh(sys.executable, "-i").stderr(sh.STDOUT)

    # Adjust the Python prompt because PyPy3 and Python3 have different prompts.
    ps1_alt = ">>> |"

    async with cmd.prompt(ps1_alt, timeout=3.0, normalize_newlines=True) as repl:
        greeting = await repl.command(f"import sys; sys.ps1='{ps1_alt}'")
        assert "Python" in greeting
        assert repl.pending == ""

        # Send a command, but don't wait long enough.
        with pytest.raises(asyncio.TimeoutError):
            await repl.command("import time; time.sleep(1)", timeout=0.2)

        # Wait for actual prompt to return.
        await repl.expect()

        # Send a command and wait for a never prompt.
        with pytest.raises(asyncio.TimeoutError):
            await repl.command("print('hello')", prompt="xxxxx", timeout=0.2)

        # Check the contents of the `pending` buffer.
        assert repl.pending == "hello\n>>> |"

        # Show we can still run expect() on the new buffer.
        found, m = await repl.expect(re.compile("l.*?>", re.DOTALL))
        assert found == "he"
        assert m and m[0] == "llo\n>"
        assert repl.pending == ">> |"

        await repl.command("exit()")
        assert repl.at_eof

    assert repl.result.exit_code == 0


async def test_prompt_python_missing_newline():
    "Test the prompt class with the Python REPL."
    cmd = sh(sys.executable, "-i").stderr(sh.STDOUT)

    async with cmd.prompt(_PS1, normalize_newlines=True) as repl:
        greeting, _ = await repl.expect()
        assert "Python" in greeting
        assert repl.pending == ""

        result = await repl.command("print(3, end='.')")
        assert result == "3."

        await repl.command("exit()")
        assert repl.at_eof

    assert repl.result.exit_code == 0


@_requires_pty
async def test_prompt_unix_shell():
    "Test the prompt class with a shell (PTY no echo)."
    cmd = sh("sh").stderr(sh.STDOUT).set(pty=_NO_ECHO, inherit_env=False)

    async with cmd.env(PS1="$", TERM="dumb").prompt("$") as repl:
        greeting, _ = await repl.expect()
        if _IS_FREEBSD:
            # FIXME: FreeBSD is complaining that it can't access tty?
            assert "job control" in greeting
        else:
            assert greeting == ""
        assert repl.pending == ""

        result = await repl.command("echo 123")
        if _IS_FREEBSD:
            # FIXME: I don't understand why FreeBSD is still echoing?
            assert result == "echo 123\r\n123\r\n"
        else:
            assert result == "123\r\n"

        await repl.command("exit")
        assert repl.at_eof

    assert repl.result.exit_code == 0


@_requires_pty
async def test_prompt_unix_shell_echo():
    "Test the prompt class with a shell (PTY default cooked mode)."
    cmd = sh("sh").stderr(sh.STDOUT).set(pty=True, inherit_env=False)

    async with cmd.env(PS1="$", TERM="dumb").prompt("$") as repl:
        greeting, _ = await repl.expect()
        if _IS_FREEBSD:
            # FIXME: FreeBSD is complaining that it can't access tty?
            assert "job control" in greeting
        else:
            assert greeting == ""

        # Alpine is including terminal escape chars.
        assert repl.pending == f"{_TERM_ESCAPES}"

        result = await repl.command("echo 123")
        assert result == f"{_TERM_ESCAPES}echo 123\r\n123\r\n"

        await repl.command("exit")
        assert repl.at_eof

    assert repl.result.exit_code == 0


@_requires_unix
async def test_prompt_unix_shell_interactive():
    "Test the prompt class with an interactive shell (non-PTY, forced)."
    cmd = sh("sh", "-i").stderr(sh.STDOUT).set(inherit_env=False)

    async with cmd.env(PS1="$", TERM="dumb").prompt("$") as repl:
        greeting, _ = await repl.expect()
        assert (
            greeting == "" or "job control" in greeting
        )  # expect message about job control (sometimes?)
        assert repl.pending == ""

        result = await repl.command("echo 123")

        if _IS_MACOS and sys.version_info[:2] >= (3, 10) and not _IS_UVLOOP:
            # On MacOS with Python 3.10 or later, the result is 'echo 123\n123'.
            # (Unless we are using uvloop.) Result is "123" on MacOS in
            # Python 3.9, or when using uvloop with later Python versions.
            assert result == "echo 123\n123\n"
        else:
            assert result == "123\n"

        await repl.command("exit")
        assert repl.at_eof

    assert repl.result.exit_code == 0


async def test_prompt_asyncio_repl():
    "Test the prompt class with the asyncio REPL."
    cmd = sh(sys.executable, "-m", "asyncio").stderr(sh.STDOUT)

    async with cmd.prompt(">>> ", normalize_newlines=True) as repl:
        greeting, _ = await repl.expect()
        assert "asyncio" in greeting

        extra, _ = await repl.expect()
        assert "import asyncio" in extra

        result = await repl.command("print('hello')")
        assert result == "hello\n"

    assert repl.result.exit_code == 0


@_requires_pty
async def test_prompt_unix_eof():
    "Test the prompt class with a shell (PTY default cooked mode)."
    cmd = sh("sh").stderr(sh.STDOUT).set(pty=True, inherit_env=False)

    async with cmd.env(PS1="> ", TERM="dumb").prompt("> ") as repl:
        greeting, _ = await repl.expect()
        if _IS_FREEBSD:
            # FIXME: FreeBSD is complaining that it can't access tty?
            assert "job control" in greeting
        else:
            assert greeting == ""

        # Alpine is including terminal escape chars.
        assert repl.pending == f"{_TERM_ESCAPES}"

        result = await repl.command("echo 123")
        assert result == f"{_TERM_ESCAPES}echo 123\r\n123\r\n"

    assert repl.result.exit_code == 0


def _escape(s: str) -> str:
    "Escape the white space in the string."
    return s.encode("unicode-escape").decode()


async def test_prompt_python_ps1_newline():
    "Test the Python REPL but change the prompt to something that has CR-LF."
    ps1 = "<<\r\n>>"
    ps1_esc = _escape(ps1)
    ps1_normalized = ps1.replace("\r\n", "\n")

    cmd = sh(sys.executable, "-i").stderr(sh.STDOUT)

    async with cmd.prompt(ps1_normalized, normalize_newlines=True) as repl:
        greeting = await repl.command(f"import sys; sys.ps1='{ps1_esc}'", timeout=3.0)
        assert _PS1 in greeting
        assert repl.pending == ""

        result = await repl.command("print('def')")
        assert result == "def\n"

        await repl.command("exit()")
        assert repl.at_eof

    assert repl.result.exit_code == 0


async def test_prompt_asyncio_repl_expect():
    "Test the prompt class with the asyncio REPL and the expect() function."
    cmd = sh(sys.executable, "-m", "asyncio").stderr(sh.STDOUT)

    prompt = re.compile(">>> ")

    async with cmd.prompt(normalize_newlines=True, timeout=3.0) as repl:
        greeting, x = await repl.expect(prompt)
        assert "asyncio" in greeting
        assert x and x[0] == ">>> "
        # There will likely be data in `repl.pending`.

        extra, x = await repl.expect(prompt)
        assert "import asyncio" in extra
        assert x and x[0] == ">>> "
        assert repl.pending == ""

        await repl.send("print('hello')")
        result, x = await repl.expect(prompt)
        assert result == "hello\n"
        assert x and x[0] == ">>> "

        repl.close()
        result, x = await repl.expect(prompt)
        assert result == "\nexiting asyncio REPL...\n"
        assert x is None
        assert repl.at_eof

    assert repl.result.exit_code == 0


async def test_prompt_python_ps1_unicode():
    "Test the Python REPL but change the prompt to an Emoji. Use chunk_size=1."
    ps1 = "<<\U0001F603>>"

    cmd = sh(sys.executable, "-i").stderr(sh.STDOUT)

    async with cmd.prompt(ps1, normalize_newlines=True, chunk_size=1) as repl:
        await repl.send(f"import sys; sys.ps1='{_escape(ps1)}'", timeout=3.0)
        assert repl.pending == ""

        greeting, m = await repl.expect()
        assert _PS1 in greeting
        assert m and m[0] == ps1

        await repl.send("print('def')")
        result, m = await repl.expect()
        assert result == "def\n"
        assert m and m[0] == ps1

        await repl.send("exit()")
        result = await repl.read_all()
        assert result == ""
        assert repl.at_eof

    assert repl.result.exit_code == 0


async def test_prompt_deadlock_antipattern(bulk_cmd):
    """Use the prompt context manager but don't read from stdout.

    Similar to test_shellous.py `test_stdout_deadlock_antipattern`
    """

    async def _antipattern():
        async with bulk_cmd.set(timeout=3.0).prompt() as cli:
            # ... and we don't read from stdout at all.
            pass

    with pytest.raises(asyncio.TimeoutError):
        # The _antipattern function must time out.
        await _antipattern()


async def test_prompt_broken_pipe():
    """Test broken pipe error for large data passed to stdin.

    We expect our process (Python) to fail with a broken pipe because `cmd`
    doesn't read its standard input.
    """
    cmd = sh(sys.executable, "-c", "pass")

    with pytest.raises(BrokenPipeError):
        async with cmd.prompt() as cli:
            with contextlib.suppress(ConnectionResetError):
                await cli.send("a" * PIPE_MAX_SIZE)


async def test_prompt_grep():
    "Test the prompt context manager with a large grep send/expect."
    cmd = sh("grep", "--line-buffered", "b").set(timeout=8.0)

    async with cmd.prompt() as cli:
        await cli.send("a" * PIPE_MAX_SIZE + "b")
        _, m = await cli.expect("b")
        assert cli.pending == "\n"
        assert m.start() == 4194305


async def test_prompt_grep_read_during_send():
    "Test the prompt context manager with a large grep send/expect."
    cmd = sh("grep", "--line-buffered", "b").set(timeout=8.0)

    async with cmd.prompt() as cli:
        await cli.send("a" * PIPE_MAX_SIZE + "b")
        await cli.send("a" * PIPE_MAX_SIZE + "b")
        await cli.expect("b")
        # If we try to stop here, there is still data unread in the pipe. The
        # grep process will not exit until we read it all. See
        # `test_prompt_grep_unread_data`.
        await cli.expect("b")
        assert cli.pending == "\n"


async def test_prompt_grep_unread_data():
    "Test the prompt context manager with a large grep send/expect."
    cmd = sh("grep", "--line-buffered", "b").set(timeout=8.0)

    with pytest.raises(asyncio.TimeoutError):
        async with cmd.prompt() as cli:
            await cli.send("a" * PIPE_MAX_SIZE + "b")
            await cli.send("a" * PIPE_MAX_SIZE + "b")
            await cli.expect("b")
            # Note: There is still unread data in the pipe. The process will
            # not exit until we read it all. At this time, the `Prompt`
            # __aexit__ method does not implement a read_all() so we get a
            # timeout waiting for the process to exit.


@_requires_pty
async def test_prompt_grep_pty():
    "Test the prompt context manager with grep send/expect (PTY)."
    cmd = sh("grep", "b").set(timeout=8.0, pty=True)

    async with cmd.prompt() as cli:
        cli.echo = False

        # Determine MAX_CANON for the PTY.
        fd = cli.runner.pty_fd
        assert fd is not None
        if _IS_FREEBSD or _IS_MACOS:
            max_canon = os.fpathconf(fd, "PC_MAX_CANON")
        else:
            max_canon = 4096
        print(f"using max_canon = {max_canon}")

        await cli.send("a" * (max_canon - 2) + "b")
        _, m = await cli.expect(re.compile(r"b\r?\r\n"))  # MACOS: extra '\r' after 'b'?
        assert m.start() == max_canon - 2
        assert cli.pending == ""

        await cli.send("a" * (max_canon - 1) + "b")
        try:
            _, m = await cli.expect("\x07", timeout=2.0)  # \x07 is BEL
        except asyncio.TimeoutError:
            # Linux may not return anything; IMAXBEL is disabled.
            assert cli.pending == ""

        await cli.send(b"\x15", end="")  # \x15 is VKILL
        await cli.send("aaab")
        await cli.expect("b")


async def test_prompt_grep_pending():
    "Test the prompt context manager with grep and a \\Z to read pending data."
    cmd = sh("grep", "--line-buffered", "b").set(timeout=8.0)

    async with cmd.prompt() as cli:
        # Send first line and receive part of the response.
        await cli.send("abcdef")
        data, m = await cli.expect("c")
        assert data == "ab"
        assert m[0] == "c"

        # There is still pending data.
        assert cli.pending == "def\n"

        # Now send the second line.
        await cli.send("ghbijk")

        # Match end of pending buffer. This will not read any more data.
        data, m = await cli.expect(re.compile(r"\Z"))
        assert data == "def\n"
        assert m[0] == ""
        assert cli.pending == ""

        # Now read the rest of the 2nd response.
        data, m = await cli.expect("\n")
        assert data == "ghbijk"
        assert m[0] == "\n"
