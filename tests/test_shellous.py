"Shellous cross-platform tests."

import asyncio
import hashlib
import io
import sys

import pytest
from shellous import CAPTURE, INHERIT, PipeResult, Result, ResultError, context

pytestmark = pytest.mark.asyncio


# 4MB + 1: Much larger than necessary.
# See https://github.com/python/cpython/blob/main/Lib/test/support/__init__.py
PIPE_MAX_SIZE = 4 * 1024 * 1024 + 1


def test_debug_mode(event_loop):
    "Tests should be running on a loop with asyncio debug mode set."
    assert event_loop.get_debug()


@pytest.fixture
def sh():
    "Create a default shellous context."
    return context()


@pytest.fixture
def python_script(sh):
    """Create a python script that can be used in tests.

    The script behaves like common versions of `echo`, `cat`, `sleep` or `env`
    depending on environment variables.
    """
    return sh(sys.executable, "-c", _SCRIPT).stderr(INHERIT)


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
  if data:
    sys.stdout.buffer.write(data)
elif SHELLOUS_CMD == "sleep":
  time.sleep(float(sys.argv[1]))
elif SHELLOUS_CMD == "env":
  data = b''.join(f"{key}={value}\\n".encode('utf-8') for key, value in os.environ.items())
  if data:
    sys.stdout.buffer.write(data)
elif SHELLOUS_CMD == "tr":
  data = sys.stdin.buffer.read()
  if data:
    sys.stdout.buffer.write(data.upper())
elif SHELLOUS_CMD == "bulk":
  sys.stdout.buffer.write(b"1234"*(1024*1024+1))
else:
  raise NotImplementedError

if SHELLOUS_EXIT_SLEEP:
  sys.stdout.buffer.flush()
  time.sleep(float(SHELLOUS_EXIT_SLEEP))

sys.exit(SHELLOUS_EXIT_CODE)
"""


_CANCELLED_EXIT_CODE = -15 if sys.platform != "win32" else 1


@pytest.fixture
def echo_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="echo")


@pytest.fixture
def cat_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="cat")


@pytest.fixture
def sleep_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="sleep")


@pytest.fixture
def env_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="env")


@pytest.fixture
def tr_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="tr")


@pytest.fixture
def bulk_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="bulk")


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


async def test_bulk(bulk_cmd):
    result = await bulk_cmd().set(encoding=None)
    assert len(result) == 4 * (1024 * 1024 + 1)
    hash = hashlib.sha256(result).hexdigest()
    assert hash == "462d6c497b393d2c9e1584a7b4636592da837ef66cf4ff871dc937f3fe309459"


async def test_pipeline(echo_cmd, cat_cmd, tr_cmd):
    pipe = echo_cmd("xyz") | cat_cmd() | tr_cmd()
    result = await pipe()
    assert result == "XYZ"


async def test_echo_exit_code(echo_cmd):
    options = dict(return_result=True, allowed_exit_codes={7})
    result = await echo_cmd("abc").env(SHELLOUS_EXIT_CODE=7).set(**options)
    assert result.exit_code == 7
    assert result.output == "abc"


async def test_echo_cancel(echo_cmd):
    "When a command is cancelled, we should see partial output."

    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.2)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=None,  # FIXME: Should contain partial output?
        exit_code=_CANCELLED_EXIT_CODE,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


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
        exit_code=_CANCELLED_EXIT_CODE,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_pipe_error_cmd1(echo_cmd, tr_cmd):
    "Test a pipe where the first command fails with an error."

    echo_cmd = echo_cmd("abc").env(SHELLOUS_EXIT_CODE=3)
    tr_cmd = tr_cmd.env(SHELLOUS_EXIT_SLEEP=2)

    with pytest.raises(ResultError) as exc_info:
        await (echo_cmd | tr_cmd)

    assert exc_info.value.result == Result(
        output_bytes=None,
        exit_code=3,
        cancelled=False,
        encoding="utf-8",
        extra=(
            PipeResult(exit_code=3, cancelled=False),
            PipeResult(exit_code=_CANCELLED_EXIT_CODE, cancelled=True),
        ),
    )


async def test_pipe_error_cmd2(echo_cmd, tr_cmd):
    "Test a pipe where the second command fails with an error."

    tr_cmd = tr_cmd.env(SHELLOUS_EXIT_CODE=5)
    with pytest.raises(ResultError) as exc_info:
        await (echo_cmd("abc") | tr_cmd)

    assert exc_info.value.result == Result(
        output_bytes=b"ABC",
        exit_code=5,
        cancelled=False,
        encoding="utf-8",
        extra=(
            PipeResult(exit_code=0, cancelled=False),
            PipeResult(exit_code=5, cancelled=False),
        ),
    )


async def test_redirect_stdout_bytearray(echo_cmd):
    "Test redirecting stdout to bytearray."
    buf = bytearray()
    result = await echo_cmd("abc").stdout(buf)
    assert result is None
    assert buf == b"abc"


async def test_redirect_stdout_bytesio(echo_cmd):
    "Test redirecting stdout to BytesIO."
    buf = io.BytesIO()
    result = await echo_cmd("abc").stdout(buf)
    assert result is None
    assert buf.getvalue() == b"abc"


async def test_redirect_stdout_stringio(echo_cmd):
    "Test redirecting stdout to StringIO."
    buf = io.StringIO()
    result = await echo_cmd("abc").stdout(buf)
    assert result is None
    assert buf.getvalue() == "abc"


async def test_redirect_stdin_bytearray(cat_cmd):
    "Test reading stdin from bytearray."
    buf = bytearray("123", "utf-8")
    result = await cat_cmd().stdin(buf)
    assert result == "123"


async def test_pipe_redirect_stdin_capture(cat_cmd, tr_cmd):
    "Test setting stdin on pipe to CAPTURE without using `async with`."
    cmd = cat_cmd | tr_cmd
    with pytest.raises(ValueError, match="multiple capture requires 'async with'"):
        await cmd.stdin(CAPTURE)


async def test_broken_pipe(sh):
    """Test broken pipe error for large data passed to stdin.

    We expect our process (Python) to fail with a broken pipe because `cmd`
    doesn't read its standard input.
    """
    data = b"b" * PIPE_MAX_SIZE
    cmd = sh(sys.executable, "-c", "pass")

    with pytest.raises(BrokenPipeError):
        await cmd.stdin(data)


async def test_unread_stdin_unreported(sh):
    """Test tiny data passed to stdin of process that doesn't read it."""
    data = b"b" * 64
    cmd = sh(sys.executable, "-c", "pass")

    result = await cmd.stdin(data)
    assert result == ""


async def test_cat_large_data(cat_cmd):
    "Test cat with large data."
    data = "a" * PIPE_MAX_SIZE
    result = await (data | cat_cmd)
    assert result == data


async def test_broken_pipe_in_pipeline(cat_cmd, echo_cmd):
    """Test broken pipe error within a pipeline.

    We expect `cat_cmd` to fail with a broken pipe because `echo`
    doesn't read its standard input.
    """
    data = b"c" * PIPE_MAX_SIZE

    err = bytearray()
    with pytest.raises(ResultError) as exc_info:
        await (data | cat_cmd.stderr(err) | echo_cmd("abc"))

    assert exc_info.value.result == Result(
        output_bytes=b"abc",
        exit_code=1,
        cancelled=False,
        encoding="utf-8",
        extra=(
            PipeResult(exit_code=1, cancelled=False),
            PipeResult(exit_code=0, cancelled=False),
        ),
    )
    assert b"BrokenPipeError: [Errno 32] Broken pipe" in err


async def test_broken_pipe_in_failed_pipeline(cat_cmd, echo_cmd):
    "Test broken pipe error within a pipeline; last command fails."
    data = b"c" * PIPE_MAX_SIZE
    echo = echo_cmd.env(SHELLOUS_EXIT_CODE=7)

    with pytest.raises(ResultError) as exc_info:
        await (data | cat_cmd | echo("abc"))

    result = exc_info.value.result
    assert result.output_bytes == b"abc"
    assert result.exit_code == 7
    assert result.cancelled == False

    # Depending on timing, the `cat_cmd` subcommand can return either
    # _CANCELLED_EXIT_CODE or 1.

    assert result.extra[0] in (
        PipeResult(exit_code=_CANCELLED_EXIT_CODE, cancelled=True),
        PipeResult(exit_code=1, cancelled=True),
    )
    assert result.extra[1] == PipeResult(exit_code=7, cancelled=False)
