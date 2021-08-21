"Unit tests for shellous module (Windows)."

import os
import sys

import pytest
from shellous import context

win32_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows")
pytestmark = [pytest.mark.asyncio, win32_only]


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


async def test_empty_env(sh):
    "Test running a command with just SYSTEM_ROOT env var."
    cmd = sh(sys.executable, "-c", "print('test1')")
    result = await cmd.set(inherit_env=False).env(_filter_env("SYSTEMROOT"))
    assert result == "test1\r\n"


def _filter_env(*vars):
    "Return environment with just the selected variables present."
    return {key: value for key, value in os.environ.items() if key.upper() in vars}
