"Unit tests for shellous module (Windows)."

import sys

import pytest
from shellous import context

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows")


@pytest.fixture
def sh():
    return context()


@pytest.fixture
def echo(sh):
    return sh("cmd", "/c", "echo")


async def test_python(sh):
    "Test running the python executable."
    result = await sh(sys.executable, "-c", "print('test1')")
    assert result == "test1\r\n"


async def test_echo(echo):
    "Test running the echo command."
    result = await echo("foo")
    assert result == "foo\r\n"


async def test_empty_env(sh):
    "Test running a command with no environment at all."
    with pytest.raises(OSError):
        await sh(sys.executable, "-c", "pass").set(inherit_env=False)


async def test_empty_env_system_root(sh):
    "Test running a command with just SYSTEM_ROOT env var."
    cmd = sh(sys.executable, "-c", "print('test1')")
    result = await cmd.set(inherit_env=False).env(SystemRoot=...)
    assert result == "test1\r\n"


async def test_process_substitution(sh):
    "Test that process substitution raises an error."
    cmd = sh(sys.executable, "-c", "pass")
    with pytest.raises(RuntimeError, match="process substitution not supported"):
        await cmd(cmd())
