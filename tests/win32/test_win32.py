"Unit tests for shellous module (Windows)."

import sys

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


@pytest.mark.skipif(sys.version_info[0:2] >= (3, 12), reason="3.12b3 change")
async def test_empty_env():
    "Test running a command with no environment at all."
    with pytest.raises(OSError):
        await sh(sys.executable, "-c", "pass").set(inherit_env=False)


async def test_empty_env_system_root():
    "Test running a command with just SYSTEM_ROOT env var."
    cmd = sh(sys.executable, "-c", "print('test1')")
    result = await cmd.set(inherit_env=False).env(SystemRoot=...)
    assert result == "test1\r\n"


async def test_process_substitution():
    "Test that process substitution raises an error."
    cmd = sh(sys.executable, "-c", "pass")
    with pytest.raises(RuntimeError, match="process substitution not supported"):
        await cmd(cmd())
