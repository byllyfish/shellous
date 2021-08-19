"Unit tests for shellous module (Linux and MacOS)."

import asyncio
import io
import os
import sys

import pytest
from shellous import (
    CAPTURE,
    DEVNULL,
    INHERIT,
    STDOUT,
    PipeResult,
    Result,
    ResultError,
    context,
)
from shellous.util import gather_collect

unix_only = pytest.mark.skipif(sys.platform == "win32", reason="Unix")
pytestmark = [pytest.mark.asyncio, unix_only]


@pytest.fixture
def sh():
    return context()


async def test_python(sh):
    "Test running the python executable."
    result = await sh(sys.executable, "-c", "print('test1')")
    assert result == "test1\n"


async def test_echo(sh):
    "Test running the echo command."
    result = await sh("echo", "-n", "foo")
    assert result == "foo"


async def test_echo_bytes(sh):
    "Test running the echo command with bytes output."
    result = await sh("echo", "-n", "foo").set(encoding=None)
    assert result == b"foo"


async def test_echo_with_result(sh):
    "Test running the echo command using the Result object."
    result = await sh("echo", "-n", "foo").set(return_result=True)
    assert result == Result(
        output_bytes=b"foo",
        exit_code=0,
        cancelled=False,
        encoding="utf-8",
        extra=None,
    )
    assert result.output == "foo"


async def test_which(sh):
    "Test running the `which` command."
    result = await sh("which", "cat")
    assert result in {"/usr/bin/cat\n", "/bin/cat\n"}


async def test_context_env(sh):
    "Test running the env command with a custom environment context."
    env = {
        "PATH": "/bin:/usr/bin",
    }
    sh = sh.env(**env).set(inherit_env=False)
    result = await sh("env")
    assert result == "PATH=/bin:/usr/bin\n"


async def test_default_env(sh):
    "Test running the env command with an augmented default environment."
    result = await sh("env").env(MORE="less")
    assert "MORE=less" in result.split("\n")


async def test_augmented_env(sh):
    "Test running the env command with an augmented custom environment."
    env = {
        "PATH": "/bin:/usr/bin",
    }
    sh = sh.env(**env).set(inherit_env=False)
    result = await sh("env").env(MORE="less")
    assert sorted(result.rstrip().split("\n")) == [
        "MORE=less",
        "PATH=/bin:/usr/bin",
    ]


async def test_empty_env(sh):
    "Test running the env command with an empty environment."
    result = await sh("/usr/bin/env").set(inherit_env=False)
    assert result == ""


async def test_custom_echo_func(sh):
    "Test defining a custom echo function."

    def excited(*args):
        return sh("echo", "-n", *(args + ("!!",)))

    result = await excited("x", "z")
    assert result == "x z !!"


async def test_custom_echo_shorthand(sh):
    "Test defining an echo function that always passes -n (shorthand)."
    echo = sh("echo", "-n")
    result = await echo("x", "y")
    assert result == "x y"


async def test_missing_executable(sh):
    "Test invoking a non-existant command raises a FileNotFoundError."
    with pytest.raises(FileNotFoundError):
        await sh("/bin/does_not_exist")


async def test_task(sh):
    "Test converting an awaitable command into an asyncio Task object."
    task = sh("echo", "***").task()
    assert task.get_name().startswith("echo-")
    result = await task
    assert result == "***\n"


async def test_task_cancel(sh):
    "Test that we can cancel a running command task."
    task = sh("sleep", "5").task()
    await asyncio.sleep(0.1)

    # Cancel task and wait for it to exit.
    task.cancel()
    with pytest.raises(ResultError):  # FIXME: no exception expected?
        await task


async def test_task_immediate_cancel(sh):
    "Test that we can cancel a running command task."
    task = sh("sleep", "5").task()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_timeout_fail(sh):
    "Test that an awaitable command can be called with a timeout."
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(sh("sleep", "5"), 0.1)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=None,
        exit_code=-9,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_timeout_fail_no_capturing(sh):
    "Test that an awaitable command can be called with a timeout."
    cmd = sh("sleep", "5").stdin(DEVNULL).stdout(DEVNULL)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, 0.1)

    assert exc_info.value.result == Result(
        output_bytes=None,
        exit_code=-9,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_timeout_okay(sh):
    "Test awaitable command that doesn't timeout."
    result = await asyncio.wait_for(sh("echo", "jjj"), 1.0)
    assert result == "jjj\n"


async def test_input(sh):
    "Test calling a command with input string."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    result = await tr.stdin("some input")
    assert result == "SOME INPUT"


async def test_input_bytes(sh):
    "Test calling a command with input bytes, and encoding is utf-8."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    result = await tr.stdin(b"some input")
    assert result == "SOME INPUT"


async def test_input_wrong_encoding(sh):
    "Test calling a command with input string, but bytes encoding expected."
    sh = sh.set(encoding=None)
    tr = sh("tr", "[:lower:]", "[:upper:]")
    with pytest.raises(TypeError, match="input must be bytes"):
        await tr.stdin("some input")


async def test_input_none_encoding(sh):
    "Test calling a command with input string, but bytes encoding expected."
    tr = sh("tr", "[:lower:]", "[:upper:]").set(encoding=None)
    result = await tr.stdin(b"here be bytes")
    assert result == b"HERE BE BYTES"


async def test_exit_code_error(sh):
    "Test calling a command that returns a non-zero exit status."
    with pytest.raises(ResultError) as exc_info:
        await sh("false")

    assert exc_info.value.result == Result(
        output_bytes=b"",
        exit_code=1,
        cancelled=False,
        encoding="utf-8",
        extra=None,
    )


async def test_redirect_output_path(sh, tmp_path):
    "Test redirecting command output to a PathLike object."
    out = tmp_path / "test_redirect_output_path"

    # Erase and write to file.
    result = await sh("echo", "test", 1, 2, 3).stdout(out)
    assert result is None
    assert out.read_bytes() == b"test 1 2 3\n"

    # Append to the above file.
    result = await sh("echo", ".1.").stdout(out, append=True)
    assert result is None
    assert out.read_bytes() == b"test 1 2 3\n.1.\n"


async def test_redirect_output_str(sh, tmp_path):
    "Test redirecting command output to a filename string."
    out = tmp_path / "test_redirect_output_str"

    # Erase and write to file.
    result = await sh("echo", "test", 4, 5, 6).stdout(str(out))
    assert result is None
    assert out.read_bytes() == b"test 4 5 6\n"

    # Append to the above file.
    result = await sh("echo", ".2.").stdout(str(out), append=True)
    assert result is None
    assert out.read_bytes() == b"test 4 5 6\n.2.\n"


async def test_redirect_output_file(sh, tmp_path):
    "Test redirecting command output to a File-like object."
    out = tmp_path / "test_redirect_output_file"

    with open(out, "w") as fp:
        result = await sh("echo", "123").stdout(fp)
        assert result is None

    assert out.read_bytes() == b"123\n"


async def test_redirect_output_stringio(sh):
    "Test redirecting command output to a StringIO buffer."
    buf = io.StringIO()
    result = await sh("echo", "456").stdout(buf)
    assert result is None
    assert buf.getvalue() == "456\n"


async def test_redirect_output_devnull(sh):
    "Test redirecting command output to /dev/null."
    result = await sh("echo", "789").stdout(DEVNULL)
    assert result is None


async def test_redirect_output_stdout(sh):
    "Test redirecting command output to STDOUT (bad file descriptor)."
    with pytest.raises(OSError, match="Bad file descriptor"):
        await sh("echo", "789").stdout(STDOUT)


async def test_redirect_output_none(sh):
    "Test redirecting command output to None (same as DEVNULL)."
    result = await sh("echo", "789").stdout(None)
    assert result is None


async def test_redirect_output_capture(sh):
    "Test redirecting command output to None (same as DEVNULL)."
    result = await sh("echo", "789").stdout(CAPTURE)
    assert result == "789\n"


async def test_redirect_output_inherit(sh, capfd):
    "Test redirecting command output to None (same as DEVNULL)."
    result = await sh("echo", "789").stdout(INHERIT)
    assert result is None
    assert capfd.readouterr() == ("789\n", "")


async def test_redirect_input_path(sh, tmp_path):
    "Test redirecting input to a Path like object."
    data_file = tmp_path / "test_redirect_input_path"
    data_file.write_text("here is some text")
    result = await sh("wc", "-c").stdin(data_file)
    assert result.lstrip() == "17\n"


@pytest.fixture
def python_script():
    script = r"""
import sys
sys.stdout.write("hi ")
sys.stdout.write("stdout\n")
sys.stdout.flush()
sys.stderr.write("hi stderr\n")
sys.stdout.write("goodbye!")
"""
    sh = context()
    return sh(sys.executable, "-").stdin(script)


async def test_redirection(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    err = io.StringIO()
    result = await python_script().stderr(err)
    assert result == "hi stdout\ngoodbye!"
    assert err.getvalue() == "hi stderr\n"
    assert capfd.readouterr() == ("", "")


async def test_redirect_output_to_devnull(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stdout(DEVNULL)
    assert result is None
    assert capfd.readouterr() == ("", "")


async def test_redirect_output_to_inherit(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stdout(INHERIT)
    assert result is None
    assert capfd.readouterr() == ("hi stdout\ngoodbye!", "")


async def test_redirect_error_to_stdout(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stderr(STDOUT)
    assert result == "hi stdout\nhi stderr\ngoodbye!"
    assert capfd.readouterr() == ("", "")


async def test_capture_error_only(python_script, capfd):
    "Test redirection options with stderr output."
    result = await python_script.stderr(CAPTURE).stdout(DEVNULL)
    assert result == "hi stderr\n"
    assert capfd.readouterr() == ("", "")


async def test_redirect_error_to_inherit(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stderr(INHERIT)
    assert result == "hi stdout\ngoodbye!"
    assert capfd.readouterr() == ("", "hi stderr\n")


async def test_redirect_error_to_devnull(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stderr(DEVNULL)
    assert result == "hi stdout\ngoodbye!"
    assert capfd.readouterr() == ("", "")


async def test_redirect_error_to_capture(python_script):
    "Test using CAPTURE when not using `async with`."
    with pytest.raises(ValueError, match="multiple capture requires 'async with'"):
        await python_script.stderr(CAPTURE)


async def test_async_context_manager(sh):
    "Use `async with` to read/write bytes incrementally."
    tr = sh("tr", "[:lower:]", "[:upper:]").stdin(CAPTURE)

    async with tr.runner() as (stdin, stdout, stderr):
        assert stderr is None

        # N.B. We won't deadlock writing/reading a single byte.
        stdin.write(b"a")
        stdin.close()
        result = await stdout.read()

    assert result == b"A"


async def test_async_iteration(sh):
    "Use `async for` to read stdout line by line."
    echo = sh("echo", "-n", "line1\n", "line2\n", "line3").stderr(DEVNULL)
    result = [line async for line in echo]
    assert result == ["line1\n", " line2\n", " line3"]


async def test_manually_created_pipeline(sh):
    "You can create a pipeline manually using input redirection and os.pipe()."
    (r, w) = os.pipe()
    echo = sh("echo", "-n", "abc").stdout(w, close=True)
    tr = sh("tr", "[:lower:]", "[:upper:]").stdin(r, close=True)
    result = await asyncio.gather(tr, echo)
    assert result == ["ABC", None]


async def test_pipeline(sh):
    "Test a simple pipeline."
    pipe = sh("echo", "-n", "xyz") | sh("tr", "[:lower:]", "[:upper:]")
    result = await pipe
    assert result == "XYZ"


async def test_pipeline_with_env(sh):
    "Test a simple pipeline with an augmented environment."
    pipe = sh("echo", "-n", "xyz").env(FOO=1) | sh("tr", "[:lower:]", "[:upper:]")
    result = await pipe
    assert result == "XYZ"


async def test_pipeline_with_result(sh):
    "Test a simple pipeline with `return_result` set to True."
    echo = sh("echo", "-n", "xyz")
    tr = sh("tr", "[:lower:]", "[:upper:]").set(return_result=True)
    result = await (echo | tr)
    assert result == Result(
        output_bytes=b"XYZ",
        exit_code=0,
        cancelled=False,
        encoding="utf-8",
        extra=(
            PipeResult(
                exit_code=0,
                cancelled=False,
            ),
            PipeResult(
                exit_code=0,
                cancelled=False,
            ),
        ),
    )


async def test_pipeline_single_cmd(sh):
    "Test a single command pipeline."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    result = await ("abc" | tr)
    assert result == "ABC"


async def test_pipeline_invalid_cmd1(sh):
    pipe = sh(" non_existant ", "xyz") | sh("tr", "[:lower:]", "[:upper:]")
    with pytest.raises(FileNotFoundError, match="' non_existant '"):
        await pipe


async def test_pipeline_invalid_cmd2(sh):
    pipe = sh("echo", "abc") | sh(" non_existant ", "xyz")
    with pytest.raises(FileNotFoundError, match="' non_existant '"):
        await pipe


async def test_allowed_exit_codes(sh):
    "Test `allows_exit_codes` option."
    sh = sh.stderr(STDOUT).set(allowed_exit_codes={1})
    cmd = sh("cat", "/tmp/__does_not_exist__")
    result = await cmd
    assert result == "cat: /tmp/__does_not_exist__: No such file or directory\n"


async def test_pipeline_async_iteration(sh):
    "Use `async for` to read stdout line by line."
    echo = sh("echo", "-n", "line1\n", "line2\n", "line3")
    cat = sh("cat")
    result = [line async for line in (echo | cat)]
    assert result == ["line1\n", " line2\n", " line3"]


async def test_pipeline_async_context_manager(sh):
    "Use `async with` to read/write bytes incrementally."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    pipe = (tr | sh("cat")).stdin(CAPTURE)
    async with pipe.runner() as (stdin, stdout, stderr):
        assert stderr is None

        # N.B. We won't deadlock writing/reading a single byte.
        stdin.write(b"a")
        stdin.close()
        result = await stdout.read()

    assert result == b"A"


async def test_gather_same_cmd(sh):
    """Test passing the same cmd to gather_collect().

    This test fails with `asyncio.gather`.
    """
    cmd = sh(sys.executable, "-c", "import secrets; print(secrets.randbits(31))")
    results = await gather_collect(cmd, cmd)
    assert results[0] != results[1]


async def test_cancelled_antipattern(sh):
    """Test the ResultError/cancellation anti-pattern.

    Catching `ResultError` using try/except conceals the `CancelledError`
    when you want to cancel the current task.
    """

    sleep_cmd = sh("sleep", 3600).set(return_result=True)

    async def _subtask():
        try:
            result1 = await sleep_cmd
        except ResultError as ex:
            assert ex.result.cancelled
            # First, CancelledError is lost!
        result2 = await sleep_cmd

    task = asyncio.create_task(_subtask())

    # Wait for task to start, then inject a CancelledError.
    await asyncio.sleep(0.01)
    task.cancel()

    # Catch ResultError from second sleep command. We have to use a timeout,
    # otherwise we'd wait an hour for the 2nd sleep command to finish.
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(task, timeout=0.1)

    assert exc_info.value.result.cancelled


async def test_cancelled_antipattern_fix(sh):
    """Test the ResultError/cancellation anti-pattern fix.

    Catching `ResultError` using try/except conceals the `CancelledError`
    when you want to cancel the current task.

    The fix is to call `ex.raise_cancel()` when you are done with the
    `ResultError`.
    """

    sleep_cmd = sh("sleep", 3600).set(return_result=True)

    async def _subtask():
        try:
            result1 = await sleep_cmd
        except ResultError as ex:
            assert ex.result.cancelled
            ex.raise_cancel()  # Re-raises CancelledError when necessary

        result2 = await sleep_cmd

    task = asyncio.create_task(_subtask())

    # Wait for task to start, then inject a CancelledError.
    await asyncio.sleep(0.01)
    task.cancel()

    # Wait for task to report it is cancelled.
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled


async def test_multiple_capture(sh):
    "Test the multiple capture example from the documentation."
    cmd = sh("cat").stdin(CAPTURE)

    runner = cmd.runner()
    async with runner as (stdin, stdout, _stderr):
        stdin.write(b"abc\n")
        output, _ = await asyncio.gather(stdout.readline(), stdin.drain())

        stdin.close()
    result = runner.result(output)

    assert result == "abc\n"
