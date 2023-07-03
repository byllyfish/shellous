"Unit tests for shellous module (Windows)."

import sys
from pathlib import Path

import pytest

from shellous import sh

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows")


@pytest.fixture
def echo():
    return sh("cmd", "/c", "echo")


async def test_python():
    "Test running the python executable."
    result = await sh(sys.executable, "-c", "print('test1')")
    assert result == "test1\r\n"


async def test_echo(echo):
    "Test running the echo command."
    result = await echo("foo")
    assert result == "foo\r\n"


async def test_empty_env():
    "Test running python with no environment at all."
    cmd = sh(sys.executable, "-c", "print('test2')")

    if sys.version_info >= (3, 12, 0, "beta", 3):
        # In CPython 3.12.0b3, gh-10546 fixed the behavior of this test.
        result = await cmd.set(inherit_env=False)
        assert result == "test2\r\n"

    else:  # CPython 3.12.0b2 and earlier...
        # The longstanding behavior on Windows was to raise an OSError when
        # creating a process with an empty environment.
        with pytest.raises(OSError):
            await cmd.set(inherit_env=False)


async def test_empty_env_echo(echo):
    "Test running echo command with no environment at all."
    if sys.version_info >= (3, 12, 0, "beta", 3):
        # In CPython 3.12.0b3, gh-10546 fixed the behavior of this test.
        result = await echo("test3").set(inherit_env=False)
        assert result == "test3\r\n"

    else:  # CPython 3.12.0b2 and earlier...
        # The longstanding behavior on Windows was to raise an OSError when
        # creating a process with an empty environment.
        with pytest.raises(OSError):
            await echo("test3").set(inherit_env=False)


async def test_empty_env_system_root():
    "Test running a command with just SYSTEM_ROOT env var."
    cmd = sh(sys.executable, "-c", "print('test1')")
    result = await cmd.set(inherit_env=False).env(SystemRoot=...)
    assert result == "test1\r\n"


def test_context_find_command():
    "Test the context's `find_command` method."
    assert sh.options.path is None
    assert sh.find_command("cmd") == Path("C:/Windows/system32/cmd.EXE")
    assert sh.find_command("there_is_no_foo_command") is None


def test_context_find_command_path():
    "Test the context's `find_command` method with a custom path configured."
    sh1 = sh.set(path="C:/does_not_exist")
    assert sh1.options.path == "C:/does_not_exist"
    assert sh1.find_command("cmd") is None


async def test_command_as_path():
    "Test running a command using the result of find_command (a Path object)."
    cmd = sh.find_command("cmd")
    result = await sh(cmd, "/c", "echo", "abc")
    assert result == "abc\r\n"


async def test_process_substitution():
    "Test that process substitution raises an error."
    cmd = sh(sys.executable, "-c", "pass")
    with pytest.raises(RuntimeError, match="process substitution not supported"):
        await cmd(cmd())
