"Shellous cross-platform tests."

import sys

import pytest
from shellous import INHERIT, context

pytestmark = pytest.mark.asyncio


@pytest.fixture
def python_script():
    """Create a python script that can be used in tests.

    The script behaves like common versions of `echo`, `cat`, `sleep` or `env`
    depending on environment variables.
    """
    sh = context()
    return sh(sys.executable, "-c", _SCRIPT)


# The script behaves differently depending on its environment vars.
_SCRIPT = """
import os
import sys
import time

SHELLOUS_CMD = os.environ.get("SHELLOUS_CMD")
SHELLOUS_EXIT_CODE = os.environ.get("SHELLOUS_EXIT_CODE")

if SHELLOUS_CMD == "echo":
  data = b' '.join(arg.encode("utf-8") for arg in sys.argv[1:])
  sys.stdout.buffer.write(data)
elif SHELLOUS_CMD == "cat":
  data = sys.stdin.buffer.read()
  sys.stdout.buffer.write(data)
elif SHELLOUS_CMD == "sleep":
  time.sleep(float(sys.argv[1]))
elif SHELLOUS_CMD == "env":
  data = b''.join(f"{key}={value}\\n".encode('utf-8') for key, value in os.environ.items())
  sys.stdout.buffer.write(data)
sys.exit(SHELLOUS_EXIT_CODE)
"""


@pytest.fixture
def echo_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="echo").stderr(INHERIT)


@pytest.fixture
def cat_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="cat").stderr(INHERIT)


@pytest.fixture
def sleep_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="sleep").stderr(INHERIT)


@pytest.fixture
def env_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="env").stderr(INHERIT)


async def test_echo(echo_cmd):
    result = await echo_cmd("abc", "def")
    assert result == "abc def"


async def test_cat(cat_cmd):
    result = await cat_cmd().stdin("abc")
    assert result == "abc"


async def test_sleep(sleep_cmd):
    result = await sleep_cmd(0.1)
    assert result == ""


async def test_env(env_cmd):
    result = await env_cmd()
    assert "SHELLOUS_CMD=env\n" in result
