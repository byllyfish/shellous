"Shellous cross-platform tests."

# pylint: disable=redefined-outer-name,invalid-name

import asyncio
import hashlib
import io
import sys
from pathlib import Path

import pytest
from shellous import CAPTURE, DEVNULL, INHERIT, PipeResult, Result, ResultError, context
from shellous.harvest import harvest_results

pytestmark = pytest.mark.asyncio

# 4MB + 1: Much larger than necessary.
# See https://github.com/python/cpython/blob/main/Lib/test/support/__init__.py
PIPE_MAX_SIZE = 4 * 1024 * 1024 + 1

# On Windows, the exit_code of a terminated process is 1.
CANCELLED_EXIT_CODE = -15 if sys.platform != "win32" else 1


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
    source_file = Path("tests/python_script.py")
    return sh(sys.executable, source_file).stderr(INHERIT)


@pytest.fixture
def echo_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="echo").set(alt_name="echo")


@pytest.fixture
def cat_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="cat").set(alt_name="cat")


@pytest.fixture
def sleep_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="sleep").set(alt_name="sleep")


@pytest.fixture
def env_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="env").set(alt_name="env")


@pytest.fixture
def tr_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="tr").set(alt_name="tr")


@pytest.fixture
def bulk_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="bulk").set(alt_name="bulk")


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
    options = dict(return_result=True, exit_codes={7})
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
        exit_code=CANCELLED_EXIT_CODE,
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
        exit_code=CANCELLED_EXIT_CODE,
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
            PipeResult(exit_code=CANCELLED_EXIT_CODE, cancelled=True),
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
    assert result == ""
    assert buf == b"abc"


async def test_redirect_stdout_bytesio(echo_cmd):
    "Test redirecting stdout to BytesIO."
    buf = io.BytesIO()
    result = await echo_cmd("abc").stdout(buf)
    assert result == ""
    assert buf.getvalue() == b"abc"


async def test_redirect_stdout_stringio(echo_cmd):
    "Test redirecting stdout to StringIO."
    buf = io.StringIO()
    result = await echo_cmd("abc").stdout(buf)
    assert result == ""
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

    with pytest.raises(BrokenPipeError):
        await (data | cat_cmd | echo_cmd("abc"))


@pytest.mark.xfail(True, reason="latent bug")
async def test_broken_pipe_in_failed_pipeline(cat_cmd, echo_cmd):
    "Test broken pipe error within a pipeline; last command fails."
    data = b"c" * PIPE_MAX_SIZE
    echo = echo_cmd.env(SHELLOUS_EXIT_CODE=7)

    with pytest.raises(BrokenPipeError):
        await (data | cat_cmd | echo("abc"))


async def test_broken_pipe_in_async_with_failed_pipeline(cat_cmd, echo_cmd):
    "Test broken pipe error within a pipeline; last command fails."
    data = b"c" * PIPE_MAX_SIZE
    echo = echo_cmd.env(SHELLOUS_EXIT_CODE=7)

    cmd = (data | cat_cmd | echo("abc")).stdin(CAPTURE).stdout(DEVNULL)
    async with cmd.run() as run:
        run.stdin.write(data)
        try:
            await run.stdin.drain()
        except BrokenPipeError:
            pass
        finally:
            run.stdin.close()

    with pytest.raises(BrokenPipeError):
        # Must retrieve BrokenPipeError from the `_stdin_closed` future.
        run.stdin.close()  # redundant close here
        await run.stdin.wait_closed()


async def test_stdout_deadlock_antipattern(bulk_cmd):
    "Use async-with but don't read from stdout."

    async def _antipattern():
        async with bulk_cmd.run() as run:
            assert run.stdin is None
            assert run.stderr is None
            assert run.stdout
            # ... and we don't read from stdout at all.

    with pytest.raises(asyncio.TimeoutError):
        # The _antipattern function must time out.
        await asyncio.wait_for(_antipattern(), 3.0)


async def test_runner_enter(echo_cmd):
    "Test cancellation behavior in Runner.__aenter__."

    async def test_task():
        async with echo_cmd.run() as run:
            pass
        return run.result()

    task = asyncio.create_task(test_task())
    await asyncio.sleep(0)
    task.cancel()

    # FIXME: At what point, should Runner raise a ResultError?
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_encoding_utf8_strict(cat_cmd):
    "Test use of encoding option with bad utf-8 data."

    cat = cat_cmd.set(encoding="utf-8 strict")
    with pytest.raises(UnicodeDecodeError, match="invalid start byte"):
        await (b"\x81abc" | cat)


async def test_encoding_utf8_replace(cat_cmd):
    "Test use of encoding option with bad utf-8 data."

    cat = cat_cmd.set(encoding="utf-8 replace")
    result = await (b"\x81abc" | cat)
    assert result == "\ufffdabc"


async def test_many_short_programs_sequential(echo_cmd):
    "Test many short programs (sequential)."
    COUNT = 10

    failure_count = 0
    for i in range(COUNT):
        result = await echo_cmd("abc")
        if result != "abc":
            failure_count += 1

    assert failure_count == 0


async def test_many_short_programs_parallel(echo_cmd):
    "Test many short programs (parallel)."
    COUNT = 10

    cmds = [echo_cmd("abcd") for i in range(COUNT)]
    results = await harvest_results(*cmds)

    assert results == ["abcd"] * COUNT


async def test_redirect_stdin_capture_iter(cat_cmd, tr_cmd):
    "Test setting stdin to CAPTURE when using `async for`."
    with pytest.raises(
        RuntimeError,
        match="multiple capture not supported in iterator",
    ):
        async with cat_cmd.stdin(CAPTURE).run() as run:
            async for line in run:
                pass


async def test_pipe_redirect_stdin_capture_iter(cat_cmd, tr_cmd):
    "Test setting stdin on pipe to CAPTURE when using `async for`."
    cmd = cat_cmd | tr_cmd
    with pytest.raises(
        RuntimeError, match="multiple capture not supported in iterator"
    ):
        async with cmd.stdin(CAPTURE).run() as run:
            async for line in run:
                pass


async def test_pipe_immediate_cancel(cat_cmd, tr_cmd):
    "Test running a pipe that is immediately cancelled."
    cmd = cat_cmd | tr_cmd
    task = cmd.task()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        # FIXME: Should raise ResultError.
        await task


async def test_breaking_out_of_async_iter(env_cmd):
    "Test breaking out of an async iterator."
    async with env_cmd.run() as run:
        async for _ in run:
            break
    # report_orphan_tasks


async def test_exception_in_async_iter(env_cmd):
    "Test breaking out of an async iterator."
    with pytest.raises(ValueError):
        async with env_cmd.run() as run:
            async for _ in run:
                raise ValueError(1)
    # report_orphan_tasks


async def test_pipe_breaking_out_of_async_iter(env_cmd, tr_cmd):
    "Test breaking out of an async iterator."
    cmd = env_cmd | tr_cmd

    async with cmd.run() as run:
        async for _ in run:
            break
    # report_orphan_tasks


async def test_pipe_exception_in_async_iter(env_cmd, tr_cmd):
    "Test breaking out of an async iterator."
    cmd = env_cmd | tr_cmd

    with pytest.raises(ValueError):
        async with cmd.run() as run:
            async for _ in run:
                raise ValueError(1)
    # report_orphan_tasks
