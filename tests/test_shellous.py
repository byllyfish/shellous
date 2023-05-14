"Shellous cross-platform tests."

# pylint: disable=redefined-outer-name,invalid-name

import asyncio
import hashlib
import io
import logging
import os
import sys
from pathlib import Path

import asyncstdlib as asl
import pytest

from shellous import Result, ResultError, sh
from shellous.harvest import harvest_results

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


def _attach_loop():
    "Make sure the current child watcher is attached to a loop, if necessary."
    if sys.platform != "win32":
        cw = asyncio.get_child_watcher()
        cw.attach_loop(asyncio.get_running_loop())


def test_debug_mode(event_loop):
    "Tests should be running on a loop with asyncio debug mode set."
    assert event_loop.get_debug()


@pytest.fixture
def python_script():
    """Create a python script that can be used in tests.

    The script behaves like common versions of `echo`, `cat`, `sleep` or `env`
    depending on environment variables.
    """
    source_file = Path("tests/python_script.py")
    return sh(sys.executable, "-u", source_file).stderr(sh.INHERIT)


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


@pytest.fixture
def count_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="count").set(alt_name="count")


@pytest.fixture
def error_cmd(python_script):
    return python_script.env(SHELLOUS_CMD="error").set(alt_name="error")


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
    result = await bulk_cmd().set(encoding="latin1")
    assert len(result) == 4 * (1024 * 1024 + 1)
    value = hashlib.sha256(result.encode("latin1")).hexdigest()
    assert value == "462d6c497b393d2c9e1584a7b4636592da837ef66cf4ff871dc937f3fe309459"


async def test_count(count_cmd):
    result = await count_cmd(5)
    assert result == "1\n2\n3\n4\n5\n"


async def test_error(error_cmd):
    result = await error_cmd.stderr(sh.BUFFER).result

    assert result.exit_code == 0
    assert result.output_bytes == b""
    assert result.error_bytes == b"1" * 1024
    assert result.output == ""
    assert result.error == "1" * 1024


async def test_error_bulk(error_cmd):
    # sh.BUFFER is used to override stderr(sh.INHERIT).
    result = await error_cmd("1").env(SHELLOUS_EXIT_CODE="13").stderr(sh.BUFFER).result

    assert result.exit_code == 13
    assert result.output_bytes == b""
    assert result.error_bytes == b"1" * 1024
    assert result.output == ""
    assert result.error == "1" * 1024


async def test_error_result():
    result = await sh.result(sys.executable, "-c", "print('hello')")
    assert result.exit_code == 0
    assert result.output.rstrip() == "hello"
    assert result.error == ""


async def test_error_only():
    "Test standard error output only (STDOUT + DEVNULL)."
    cmd = (
        sh(
            sys.executable,
            "-c",
            "import sys; print('xyz'); print('abc', file=sys.stderr)",
        )
        .stdout(sh.DEVNULL)
        .stderr(sh.STDOUT)
    )

    result = await cmd.result()
    assert result.output.rstrip() == "abc"
    assert result.error == ""

    async with cmd as run:
        assert run.stdin is None
        assert run.stdout is None
        assert run.stderr is None

    res = run.result()
    assert res.output.rstrip() == "abc"
    assert res.error == ""

    out = [line async for line in cmd]
    assert len(out) == 1
    assert out[0].rstrip() == "abc"


async def test_nonexistant_cmd():
    with pytest.raises(FileNotFoundError):
        await sh("non_existant_command").set(_return_result=True)


async def test_nonexecutable_cmd():
    if sys.platform == "win32":
        with pytest.raises(OSError, match="not a valid Win32 application"):
            await sh("./README.md").set(_return_result=True)
    else:
        with pytest.raises(PermissionError):
            await sh("./README.md").set(_return_result=True)


async def test_pipeline(echo_cmd, cat_cmd, tr_cmd):
    pipe = echo_cmd("xyz") | cat_cmd() | tr_cmd()
    result = await pipe()
    assert result == "XYZ"


async def test_echo_exit_code(echo_cmd):
    options = dict(_return_result=True, exit_codes={7})
    result = await echo_cmd("abc").env(SHELLOUS_EXIT_CODE=7).set(**options)
    assert result.exit_code == 7
    assert result.output == "abc"


async def test_echo_cancel(echo_cmd):
    "When a command is cancelled, we should see partial output."
    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(cmd, timeout=0.75)


async def test_echo_cancel_incomplete(echo_cmd):
    "When a command is cancelled, we should see partial output."
    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2).set(_catch_cancelled_error=True)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.75)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        exit_code=CANCELLED_EXIT_CODE,
        output_bytes=b"abc",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
    )


async def test_echo_cancel_stringio(echo_cmd):
    "When a command is cancelled, we should see partial output."
    buf = io.StringIO()
    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2).stdout(buf)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(cmd, timeout=0.75)

    assert buf.getvalue() == "abc"


async def test_echo_cancel_stringio_incomplete(echo_cmd):
    "When a command is cancelled, we should see partial output."
    buf = io.StringIO()
    cmd = (
        echo_cmd("abc")
        .env(SHELLOUS_EXIT_SLEEP=2)
        .stdout(buf)
        .set(_catch_cancelled_error=True)
    )
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.75)

    assert buf.getvalue() == "abc"
    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        exit_code=CANCELLED_EXIT_CODE,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
    )


async def test_echo_result(echo_cmd):
    "Test the .result modifier with a single command."
    echo = echo_cmd.env(SHELLOUS_EXIT_CODE=17)

    result1 = await echo("def").result
    assert result1.exit_code == 17
    assert result1.output == "def"
    assert not result1

    result2 = await echo.result("xyz")  # preferred syntax
    assert result2.exit_code == 17
    assert result2.output == "xyz"
    assert not result2


async def test_pipe_result_1(echo_cmd, tr_cmd):
    "Test the .result modifier with a pipe."
    echo = echo_cmd.env(SHELLOUS_EXIT_SLEEP=0.5, SHELLOUS_EXIT_CODE=17)
    tr = tr_cmd

    pipe = echo("abc") | tr
    result = await pipe.result
    assert result.exit_code == 17
    assert result.output in ("ABC", "")  # race condition in test
    assert not result


async def test_pipe_result_2(echo_cmd, tr_cmd):
    "Test the .result modifier with a pipe."
    echo = echo_cmd
    tr = tr_cmd.env(SHELLOUS_EXIT_CODE=18)

    pipe = echo("abc") | tr
    result = await pipe.result
    assert result.exit_code == 18
    assert result.output == "ABC"
    assert not result


async def test_pipe_result_3(echo_cmd, tr_cmd):
    "Test the .result modifier with a pipe."
    echo = echo_cmd.env(SHELLOUS_EXIT_SLEEP=0.5, SHELLOUS_EXIT_CODE=9)
    tr = tr_cmd.env(SHELLOUS_EXIT_CODE=18)

    pipe = echo("abc") | tr
    result = await pipe.result
    assert result.exit_code == 9
    assert result.output in ("ABC", "")  # race condition in test
    assert not result


async def test_pipe_cancel(echo_cmd, tr_cmd):
    "When a pipe is cancelled, we should see partial output."
    echo_cmd = echo_cmd("abc")
    tr_cmd = tr_cmd.env(SHELLOUS_EXIT_SLEEP=2)

    cmd = echo_cmd | tr_cmd
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(cmd, timeout=0.75)


async def test_pipe_cancel_incomplete(echo_cmd, cat_cmd):
    "When a pipe is cancelled, we should see partial output."
    echo_cmd = echo_cmd("abc")
    cat_cmd = cat_cmd.env(SHELLOUS_EXIT_SLEEP=2).set(_catch_cancelled_error=True)

    cmd = echo_cmd | cat_cmd
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, timeout=0.75)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        exit_code=0,
        output_bytes=b"abc",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
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
            exit_code=3,
            output_bytes=b"ABC",
            error_bytes=b"",
            cancelled=False,
            encoding="utf-8",
        ),
        Result(
            exit_code=3,
            output_bytes=b"",  # only difference
            error_bytes=b"",
            cancelled=False,
            encoding="utf-8",
        ),
    )


async def test_pipe_error_cmd2(echo_cmd, tr_cmd):
    "Test a pipe where the second command fails with an error."
    tr_cmd = tr_cmd.env(SHELLOUS_EXIT_CODE=5)
    with pytest.raises(ResultError) as exc_info:
        await (echo_cmd("abc") | tr_cmd)

    # This test has a race condition; sometimes the first command is cancelled.

    assert exc_info.value.result == Result(
        exit_code=5,
        output_bytes=b"ABC",
        error_bytes=b"",
        cancelled=False,
        encoding="utf-8",
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


async def test_redirect_stdout_result(echo_cmd):
    "Test redirecting stdout to RESULT."
    result = await echo_cmd("abc").stdout(sh.BUFFER)
    assert result == "abc"


async def test_redirect_stdin_bytearray(cat_cmd):
    "Test reading stdin from bytearray."
    buf = bytearray("123", "utf-8")
    result = await cat_cmd().stdin(buf)
    assert result == "123"


async def test_redirect_stdin_bytesio(cat_cmd):
    "Test reading stdin from BytesIO."
    buf = io.BytesIO(b"123")
    result = await cat_cmd().stdin(buf)
    assert result == "123"


async def test_redirect_stdin_stringio(cat_cmd):
    "Test reading stdin from StringIO."
    buf = io.StringIO("123")
    result = await cat_cmd().stdin(buf)
    assert result == "123"


async def test_redirect_stdin_stringio_no_encoding(cat_cmd):
    "Test reading stdin from StringIO with encoding=None"
    buf = io.StringIO("123")
    with pytest.raises(TypeError, match="invalid encoding"):
        await cat_cmd().stdin(buf).set(encoding=None)


async def test_redirect_stdin_inherit(echo_cmd):
    "Test reading stdin from INHERIT."
    try:
        result = await echo_cmd("abc").stdin(sh.INHERIT)
        assert result == "abc"
    except io.UnsupportedOperation:
        # Raises UnsupportedOperation under code coverage.
        pass


async def test_redirect_stdin_result(echo_cmd):
    "Test reading stdin from RESULT."
    with pytest.raises(TypeError, match="unsupported input type"):
        await echo_cmd("abc").stdin(sh.BUFFER)


async def test_redirect_stdin_unsupported_type(cat_cmd):
    "Test reading stdin from unsupported type."
    with pytest.raises(TypeError, match="unsupported input type"):
        await cat_cmd("abc").stdin(1 + 2j)


async def test_broken_pipe():
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

    if isinstance(exc_info.value, ResultError):
        assert exc_info.value.result == Result(
            exit_code=7,
            output_bytes=b"abc",
            error_bytes=b"",
            cancelled=False,
            encoding="utf-8",
        )


async def test_broken_pipe_in_async_with_failed_pipeline(cat_cmd, echo_cmd):
    "Test broken pipe error within a pipeline; last command fails."
    data = b"c" * PIPE_MAX_SIZE
    echo = echo_cmd.env(SHELLOUS_EXIT_CODE=7)

    cmd = (data | cat_cmd | echo("abc")).stdin(sh.CAPTURE).stdout(sh.DEVNULL)
    async with cmd as run:
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
        async with bulk_cmd.set(timeout=3.0).stdout(sh.CAPTURE) as run:
            assert run.stdin is None
            assert run.stderr is None
            assert run.stdout
            # ... and we don't read from stdout at all.

    with pytest.raises(asyncio.TimeoutError):
        # The _antipattern function must time out.
        await _antipattern()


async def test_runner_enter(echo_cmd):
    "Test cancellation behavior in Runner.__aenter__."

    async def test_task():
        async with echo_cmd as run:
            pass
        return run.result()

    task = asyncio.create_task(test_task())
    await asyncio.sleep(0)
    task.cancel()

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
    cmd = sh.CAPTURE | cat_cmd | buf

    async with cmd as run:
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
    for _ in range(COUNT):
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


async def test_redirect_stderr_capture_iter(cat_cmd):
    "Test setting stderr to CAPTURE when using `async for`."
    with pytest.raises(
        RuntimeError,
        match="multiple capture not supported in iterator",
    ):
        async for _ in cat_cmd.stderr(sh.CAPTURE):
            pass


async def test_pipe_redirect_stderr_capture_iter(cat_cmd, tr_cmd):
    "Test setting stderr on pipe to CAPTURE when using `async for`."
    cmd = cat_cmd | tr_cmd
    with pytest.raises(
        RuntimeError, match="multiple capture not supported in iterator"
    ):
        async for _ in cmd.stderr(sh.CAPTURE):
            pass


async def test_pipe_immediate_cancel(cat_cmd, tr_cmd):
    "Test running a pipe that is immediately cancelled."
    cmd = cat_cmd | tr_cmd
    task = asyncio.create_task(cmd.coro())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_breaking_out_of_async_iter(env_cmd):
    "Test breaking out of an async iterator."
    async with env_cmd as run:
        async for _ in run:
            break
    # report_orphan_tasks


async def test_exception_in_async_iter(env_cmd):
    "Test breaking out of an async iterator."
    with pytest.raises(ValueError):
        async with env_cmd.stdout(sh.CAPTURE) as run:
            async for _ in run:
                raise ValueError(1)
    # report_orphan_tasks


async def test_pipe_breaking_out_of_async_iter(env_cmd, tr_cmd):
    "Test breaking out of an async iterator."
    cmd = env_cmd | tr_cmd

    async with cmd as run:
        async for _ in run:
            break
    # report_orphan_tasks


async def test_pipe_exception_in_async_iter(env_cmd, tr_cmd):
    "Test breaking out of an async iterator."
    cmd = env_cmd | tr_cmd

    with pytest.raises(ValueError):
        async with cmd.stdout(sh.CAPTURE) as run:
            async for _ in run:
                raise ValueError(1)
    # report_orphan_tasks


async def test_pipe_with_exception_in_middle(env_cmd, tr_cmd):
    "Test pipe context manager failing with exception."
    cmd = env_cmd | tr_cmd

    with pytest.raises(ValueError):
        async with cmd:
            raise ValueError(1)
    # report_orphan_tasks


async def test_process_substitution(echo_cmd, cat_cmd):
    "Test process substitution."
    cmd = cat_cmd(echo_cmd("abc"))

    if sys.platform == "win32":
        with pytest.raises(
            RuntimeError,
            match="process substitution not supported",
        ):
            await cmd

    else:
        result = await cmd
        assert result == "abc"


async def test_async_iter_with_latin1_encoding(cat_cmd):
    "Test async iteration with encoding=None."
    cmd = b"a\nb\nc\nd" | cat_cmd.set(encoding="latin1") | sh.CAPTURE
    async with cmd as run:
        lines = [line async for line in run]

    assert lines == ["a\n", "b\n", "c\n", "d"]


async def test_stringio_redirect_with_bytes_encoding(echo_cmd):
    "Can't use StringIO redirect output buffer with encoding=None."
    buf = io.StringIO()
    cmd = echo_cmd | buf

    with pytest.raises(TypeError, match="invalid encoding"):
        await cmd("abc").set(encoding=None)


async def test_quick_cancel(echo_cmd):
    "Test a command that is quickly cancelled, just before starting."

    async def _test_task():
        # Cancel in Runner._subprocess_exec().
        current_task = asyncio.current_task()
        assert current_task is not None
        current_task.cancel()
        return await echo_cmd("hello").set(_catch_cancelled_error=True)

    task = asyncio.create_task(_test_task())

    with pytest.raises(ResultError) as exc_info:
        await task

    assert exc_info.value.result == Result(
        exit_code=UNLAUNCHED_EXIT_CODE,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
    )


@pytest.mark.skipif(sys.platform == "win32" or _is_uvloop(), reason="win32,uvloop")
async def test_pty_echo_exit_code(echo_cmd):
    "Test exit code is reported correctly for pty."
    options = dict(_return_result=True, exit_codes={7}, pty=True)
    result = await echo_cmd("abc").env(SHELLOUS_EXIT_CODE=7).set(**options)

    assert result.exit_code == 7
    assert result.output == "abc"


async def test_redirect_to_arbitrary_tuple():
    "Test redirection to an arbitrary tuple."
    with pytest.raises(TypeError, match="unsupported"):
        await sh("echo").stdout((1, 2))  # type: ignore

    with pytest.raises(TypeError, match="unsupported"):
        await (sh("echo") | (1, 2))  # type: ignore


async def test_command_context_manager_default():
    "Test running a command using its context manager."
    async with sh("echo", "hello") as run:
        # By default, context manager does not capture any streams.
        assert run.stdin is None
        assert run.stdout is None
        assert run.stderr is None

    assert run.result().output == "hello\n"


async def test_command_context_manager_api():
    "Test running a command using its context manager."
    async with sh("echo", "hello").stdout(sh.CAPTURE) as run:
        assert run.stdout is not None
        out = await run.stdout.read()

    assert out == b"hello\n"


async def test_command_context_manager_api_reentrant():
    "Test running a command using its context manager."
    cmd = sh("echo", "hello").stdout(sh.CAPTURE)
    async with cmd as run1:
        assert run1.stdout is not None
        out1 = await run1.stdout.read()

        # Re-enter context manager here for exact same command.
        async with cmd as run2:
            assert run2.stdout is not None
            out2 = await run2.stdout.read()

    assert out1 == out2 == b"hello\n"


async def test_pipe_context_manager_api():
    "Test running a pipeline using its context manager."
    async with sh("echo", "hello") | sh("cat").stdout(sh.CAPTURE) as run:
        assert run.stdout is not None
        out = await run.stdout.read()

    assert out == b"hello\n"


async def test_pipe_context_manager_api_reentrant():
    "Test running a pipeline using its context manager."
    cmd = sh("echo", "hello") | sh("cat").stdout(sh.CAPTURE)
    async with cmd as run1:
        assert run1.stdout is not None
        out1 = await run1.stdout.read()

        # Re-enter context manager here for exact same command.
        async with cmd as run2:
            assert run2.stdout is not None
            out2 = await run2.stdout.read()

    assert out1 == out2 == b"hello\n"


async def test_command_iterator_api(echo_cmd):
    "Test running a command's async iterator directly."
    lines = [line.rstrip() async for line in echo_cmd("hello\n", "world")]
    assert lines == ["hello", " world"]

    # When the async iterator runs to completion, there is no problem with
    # extra tasks hanging around.
    assert len(asyncio.all_tasks()) == 1


async def test_command_iterator_api_interrupted(echo_cmd):
    "Test running a command's async iterator directly."

    async def _test():
        async for line in echo_cmd("hello\n", "cruel\n", "world\n"):
            if "hello" in line:
                return True
        return False

    assert await _test()

    # An async iterator was interrupted. At this point, there *may* still be
    # tasks running related to the command invocation in _test. The tasks will
    # be cleaned up when the `GeneratorExit` exception is propagated.

    # We still need to wait for the process to asynchronously exit...
    await asyncio.sleep(0.5)


def test_command_iterator_api_interrupted_sync(echo_cmd):
    "Test running a command's async iterator directly."

    async def _test():
        _attach_loop()

        async for line in echo_cmd("hello\n", "cruel\n", "world\n"):
            if "hello" in line:
                return True
        return False

    # asyncio.run() should do all clean up for interrupted async iterator.
    result = asyncio.run(_test())
    assert result


async def test_pipe_iterator_api(echo_cmd, cat_cmd):
    "Test running a command's async iterator directly."
    cmd = echo_cmd("hello\n", "world") | cat_cmd()
    lines = [line.rstrip() async for line in cmd]
    assert lines == ["hello", " world"]

    # When the async iterator runs to completion, there is no problem with
    # extra tasks hanging around.
    assert len(asyncio.all_tasks()) == 1


async def test_pipe_iterator_api_interrupted(echo_cmd, cat_cmd):
    "Test running a command's async iterator directly."

    async def _test():
        cmd = echo_cmd("hello\n", "cruel\n", "world\n") | cat_cmd()
        async for line in cmd:
            if "hello" in line:
                return True
        return False

    assert await _test()

    # An async iterator was interrupted. At this point, there are still
    # tasks running related to the command invocation in _test. The tasks will
    # be cleaned up when the `GeneratorExit` exception is propagated.
    assert len(asyncio.all_tasks()) > 1
    await asyncio.sleep(0)

    # We still need to wait for the process to asynchrously exit...
    await asyncio.sleep(0.1)


def test_pipe_iterator_api_interrupted_sync(echo_cmd, cat_cmd):
    "Test running a command's async iterator directly."
    # Due to bpo-?, asyncio.run may log an ERROR message for a task even
    # though the task's exception has been explicitly consumed:
    #
    #  "ERROR asyncio unhandled exception during asyncio.run() shutdown"
    #
    # To temporarily prevent this, set a custom exception handler on the loop
    # to change it to a WARNING message.

    IGNORED_MSG = "unhandled exception during asyncio.run() shutdown"

    def _custom_exception_handler(loop, context):
        task = context.get("task")
        message = context.get("message")
        if task and "echo|cat#" in task.get_name() and IGNORED_MSG in message:
            logging.getLogger(__name__).warning(
                "Ignore %r from %r",
                message,
                task,
            )
        else:
            loop.default_exception_handler(context)

    async def _test():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_custom_exception_handler)
        _attach_loop()

        cmd = echo_cmd("hello\n", "cruel\n", "world\n") | cat_cmd()
        async for line in cmd:
            if "hello" in line:
                return True
        return False

    # asyncio.run() should do all clean up for interrupted async iterator.
    result = asyncio.run(_test())
    assert result


async def test_audit_callback(echo_cmd):
    "Test the audit callback hook."
    calls = []

    def _audit(phase, info):
        runner = info["runner"]
        failure = info["failure"] or None
        calls.append((phase, runner.name, runner.returncode, failure))

    echo = echo_cmd.set(audit_callback=_audit)

    result = await echo("hello")
    assert result == "hello"
    assert calls == [
        ("start", "echo", None, None),
        ("stop", "echo", 0, None),
    ]


async def test_audit_callback_launch_failure():
    "Test the audit callback hook with a failure-to-launch error."
    calls = []

    def _audit(phase, info):
        runner = info["runner"]
        failure = info["failure"] or None
        assert not info["signal"]
        calls.append((phase, runner.name, runner.pid, runner.returncode, failure))

    malformed = sh("__does_not_exist__").set(audit_callback=_audit)

    with pytest.raises(FileNotFoundError):
        await malformed("hello")

    assert calls == [
        (
            "start",
            "__does_not_exist__",
            None,
            None,
            None,
        ),
        (
            "stop",
            "__does_not_exist__",
            None,
            None,
            "FileNotFoundError",
        ),
    ]


async def test_audit_pipe_cancel(echo_cmd, tr_cmd):
    "Test audit callback when a pipe is cancelled."
    calls = []

    def _audit(phase, info):
        runner = info["runner"]
        signal = info["signal"]
        calls.append((phase, runner.name, runner.returncode, signal))

    echo_cmd = echo_cmd("abc").set(audit_callback=_audit)
    tr_cmd = tr_cmd.env(SHELLOUS_EXIT_SLEEP=2).set(audit_callback=_audit)

    cmd = echo_cmd | tr_cmd
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(cmd, timeout=0.75)

    assert calls == [
        ("start", "echo", None, ""),
        ("start", "tr", None, ""),
        ("stop", "echo", 0, ""),
        ("signal", "tr", None, "SIGTERM"),
        ("stop", "tr", CANCELLED_EXIT_CODE, ""),
    ]


async def test_multiple_pipe(echo_cmd, cat_cmd):
    "Test a pipeline of 7 commands."
    cat = cat_cmd
    cmd = echo_cmd("xyz") | cat | cat | cat | cat | cat | cat

    result = await cmd
    assert result == "xyz"


async def test_command_with_timeout_expiring(sleep_cmd):
    "Test a command with a timeout option."
    with pytest.raises(asyncio.TimeoutError):
        await sleep_cmd(10).set(timeout=0.1)


async def test_command_with_timeout_ignored(sleep_cmd):
    "Test a command with a timeout option."
    result = await sleep_cmd(0.1).set(timeout=1.0)
    assert result == ""


async def test_command_with_timeout_expiring_context(sleep_cmd):
    "Test a command with a timeout option."
    sleep = sleep_cmd(10).set(timeout=0.1).stdout(sh.CAPTURE)

    with pytest.raises(asyncio.TimeoutError):
        async with sleep as run:
            await run.stdout.read()
            assert False  # never reached


async def test_command_with_timeout_expiring_generator(sleep_cmd):
    "Test a command with a timeout option."
    sleep = sleep_cmd(10).set(timeout=0.1)

    with pytest.raises(asyncio.TimeoutError):
        async for _ in sleep:
            assert False  # never reached


async def test_command_with_timeout_result(sleep_cmd):
    "Test a command with both timeout and .result modifiers."
    sleep = sleep_cmd(10).result

    with pytest.raises(asyncio.TimeoutError):
        await sleep(10).set(timeout=0.1)


async def test_command_with_timeout_incomplete_result(sleep_cmd):
    "Test a command with both timeout and .result modifiers."
    sleep = sleep_cmd(10).result.set(_catch_cancelled_error=True)

    result = await sleep(10).set(timeout=0.1)

    assert result == Result(
        exit_code=CANCELLED_EXIT_CODE,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
    )


async def test_command_with_timeout_incomplete_resulterror(sleep_cmd):
    "Test a command with both timeout and catch_cancelled_error modifiers."
    sleep = sleep_cmd(10).set(_catch_cancelled_error=True)

    with pytest.raises(ResultError) as exc_info:
        await sleep(10).set(timeout=0.1)

    assert exc_info.value.result == Result(
        exit_code=CANCELLED_EXIT_CODE,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
    )


async def test_wait_for_zero_seconds(sleep_cmd):
    "Test asyncio.wait_for(0) with a timeout of zero seconds."
    calls = []

    def _audit(phase, info):
        runner = info["runner"]
        failure = info.get("failure")
        calls.append((phase, runner.name, runner.returncode, failure))

    sleep = sleep_cmd(10).set(audit_callback=_audit)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sleep(10), 0.0)

    # There are no start/stop audit calls when the timeout expires before
    # launching the process.
    assert not calls


async def test_timeout_zero_seconds(sleep_cmd):
    "Test command with a timeout of zero seconds."
    calls = []

    def _audit(phase, info):
        runner = info["runner"]
        calls.append((phase, runner.name, runner.returncode))

    sleep = sleep_cmd(10).set(audit_callback=_audit, timeout=0.0)

    with pytest.raises(asyncio.TimeoutError):
        await sleep(10)

    # The `timeout` timer starts as soon as the process is started. Contrast
    # this with asyncio.wait_for().
    assert calls == [
        ("start", "sleep", None),
        ("signal", "sleep", None),
        ("stop", "sleep", CANCELLED_EXIT_CODE),
    ]


async def test_timeout_negative_seconds(sleep_cmd):
    "Test command with a negative timeout (treated the same as 0 seconds)."
    calls = []

    def _audit(phase, info):
        runner = info["runner"]
        calls.append((phase, runner.name, runner.returncode))

    sleep = sleep_cmd(10).set(audit_callback=_audit, timeout=-1.0)

    with pytest.raises(asyncio.TimeoutError):
        await sleep(10)

    # The `timeout` timer starts as soon as the process is started. Contrast
    # this with asyncio.wait_for().
    assert calls == [
        ("start", "sleep", None),
        ("signal", "sleep", None),
        ("stop", "sleep", CANCELLED_EXIT_CODE),
    ]


async def test_command_timeout_incomplete_result(echo_cmd):
    "Test timeout option in combination with catch_cancelled_error option."
    cmd = (
        echo_cmd("abc")
        .env(SHELLOUS_EXIT_SLEEP=2)
        .set(_catch_cancelled_error=True, timeout=0.4)
    )
    with pytest.raises(ResultError) as exc_info:
        await cmd

    assert exc_info.value.result == Result(
        exit_code=CANCELLED_EXIT_CODE,
        output_bytes=b"abc",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
    )


async def test_command_timeout_incomplete_result_exit_code(echo_cmd):
    "Test timeout, catch_cancelled_error, and exit_codes option."
    # Test timeout alone.
    cmd = echo_cmd("abc").env(SHELLOUS_EXIT_SLEEP=2).set(timeout=0.4)
    with pytest.raises(asyncio.TimeoutError):
        await cmd

    # Test timeout and catch_cancelled_error. Setting `catch_cancelled_error` gives
    # us a ResultError with the partial result.
    cmd = cmd.set(_catch_cancelled_error=True)
    with pytest.raises(ResultError) as exc_info:
        await cmd

    assert exc_info.value.result == Result(
        exit_code=CANCELLED_EXIT_CODE,
        output_bytes=b"abc",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
    )

    # Test timeout, catch_cancelled_error, and exit_codes. You can't do this with
    # asyncio.wait_for; you have to use the timeout option.
    cmd = cmd.set(exit_codes={CANCELLED_EXIT_CODE})
    result = await cmd
    assert result == "abc"


async def test_as_completed(echo_cmd):
    "Test shellous using asyncio's `as_completed` function."
    cmds = [echo_cmd(i).env(SHELLOUS_EXIT_SLEEP=0.75 * i) for i in range(5)]

    for i, cmd in enumerate(asyncio.as_completed(cmds)):
        result = await cmd
        assert result == str(i)


async def test_multiple_context_manager(echo_cmd):
    "Test use of multiple context managers at the same time."
    echo1 = echo_cmd(1).stdout(sh.CAPTURE)
    echo2 = echo_cmd(2).stdout(sh.CAPTURE)

    async with echo1 as run1, echo2 as run2:
        line1 = await run1.stdout.read()
        line2 = await run2.stdout.read()
        assert int(line1) + 1 == int(line2)


async def test_asl_map(count_cmd):
    "Test compatibility with async itertools like `asyncstdlib.map`."

    def _double(line):
        return 2 * int(line.strip())

    stream = asl.map(_double, count_cmd(5))
    assert await asl.list(stream) == [2, 4, 6, 8, 10]

    assert await asl.anext(stream, "DONE") == "DONE"


async def test_asl_zip(count_cmd):
    "Test compatibility with async itertools like `asyncstdlib.zip`."
    calls = []

    def _audit(phase, info):
        if phase in ("start", "stop"):
            runner = info["runner"]
            calls.append((phase, runner.name))

    count = count_cmd.set(audit_callback=_audit)

    def _single(line):
        return int(line.strip())

    def _double(line):
        return 2 * int(line.strip())

    singled = asl.map(_single, count(7).set(alt_name="count1"))
    doubled = asl.map(_double, count(100).set(alt_name="count2"))
    zipped = asl.zip(singled, doubled)

    # The cool thing is that no subprocesses are launched until we iterate!
    assert not calls

    assert await asl.list(zipped) == [
        (1, 2),
        (2, 4),
        (3, 6),
        (4, 8),
        (5, 10),
        (6, 12),
        (7, 14),
    ]

    assert await asl.anext(zipped, "DONE") == "DONE"

    assert calls == [
        ("start", "count1"),
        ("start", "count2"),
        ("stop", "count1"),
        ("stop", "count2"),
    ]


async def test_asl_takewhile_sum(count_cmd, echo_cmd):
    "Test compatibility with async itertools like `asyncstdlib.takewhile`."

    def _to_int(line):
        return int(line.strip())

    def _less_than_20(num):
        return num < 20

    stuff = asl.chain(count_cmd(6), echo_cmd("10\n", "20\n", "30\n", "40\n"))
    ints = asl.map(_to_int, stuff)
    value = asl.sum(asl.takewhile(_less_than_20, ints))

    assert await value == 31


async def test_asl_islice(count_cmd):
    "Test compatibility with async itertool `islice`."
    iterator = count_cmd(25)

    async with asl.scoped_iter(iterator) as iter1:
        oddlines = asl.islice(iter1, 0, None, 2)
        firstfourodd = asl.islice(oddlines, 4)

        assert await asl.list(firstfourodd) == ["1\n", "3\n", "5\n", "7\n"]


async def test_bulk_line_limit(bulk_cmd):
    "Test line iteration with bulk command."
    with pytest.raises(ValueError, match="Separator is not found"):
        async for _ in bulk_cmd:
            assert False  # never reached


def _run(cmd):
    "Run command in process pool executor."

    async def _coro():
        _attach_loop()
        return await cmd

    return asyncio.run(_coro())


async def test_process_pool_executor(echo_cmd, report_children):
    """Test that a command can be executed in a ProcessPoolExecutor.

    This tests that a command is pickle-able. It also tests that
    ProcessPoolExecutor and shellous can co-exist.
    """
    from concurrent.futures import ProcessPoolExecutor

    echo = echo_cmd.set(_return_result=True)

    with ProcessPoolExecutor() as executor:
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(executor, _run, echo("abc"))
        # Be aware this can fail if the test_shellous.py module imports
        # a relative module like conftest.
        result = await fut

    assert result == Result(
        exit_code=0,
        output_bytes=b"abc",
        error_bytes=b"",
        cancelled=False,
        encoding="utf-8",
    )

    # Close the multiprocessing resource_tracker. Otherwise, it will trigger
    # failures for open fd's and child processes.
    from multiprocessing import resource_tracker

    resource_tracker._resource_tracker._stop()  # pyright: ignore[reportGeneralTypeIssues]
