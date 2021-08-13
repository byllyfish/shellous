"Shellous cross-platform tests."

import asyncio
import io
import sys

import pytest
from shellous import INHERIT, Result, ResultError, context

pytestmark = pytest.mark.asyncio


def test_debug_mode(event_loop):
    "Tests should be running on a loop with asyncio debug mode set."
    assert event_loop.get_debug()


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
SHELLOUS_EXIT_CODE = int(os.environ.get("SHELLOUS_EXIT_CODE") or 0)
SHELLOUS_EXIT_SLEEP = int(os.environ.get("SHELLOUS_EXIT_SLEEP") or 0)

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
elif SHELLOUS_CMD == "tr":
  data = sys.stdin.buffer.read()
  sys.stdout.buffer.write(data.upper())
else:
  raise NotImplementedError

if SHELLOUS_EXIT_SLEEP:
  sys.stdout.buffer.flush()
  time.sleep(float(SHELLOUS_EXIT_SLEEP))

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


@pytest.fixture
def tr_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="tr").stderr(INHERIT)


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


async def test_tr(tr_cmd):
    result = await tr_cmd().stdin("abc")
    assert result == "ABC"


async def test_pipeline(echo_cmd, cat_cmd, tr_cmd):
    pipe = echo_cmd("xyz") | cat_cmd() | tr_cmd()
    result = await pipe()
    assert result == "XYZ"


async def test_echo_exit_code(echo_cmd):
    options = dict(return_result=True, allowed_exit_codes={7})
    result = await echo_cmd("abc").env(SHELLOUS_EXIT_CODE=7).set(**options)
    assert result.exit_code == 7
    assert result.output == "abc"


@pytest.mark.xfail(reason="FIXME")
async def test_echo_cancel(echo_cmd):
    "When a command is cancelled, we should see partial output."

    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.2)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes="abc",  # FIXME: should be partial output...
        exit_code=-9,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )
    assert exc_info.value.command is cmd


@pytest.mark.xfail(reason="FIXME")
async def test_echo_cancel_stringio(echo_cmd):
    "When a command is cancelled, we should see partial output."

    buf = io.StringIO()
    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2).stdout(buf)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.2)

    assert buf.getvalue() == "abc"
    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=None,
        exit_code=-9,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )
