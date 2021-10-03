"Unit tests for shellous module (Linux and MacOS)."

import asyncio
import io
import os
import re
import signal
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
    canonical,
    cbreak,
    context,
    raw,
)
from shellous.harvest import harvest_results

unix_only = pytest.mark.skipif(sys.platform == "win32", reason="Unix")
pytestmark = [pytest.mark.asyncio, unix_only]

_CANCELLED_EXIT_CODE = -15


def _is_uvloop():
    "Return true if we're running under uvloop."
    return os.environ.get("SHELLOUS_LOOP_TYPE") == "uvloop"


def _is_codecov_linux():
    "Return true if we're running code coverage under Linux."
    return sys.platform == "linux" and os.environ.get("SHELLOUS_CODE_COVERAGE")


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


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
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
    task = asyncio.create_task(sh("echo", "***").coro())
    result = await task
    assert result == "***\n"


async def test_task_cancel(sh):
    "Test that we can cancel a running command task."
    task = asyncio.create_task(sh("sleep", "5").coro())
    await asyncio.sleep(0.1)

    # Cancel task and wait for it to exit.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_task_cancel_incomplete_result(sh):
    "Test that we can cancel a running command task."
    task = asyncio.create_task(sh("sleep", "5").set(incomplete_result=True).coro())
    await asyncio.sleep(0.1)

    # Cancel task and wait for it to exit.
    task.cancel()
    with pytest.raises(ResultError):
        await task


async def test_task_immediate_cancel(sh):
    "Test that we can cancel a running command task."
    task = asyncio.create_task(sh("sleep", "5").coro())
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_timeout_fail(sh):
    "Test that an awaitable command can be called with a timeout."
    cmd = sh("sleep", "5").set(incomplete_result=True)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, 0.2)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=b"",
        exit_code=_CANCELLED_EXIT_CODE,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_timeout_fail_no_capturing(sh):
    "Test that an awaitable command can be called with a timeout."
    cmd = sh("sleep", "5").stdin(DEVNULL).stdout(DEVNULL).set(incomplete_result=True)

    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, 0.2)

    assert exc_info.value.result == Result(
        output_bytes=b"",
        exit_code=_CANCELLED_EXIT_CODE,
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
    assert result == ""
    assert out.read_bytes() == b"test 1 2 3\n"

    # Append to the above file.
    result = await sh("echo", ".1.").stdout(out, append=True)
    assert result == ""
    assert out.read_bytes() == b"test 1 2 3\n.1.\n"


async def test_redirect_output_str(sh, tmp_path):
    "Test redirecting command output to a filename string."
    out = tmp_path / "test_redirect_output_str"

    # Erase and write to file.
    result = await sh("echo", "test", 4, 5, 6).stdout(str(out))
    assert result == ""
    assert out.read_bytes() == b"test 4 5 6\n"

    # Append to the above file.
    result = await sh("echo", ".2.").stdout(str(out), append=True)
    assert result == ""
    assert out.read_bytes() == b"test 4 5 6\n.2.\n"


async def test_redirect_output_file(sh, tmp_path):
    "Test redirecting command output to a File-like object."
    out = tmp_path / "test_redirect_output_file"

    with open(out, "w") as fp:
        result = await sh("echo", "123").stdout(fp)
        assert result == ""

    assert out.read_bytes() == b"123\n"


async def test_redirect_output_stringio(sh):
    "Test redirecting command output to a StringIO buffer."
    buf = io.StringIO()
    result = await sh("echo", "456").stdout(buf)
    assert result == ""
    assert buf.getvalue() == "456\n"


async def test_redirect_output_devnull(sh):
    "Test redirecting command output to /dev/null."
    result = await sh("echo", "789").stdout(DEVNULL)
    assert result == ""


async def test_redirect_output_stdout(sh):
    "Test redirecting command output to STDOUT."
    with pytest.raises(ValueError, match="STDOUT is only supported by stderr"):
        await sh("echo", "789").stdout(STDOUT)


async def test_redirect_output_none(sh):
    "Test redirecting command output to None (same as DEVNULL)."
    result = await sh("echo", "789").stdout(None)
    assert result == ""


async def test_redirect_output_capture(sh):
    "Test redirecting command output to None (same as DEVNULL)."
    result = await sh("echo", "789").stdout(CAPTURE)
    assert result == "789\n"


async def test_redirect_output_inherit(sh, capfd):
    "Test redirecting command output to None (same as DEVNULL)."
    result = await sh("echo", "789").stdout(INHERIT)
    assert result == ""
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
    assert result == ""
    assert capfd.readouterr() == ("", "")


async def test_redirect_output_to_inherit(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stdout(INHERIT)
    assert result == ""
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

    async with tr.run() as run:
        assert run.stderr is None

        # N.B. We won't deadlock writing/reading a single byte.
        run.stdin.write(b"a")
        run.stdin.close()
        result = await run.stdout.read()

    assert result == b"A"


async def test_async_iteration(sh):
    "Use `async for` to read stdout line by line."
    echo = sh("echo", "-n", "line1\n", "line2\n", "line3").stderr(DEVNULL)
    async with echo.run() as run:
        result = [line async for line in run]
    assert result == ["line1\n", " line2\n", " line3"]


async def test_manually_created_pipeline(sh):
    "You can create a pipeline manually using input redirection and os.pipe()."
    (r, w) = os.pipe()
    echo = sh("echo", "-n", "abc").stdout(w, close=True)
    tr = sh("tr", "[:lower:]", "[:upper:]").stdin(r, close=True)
    result = await asyncio.gather(tr, echo)
    assert result == ["ABC", ""]


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
    # uvloop's error message does not include filename.
    with pytest.raises(FileNotFoundError):
        await pipe


async def test_pipeline_invalid_cmd2(sh):
    pipe = sh("echo", "abc") | sh(" non_existant ", "xyz")
    # uvloop's error message does not include filename.
    with pytest.raises(FileNotFoundError):
        await pipe


async def test_exit_codes(sh):
    "Test `allows_exit_codes` option."
    sh = sh.stderr(STDOUT).set(exit_codes={1})
    cmd = sh("cat", "/tmp/__does_not_exist__")
    result = await cmd
    assert result == "cat: /tmp/__does_not_exist__: No such file or directory\n"


async def test_pipeline_async_iteration(sh):
    "Use `async for` to read stdout line by line."
    echo = sh("echo", "-n", "line1\n", "line2\n", "line3")
    cat = sh("cat")
    async with (echo | cat).run() as run:
        result = [line async for line in run]
    assert result == ["line1\n", " line2\n", " line3"]


async def test_pipeline_async_context_manager(sh):
    "Use `async with` to read/write bytes incrementally."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    pipe = (tr | sh("cat")).stdin(CAPTURE)
    async with pipe.run() as run:
        assert run.stderr is None

        # N.B. We won't deadlock writing/reading a single byte.
        run.stdin.write(b"a")
        run.stdin.close()
        result = await run.stdout.read()

    assert result == b"A"
    assert run.name == "tr|cat"
    assert (
        repr(run) == "<PipeRunner 'tr|cat' results=["
        "Result(output_bytes=b'', exit_code=0, cancelled=False, encoding='utf-8', extra=None), "
        "Result(output_bytes=b'', exit_code=0, cancelled=False, encoding='utf-8', extra=None)]>"
    )


async def test_gather_same_cmd(sh):
    """Test passing the same cmd to harvest().

    This test fails with `asyncio.gather`.
    """
    cmd = sh(sys.executable, "-c", "import secrets; print(secrets.randbits(31))")
    results = await harvest_results(cmd, cmd)
    assert results[0] != results[1]


async def test_cancelled_antipattern(sh):
    """Test the ResultError/cancellation anti-pattern.

    This occurs *only* when incomplete_result=True.

    Catching `ResultError` using try/except conceals the `CancelledError`
    when you want to cancel the current task.
    """

    sleep_cmd = sh("sleep", 3600).set(return_result=True, incomplete_result=True)

    async def _subtask():
        try:
            await sleep_cmd
        except ResultError as ex:
            # sleep_cmd has incomplete_result set to True.
            assert ex.result.cancelled
            # CancelledError is lost!
        await sleep_cmd

    task = asyncio.create_task(_subtask())

    # Wait for task to start, then inject a CancelledError.
    await asyncio.sleep(0.1)
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

    The fix is to explicitly re-raise the CancelledError when you are done with
    the `ResultError`.
    """

    sleep_cmd = sh("sleep", 3600).set(return_result=True, incomplete_result=True)

    async def _subtask():
        try:
            await sleep_cmd
        except ResultError as ex:
            assert ex.result.cancelled
            if ex.result.cancelled:
                raise asyncio.CancelledError() from ex

        await sleep_cmd

    task = asyncio.create_task(_subtask())

    # Wait for task to start, then inject a CancelledError.
    await asyncio.sleep(0.1)
    task.cancel()

    # Wait for task to report it is cancelled.
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled


async def test_multiple_capture(sh):
    "Test the multiple capture example from the documentation."
    cmd = sh("cat").stdin(CAPTURE)

    async with cmd.run() as run:
        run.stdin.write(b"abc\n")
        output, _ = await asyncio.gather(run.stdout.readline(), run.stdin.drain())
        run.stdin.close()

    result = run.result(output)
    assert result == "abc\n"


async def test_cancel_timeout(sh):
    "Test the `cancel_timeout` setting."
    sleep = sh("nohup", "sleep").set(
        cancel_timeout=0.25,
        cancel_signal=signal.SIGHUP,
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sleep(10.0), 0.25)


async def test_shell_cmd(sh):
    "Test a shell command.  (https://bugs.python.org/issue43884)"
    shell = sh("/bin/sh", "-c").set(
        return_result=True,
        incomplete_result=True,
        exit_codes={0, _CANCELLED_EXIT_CODE},
    )

    task = asyncio.create_task(shell("sleep 2 && echo done").coro())
    await asyncio.sleep(0.25)
    task.cancel()

    with pytest.raises(ResultError) as exc_info:
        await task

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=b"",
        exit_code=_CANCELLED_EXIT_CODE,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_large_cat(sh):
    "Test cat with some decent sized data."
    data = b"x" * (4 * 1024 * 1024 + 1)
    cat = sh("cat").set(encoding=None)
    result = await (data | cat)
    assert len(result) == (4 * 1024 * 1024 + 1)
    assert result == data


async def test_env_ellipsis_unix(sh):
    "Test using `...` in env method to grab value from global environment."
    cmd = sh("env").set(inherit_env=False).env(PATH=...)
    result = await cmd
    assert result.startswith("PATH=")


async def test_env_ellipsis_unix_wrong_case(sh):
    "Test using `...` in env method to grab value from global environment."

    # Fails because actual env is named "PATH", not "path"
    with pytest.raises(KeyError):
        sh("env").set(inherit_env=False).env(path=...)


async def test_process_substitution(sh):
    """Test process substitution.

    ```
    diff <(echo a) <(echo b)
    ```
    """

    cmd = sh("diff", sh("echo", "a"), sh("echo", "b")).set(exit_codes={0, 1})
    result = await cmd
    assert result == "1c1\n< a\n---\n> b\n"


async def test_process_substitution_with_pipe(sh):
    """Test process substitution.

    ```
    diff <(echo a | cat) <(echo b | cat)
    ```
    """

    pipe1 = sh("echo", "a") | sh("cat")
    pipe2 = sh("echo", "b") | sh("cat")
    cmd = sh("diff", pipe1, pipe2).set(exit_codes={0, 1})
    result = await cmd
    assert result == "1c1\n< a\n---\n> b\n"


async def test_process_substitution_write(sh, tmp_path):
    """Test process substitution with write_mode.

    ```
    echo a | tee >(cat > tmpfile)
    ```
    """

    out = tmp_path / "test_process_sub_write"
    cat = sh("cat") | out
    pipe = sh("echo", "a") | sh("tee", ~cat())

    result = await pipe
    assert result == "a\n"
    assert out.read_bytes() == b"a\n"


async def test_process_substitution_write_pipe(sh, tmp_path):
    """Test process substitution with write_mode.

    ```
    echo b | tee >(cat | cat > tmpfile)
    ```
    """

    out = tmp_path / "test_process_sub_write"
    cat = sh("cat") | sh("cat") | out
    pipe = sh("echo", "b") | sh("tee", ~cat())

    result = await pipe
    assert result == "b\n"
    assert out.read_bytes() == b"b\n"


async def test_process_substitution_write_pipe_alt(sh, tmp_path):
    """Test process substitution with write_mode.

    Change where the ~ appears; put it on the first command of the pipe.

    ```
    echo b | tee >(cat | cat > tmpfile)
    ```
    """

    out = tmp_path / "test_process_sub_write"
    cat = ~sh("cat") | sh("cat") | out
    pipe = sh("echo", "b") | sh("tee", cat()).stderr(INHERIT)

    result = await pipe
    assert result == "b\n"
    assert out.read_bytes() == b"b\n"


async def test_start_new_session(sh):
    """Test `start_new_session` option."""

    script = """import os; print(os.getsid(0) == os.getpid())"""
    cmd = sh(sys.executable, "-c", script)

    result = await cmd
    assert result == "False\n"

    result = await cmd.set(start_new_session=False)
    assert result == "False\n"

    result = await cmd.set(start_new_session=True)
    assert result == "True\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_manual(sh):
    """Test setting up a pty manually."""

    import pty  # import not supported on windows

    parent_fd, child_fd = pty.openpty()

    tr = sh("tr", "[:lower:]", "[:upper:]")
    cmd = (
        tr.stdin(child_fd, close=True)
        .stdout(child_fd, close=True)
        .set(start_new_session=True)
    )

    async with cmd.run():
        # Use synchronous functions to test pty directly.
        os.write(parent_fd, b"abc\n")
        await asyncio.sleep(0.1)
        result = os.read(parent_fd, 1024)
        os.close(parent_fd)

    assert result == b"abc\r\nABC\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_manual_ls(sh):
    """Test setting up a pty manually."""

    import pty  # import not supported on windows

    import shellous
    from shellous.log import log_timer

    parent_fd, child_fd = pty.openpty()

    cmd = (
        sh("ls", "README.md")
        .stdin(child_fd, close=False)
        .stdout(child_fd, close=False)
        .set(start_new_session=True, preexec_fn=shellous.pty_util.set_ctty(child_fd))
    )

    result = bytearray()
    async with cmd.run():
        # Use synchronous functions to test pty directly.
        while True:
            try:
                with log_timer("os.read", -1):
                    data = os.read(parent_fd, 4096)
            except OSError:  # indicates EOF on Linux
                data = b""
            if not data:
                break
            result.extend(data)
            # Close child_fd after first successful read. If we close it
            # in the parent immediately after forking the child, we don't read
            # anything in the parent on MacOS! This has something to do with
            # the process exiting so quickly with so little output.
            with log_timer("os.close", -1):
                if child_fd >= 0:
                    os.close(child_fd)
                    child_fd = -1

    os.close(parent_fd)

    assert result == b"README.md\r\n"


async def _get_streams(fd):
    "Wrap fd in a StreamReader, StreamWriter pair."

    import fcntl

    fcntl.fcntl(fd, fcntl.F_SETFL, os.O_NONBLOCK)
    reader_pipe = os.fdopen(fd, "rb", 0, closefd=False)
    writer_pipe = os.fdopen(fd, "wb", 0, closefd=True)

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(loop=loop)
    reader_protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    reader_transport, _ = await loop.connect_read_pipe(
        lambda: reader_protocol,
        reader_pipe,
    )
    writer_transport, writer_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin,
        writer_pipe,
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

    # Patch writer_transport.close so it also closes the reader_transport.
    def _close():
        _orig_close()
        reader_transport.close()

    writer_transport.close, _orig_close = _close, writer_transport.close

    return reader, writer


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_manual_streams(sh):
    """Test setting up a pty manually."""

    import pty  # import not supported on windows

    parent_fd, child_fd = pty.openpty()

    tr = sh("tr", "[:lower:]", "[:upper:]")
    cmd = (
        tr.stdin(child_fd, close=True)
        .stdout(child_fd, close=True)
        .set(start_new_session=True)
    )

    reader, writer = await _get_streams(parent_fd)

    async with cmd.run():
        writer.write(b"abc\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        result = await reader.read(1024)
        writer.close()

    assert result == b"abc\r\nABC\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty(sh):
    "Test the `pty` option."
    cmd = sh("tr", "[:lower:]", "[:upper:]").stdin(CAPTURE).set(pty=True)

    async with cmd.run() as run:
        run.stdin.write(b"abc\n")
        await run.stdin.drain()
        await asyncio.sleep(0.1)
        result = await run.stdout.read(1024)
        run.stdin.close()

    assert result == b"abc\r\nABC\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_ctermid(sh):
    "Test the `pty` option and print out the ctermid, ttyname for stdin/stdout."
    cmd = (
        sh(
            sys.executable,
            "-c",
            "import os; print(os.ctermid(), os.ttyname(0), os.ttyname(1))",
        )
        .stdin(CAPTURE)
        .set(pty=True)
    )

    async with cmd.run() as run:
        result = await run.stdout.read(1024)
        run.stdin.close()

    ctermid, stdin_tty, stdout_tty = result.split()

    print(ctermid, stdin_tty, stdout_tty)
    assert re.fullmatch(br"/dev/(?:ctty|tty|pts/\d+)", ctermid), ctermid
    assert re.fullmatch(br"/dev/(?:ttys|pts/)\d+", stdin_tty), stdin_tty
    assert re.fullmatch(br"/dev/(?:ttys|pts/)\d+", stdout_tty), stdout_tty


_STTY_DARWIN = (
    b"speed 9600 baud; 0 rows; 0 columns;\r\n"
    b"lflags: icanon isig iexten echo echoe -echok echoke -echonl echoctl\r\n"
    b"\t-echoprt -altwerase -noflsh -tostop -flusho -pendin -nokerninfo\r\n"
    b"\t-extproc\r\n"
    b"iflags: -istrip icrnl -inlcr -igncr ixon -ixoff ixany imaxbel -iutf8\r\n"
    b"\t-ignbrk brkint -inpck -ignpar -parmrk\r\n"
    b"oflags: opost onlcr -oxtabs -onocr -onlret\r\n"
    b"cflags: cread cs8 -parenb -parodd hupcl -clocal -cstopb -crtscts -dsrflow\r\n"
    b"\t-dtrflow -mdmbuf\r\n"
    b"cchars: discard = ^O; dsusp = ^Y; eof = ^D; eol = <undef>;\r\n"
    b"\teol2 = <undef>; erase = ^?; intr = ^C; kill = ^U; lnext = ^V;\r\n"
    b"\tmin = 1; quit = ^\\; reprint = ^R; start = ^Q; status = ^T;\r\n"
    b"\tstop = ^S; susp = ^Z; time = 0; werase = ^W;\r\n"
)

_STTY_LINUX = (
    b"speed 38400 baud; rows 0; columns 0; line = 0;\r\n"
    b"intr = ^C; quit = ^\\; erase = ^?; kill = ^U; eof = ^D; eol = <undef>;\r\n"
    b"eol2 = <undef>; swtch = <undef>; start = ^Q; stop = ^S; susp = ^Z; rprnt = ^R;\r\n"
    b"werase = ^W; lnext = ^V; discard = ^O; min = 1; time = 0;\r\n"
    b"-parenb -parodd -cmspar cs8 -hupcl -cstopb cread -clocal -crtscts\r\n"
    b"-ignbrk -brkint -ignpar -parmrk -inpck -istrip -inlcr -igncr icrnl ixon -ixoff\r\n"
    b"-iuclc -ixany -imaxbel -iutf8\r\n"
    b"opost -olcuc -ocrnl onlcr -onocr -onlret -ofill -ofdel nl0 cr0 tab0 bs0 vt0 ff0\r\n"
    b"isig icanon iexten echo echoe echok -echonl -noflsh -xcase -tostop -echoprt\r\n"
    b"echoctl echoke -flusho -extproc\r\n"
)

_STTY_FREEBSD_12 = (
    b"speed 9600 baud; 0 rows; 0 columns;\r\n"
    b"lflags: icanon isig iexten echo echoe -echok echoke -echonl echoctl\r\n"
    b"\t-echoprt -altwerase -noflsh -tostop -flusho -pendin -nokerninfo\r\n"
    b"\t-extproc\r\n"
    b"iflags: -istrip icrnl -inlcr -igncr ixon -ixoff ixany imaxbel -ignbrk\r\n"
    b"\tbrkint -inpck -ignpar -parmrk\r\n"
    b"oflags: opost onlcr -ocrnl tab0 -onocr -onlret\r\n"
    b"cflags: cread cs8 -parenb -parodd hupcl -clocal -cstopb -crtscts -dsrflow\r\n"
    b"\t-dtrflow -mdmbuf\r\n"
    b"cchars: discard = ^O; dsusp = ^Y; eof = ^D; eol = <undef>;\r\n"
    b"\teol2 = <undef>; erase = ^?; erase2 = ^H; intr = ^C; kill = ^U;\r\n"
    b"\tlnext = ^V; min = 1; quit = ^\\; reprint = ^R; start = ^Q;\r\n"
    b"\tstatus = ^T; stop = ^S; susp = ^Z; time = 0; werase = ^W;\r\n"
)

_STTY_FREEBSD_13 = (
    b"speed 9600 baud; 0 rows; 0 columns;\r\n"
    b"lflags: icanon isig iexten echo echoe -echok echoke -echonl echoctl\r\n"
    b"\t-echoprt -altwerase -noflsh -tostop -flusho -pendin -nokerninfo\r\n"
    b"\t-extproc\r\n"
    b"iflags: -istrip icrnl -inlcr -igncr ixon -ixoff ixany imaxbel -ignbrk\r\n"
    b"\tbrkint -inpck -ignpar -parmrk\r\n"
    b"oflags: opost onlcr -ocrnl tab0 -onocr -onlret\r\n"
    b"cflags: cread cs8 -parenb -parodd hupcl -clocal -cstopb -crtscts -dsrflow\r\n"
    b"\t-dtrflow -mdmbuf rtsdtr\r\n"
    b"cchars: discard = ^O; dsusp = ^Y; eof = ^D; eol = <undef>;\r\n"
    b"\teol2 = <undef>; erase = ^?; erase2 = ^H; intr = ^C; kill = ^U;\r\n"
    b"\tlnext = ^V; min = 1; quit = ^\\; reprint = ^R; start = ^Q;\r\n"
    b"\tstatus = ^T; stop = ^S; susp = ^Z; time = 0; werase = ^W;\r\n"
)


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_stty_all(sh, tmp_path):
    "Test the `pty` option and print out the result of stty -a"

    err = tmp_path / "test_pty_stty_all"
    cmd = sh("stty", "-a").stdin(CAPTURE).stderr(err).set(pty=True)

    buf = b""
    async with cmd.run() as run:
        while True:
            result = await run.stdout.read(1024)
            if not result:
                run.stdin.close()
                break
            buf += result

    assert err.read_bytes() == b""
    if sys.platform == "linux":
        assert buf == _STTY_LINUX
    elif sys.platform.startswith("freebsd"):
        assert buf == _STTY_FREEBSD_12 or buf == _STTY_FREEBSD_13
    else:
        assert buf == _STTY_DARWIN


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_tr_eot(sh):
    "Test the `pty` option with a control-D (EOT = 0x04)"
    cmd = sh("tr", "[:lower:]", "[:upper:]").stdin(CAPTURE).set(pty=True)

    async with cmd.run() as run:
        run.stdin.write(b"abc\n\x04")
        await run.stdin.drain()
        await asyncio.sleep(0.1)
        result = await run.stdout.read(1024)

    if sys.platform == "linux":
        assert result == b"abc\r\nABC\r\n"
    else:
        assert result == b"abc\r\n^D\x08\x08ABC\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cat_eot(sh):
    "Test the `pty` option with a control-D (EOT = 0x04)"
    cmd = sh("cat").stdin(CAPTURE).set(pty=True)

    async with cmd.run() as run:
        run.stdin.write(b"abc\x04\x04")
        await run.stdin.drain()
        await asyncio.sleep(0.1)
        result = await run.stdout.read(1024)

    if sys.platform == "linux":
        assert result == b"abcabc"
    else:
        assert result == b"abc^D\x08\x08^D\x08\x08abc"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_raw_size(sh):
    "Test the `pty` option in raw mode."

    cmd = sh("stty", "size").set(pty=raw(rows=17, cols=41))
    result = await cmd
    assert result == "17 41\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cbreak_size(sh):
    "Test the `pty` option in cbreak mode."

    cmd = sh("stty", "size").set(pty=cbreak(rows=19, cols=43))
    result = await cmd
    assert result == "19 43\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_raw_ls(sh):
    "Test the `pty` option in raw mode."

    cmd = sh("ls").set(pty=raw(rows=24, cols=40)).stderr(STDOUT)
    result = await cmd

    assert "README.md" in result
    for line in io.StringIO(result):
        assert len(line.rstrip()) <= 40


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_raw_size_inherited(sh):
    "Test the `pty` option in raw mode."

    cmd = sh("stty", "size").set(pty=raw(rows=..., cols=...))
    result = await cmd

    rows, cols = (int(n) for n in result.rstrip().split())
    assert rows >= 0 and cols >= 0


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cat_auto_eof(sh):
    "Test the `pty` option with string input and auto-EOF."
    cmd = "abc" | sh("cat").set(pty=True)
    result = await cmd

    if sys.platform == "linux":
        assert result == "abcabc"
    else:
        assert result == "abc^D\x08\x08^D\x08\x08abc"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cat_iteration_no_echo(sh):
    "Test the `pty` option with string input, iteration, and echo=False."

    cmd = "abc\ndef\nghi" | sh("cat").set(pty=canonical(echo=False))

    async with cmd.run() as run:
        lines = [line async for line in run]

    assert lines == ["abc\r\n", "def\r\n", "ghi"]


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_canonical_ls(sh):
    "Test canonical ls output through pty is in columns."
    cmd = sh("ls", "README.md", "CHANGELOG.md").set(
        pty=canonical(cols=20, rows=10, echo=False)
    )
    result = await cmd
    assert result == "CHANGELOG.md\r\nREADME.md\r\n"


@pytest.mark.xfail(_is_uvloop() or _is_codecov_linux(), reason="uvloop,codecov")
@pytest.mark.timeout(90)
async def test_pty_compare_large_ls_output(sh):
    "Compare pty output to non-pty output."
    cmd = sh("ls", "-l", "/usr/lib")
    regular_result = await cmd

    pty_result = await cmd.set(pty=True)
    pty_result = pty_result.replace("^D\x08\x08", "").replace("\r", "")

    assert pty_result == regular_result


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_compare_small_ls_output(sh):
    "Compare pty output to non-pty output."
    cmd = sh("ls", "README.md")
    regular_result = await cmd

    pty_result = await cmd.set(pty=True)
    pty_result = pty_result.replace("^D\x08\x08", "").replace("\r", "")

    assert pty_result == regular_result


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_stress_pty_canonical_ls_parallel(sh, report_children):
    "Test canonical ls output through pty is in columns (parallel stress test)."
    pty = canonical(cols=20, rows=10, echo=False)
    cmd = sh("ls", "README.md", "CHANGELOG.md").set(pty=pty)

    # Execute command 10 times in parallel.
    multi = [cmd] * 10
    _, results = await harvest_results(*multi, timeout=7.0)

    for result in results:
        assert result == "CHANGELOG.md\r\nREADME.md\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_stress_pty_canonical_ls_sequence(sh, report_children):
    "Test canonical ls output through pty is in columns (sequence stress test)."
    pty = canonical(cols=20, rows=10, echo=False)
    cmd = sh("ls", "README.md", "CHANGELOG.md").set(pty=pty)

    # Execute command 10 times sequentially.
    for _ in range(10):
        result = await cmd()
        assert result == "CHANGELOG.md\r\nREADME.md\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_timeout_fail(sh):
    "Test that a pty command can be called with a timeout."
    cmd = sh("sleep", "5").set(incomplete_result=True, pty=True)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, 0.2)

    if sys.platform == "linux":
        expected_output = b""
    else:
        expected_output = b"^D\x08\x08"

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        output_bytes=expected_output,
        exit_code=_CANCELLED_EXIT_CODE,
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_default_redirect_stderr(sh):
    "Test that pty redirects stderr to stdout."

    cmd = sh("ls", "DOES_NOT_EXIST")

    # Non-pty mode redirects stderr to /dev/null.
    result = await cmd.set(exit_codes={1, 2})
    assert result == ""

    # Pty mode redirects stderr to stdout.
    result = await cmd.set(exit_codes={1, 2}, pty=True)
    assert result.endswith("No such file or directory\r\n")
