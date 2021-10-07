"Shellous cross-platform tests."

# pylint: disable=redefined-outer-name,invalid-name

import asyncio
import hashlib
import io
import logging
import os
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
KILL_EXIT_CODE = -9 if sys.platform != "win32" else 1
UNLAUNCHED_EXIT_CODE = -255


def _is_uvloop():
    "Return true if we're running under uvloop."
    return os.environ.get("SHELLOUS_LOOP_TYPE") == "uvloop"


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


async def test_nonexistant_cmd():
    sh = context()
    with pytest.raises(FileNotFoundError):
        await sh("non_existant_command").set(return_result=True)


async def test_nonexecutable_cmd():
    sh = context()
    if sys.platform == "win32":
        with pytest.raises(OSError, match="not a valid Win32 application"):
            await sh("./README.md").set(return_result=True)
    else:
        with pytest.raises(PermissionError):
            await sh("./README.md").set(return_result=True)


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
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(cmd, timeout=0.2)


async def test_echo_cancel_incomplete(echo_cmd):
    "When a command is cancelled, we should see partial output."

    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2).set(incomplete_result=True)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.2)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=b"abc",
        exit_code=CANCELLED_EXIT_CODE,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_echo_cancel_stringio(echo_cmd):
    "When a command is cancelled, we should see partial output."

    buf = io.StringIO()
    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2).stdout(buf)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(cmd, timeout=0.2)

    assert buf.getvalue() == "abc"


async def test_echo_cancel_stringio_incomplete(echo_cmd):
    "When a command is cancelled, we should see partial output."

    buf = io.StringIO()
    cmd = (
        echo_cmd("abc")
        .env(SHELLOUS_EXIT_SLEEP=2)
        .stdout(buf)
        .set(incomplete_result=True)
    )
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.2)

    assert buf.getvalue() == "abc"
    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=b"",
        exit_code=CANCELLED_EXIT_CODE,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_pipe_cancel(echo_cmd, tr_cmd):
    "When a pipe is cancelled, we should see partial output."
    echo_cmd = echo_cmd("abc")
    tr_cmd = tr_cmd.env(SHELLOUS_EXIT_SLEEP=2)

    cmd = echo_cmd | tr_cmd
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(cmd, timeout=0.2)


async def test_pipe_cancel_incomplete(echo_cmd, cat_cmd):
    "When a pipe is cancelled, we should see partial output."
    echo_cmd = echo_cmd("abc")
    cat_cmd = cat_cmd.env(SHELLOUS_EXIT_SLEEP=2).set(incomplete_result=True)

    cmd = echo_cmd | cat_cmd
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.4)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=b"abc",
        exit_code=0,
        cancelled=True,
        encoding="utf-8",
        extra=(
            PipeResult(exit_code=0, cancelled=False),
            PipeResult(exit_code=CANCELLED_EXIT_CODE, cancelled=True),
        ),
    )


async def test_pipe_error_cmd1(echo_cmd, tr_cmd):
    "Test a pipe where the first command fails with an error."

    echo_cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=1, SHELLOUS_EXIT_CODE=3)
    tr_cmd = tr_cmd.env(SHELLOUS_EXIT_SLEEP=2)

    with pytest.raises(ResultError) as exc_info:
        await (echo_cmd | tr_cmd)

    # This test has a race condition; sometimes we get partial output and
    # sometimes we don't.

    assert exc_info.value.result in (
        Result(
            output_bytes=b"ABC",
            exit_code=3,
            cancelled=False,
            encoding="utf-8",
            extra=(
                PipeResult(exit_code=3, cancelled=False),
                PipeResult(exit_code=CANCELLED_EXIT_CODE, cancelled=True),
            ),
        ),
        Result(
            output_bytes=b"",  # only difference
            exit_code=3,
            cancelled=False,
            encoding="utf-8",
            extra=(
                PipeResult(exit_code=3, cancelled=False),
                PipeResult(exit_code=CANCELLED_EXIT_CODE, cancelled=True),
            ),
        ),
    )


async def test_pipe_error_cmd2(echo_cmd, tr_cmd):
    "Test a pipe where the second command fails with an error."

    tr_cmd = tr_cmd.env(SHELLOUS_EXIT_CODE=5)
    with pytest.raises(ResultError) as exc_info:
        await (echo_cmd("abc") | tr_cmd)

    # This test has a race condition; sometimes the first command is cancelled.

    assert exc_info.value.result in (
        Result(
            output_bytes=b"ABC",
            exit_code=5,
            cancelled=False,
            encoding="utf-8",
            extra=(
                PipeResult(exit_code=0, cancelled=False),
                PipeResult(exit_code=5, cancelled=False),
            ),
        ),
        Result(
            output_bytes=b"ABC",
            exit_code=5,
            cancelled=False,
            encoding="utf-8",
            extra=(
                PipeResult(exit_code=0, cancelled=True),  # only difference
                PipeResult(exit_code=5, cancelled=False),
            ),
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


async def test_redirect_stdout_logger(echo_cmd, caplog):
    "Test redirecting stdout to a Logger."
    logger = logging.getLogger("test_logger")
    result = await echo_cmd("abc %r\ndef %s").stdout(logger)
    assert result == ""

    logs = [tup for tup in caplog.record_tuples if tup[0] == "test_logger"]
    assert logs == [
        ("test_logger", 40, "abc %r"),
        ("test_logger", 40, "def %s"),
    ]


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


async def test_broken_pipe_in_failed_pipeline(cat_cmd, echo_cmd):
    "Test broken pipe error within a pipeline; last command fails."
    data = b"c" * PIPE_MAX_SIZE
    echo = echo_cmd.env(SHELLOUS_EXIT_CODE=7)

    # This test has a race condition. Sometimes we get a BrokenPipeError, and
    # sometimes both commands fail before shellous detects the broken pipe.

    with pytest.raises((BrokenPipeError, ResultError)) as exc_info:
        await (data | cat_cmd | echo("abc"))

    if exc_info.type == ResultError:
        assert exc_info.value.result == Result(
            output_bytes=b"abc",
            exit_code=7,
            cancelled=False,
            encoding="utf-8",
            extra=(
                PipeResult(exit_code=120, cancelled=True),
                PipeResult(exit_code=7, cancelled=False),
            ),
        )


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


async def test_encoding_utf8(cat_cmd):
    "Test use of encoding option with bad utf-8 data."

    cat = cat_cmd.set(encoding="utf-8")
    with pytest.raises(UnicodeDecodeError, match="invalid start byte"):
        await (b"\x81abc" | cat)


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


async def test_encoding_utf8_replace_str(cat_cmd):
    "Test use of encoding option with utf-8 data."

    cat = cat_cmd.set(encoding="utf-8 replace")
    result = await ("abc" | cat)
    assert result == "abc"


async def test_encoding_ascii_str(cat_cmd):
    "Test use of encoding option with ascii data."

    cat = cat_cmd.set(encoding="ascii")
    result = await ("abc" | cat)
    assert result == "abc"


async def test_encoding_utf8_split(cat_cmd):
    "Test reconstitution of split utf-8 chars in output."

    buf = io.StringIO()
    cmd = CAPTURE | cat_cmd | buf

    async with cmd.run() as run:
        # Not split.
        run.stdin.write(b"\xf0\x9f\x90\x9f")  # "\U0001F41F" in utf-8
        await run.stdin.drain()
        await asyncio.sleep(0.2)

        # Split in half.
        run.stdin.write(b"\xf0\x9f")
        await run.stdin.drain()
        await asyncio.sleep(0.1)

        run.stdin.write(b"\x90\x9f")
        await run.stdin.drain()
        await asyncio.sleep(0.1)

        # Split up one byte at a time.
        for item in b"\xf0\x9f\x90\x9f":
            run.stdin.write(bytes([item]))
            await run.stdin.drain()
            await asyncio.sleep(0.1)

        run.stdin.close()

    assert buf.getvalue() == "\U0001F41F" * 3


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
    cancelled, results = await harvest_results(*cmds)

    assert not cancelled
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
    task = asyncio.create_task(cmd.coro())
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


async def test_pipe_with_exception_in_middle(env_cmd, tr_cmd):
    "Test pipe context manager failing with exception."
    cmd = env_cmd | tr_cmd

    with pytest.raises(ValueError):
        async with cmd.run():
            raise ValueError(1)
    # report_orphan_tasks


async def test_process_substitution(echo_cmd, cat_cmd):
    "Test process substitution."
    cmd = cat_cmd(echo_cmd("abc"))

    if sys.platform == "win32":
        with pytest.raises(
            RuntimeError,
            match="process substitution not supported on Windows",
        ):
            await cmd

    else:
        result = await cmd
        assert result == "abc"


async def test_async_iter_with_bytes_encoding(cat_cmd):
    "Test async iteration with encoding=None."

    cmd = b"a\nb\nc\nd" | cat_cmd.set(encoding=None)

    async with cmd.run() as run:
        lines = [line async for line in run]

    assert lines == [b"a\n", b"b\n", b"c\n", b"d"]


async def test_stringio_redirect_with_bytes_encoding(echo_cmd):
    "Can't use StringIO redirect output buffer with encoding=None."

    buf = io.StringIO()
    cmd = echo_cmd | buf

    with pytest.raises(TypeError, match="StringIO"):
        await cmd("abc").set(encoding=None)


async def test_quick_cancel(echo_cmd):
    "Test a command that is quickly cancelled, just before starting."

    async def _test_task():
        # Cancel in Runner._subprocess_exec().
        asyncio.current_task().cancel()
        return await echo_cmd("hello").set(incomplete_result=True)

    task = asyncio.create_task(_test_task())

    with pytest.raises(ResultError) as exc_info:
        await task

    assert exc_info.value.result == Result(
        output_bytes=b"",
        exit_code=UNLAUNCHED_EXIT_CODE,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


@pytest.mark.skipif(sys.platform == "win32" or _is_uvloop(), reason="win32,uvloop")
async def test_pty_echo_exit_code(echo_cmd):
    "Test exit code is reported correctly for pty."
    options = dict(return_result=True, exit_codes={7}, pty=True)
    result = await echo_cmd("abc").env(SHELLOUS_EXIT_CODE=7).set(**options)

    assert result.exit_code == 7
    assert result.output == "abc"
