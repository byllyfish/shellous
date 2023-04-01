"Unit tests for shellous module (Linux and MacOS)."

import asyncio
import contextlib
import io
import logging
import os
import re
import signal
import sys

import pytest

from shellous import PipeResult, Result, ResultError, cbreak, cooked, raw, sh
from shellous.harvest import harvest, harvest_results

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix")

_CANCELLED_EXIT_CODE = -15

# True if we're running on alpine linux.
_IS_ALPINE = os.path.exists("/etc/alpine-release")

# True if we're running on Python 3.10.9 or later.
_IS_PY3_NO_FD_COUNT = sys.version_info[:3] >= (3, 10, 9)


def _is_uvloop():
    "Return true if we're running under uvloop."
    return os.environ.get("SHELLOUS_LOOP_TYPE") == "uvloop"


def _is_codecov():
    "Return true if we're running code coverage."
    return os.environ.get("SHELLOUS_CODE_COVERAGE")


def _is_lsof_unsupported():
    "Return true if lsof tests are unsupported."
    return _IS_ALPINE or _is_uvloop() or _is_codecov()


def _readouterr(capfd):
    "Clean capfd output (see issue #151)."
    out, err = capfd.readouterr()
    out = re.sub(
        "^\\d+\\.\\d+ \x1b\\[35mDEBUG\x1b\\[0m.*\n",
        "",
        out,
        0,
        re.MULTILINE,
    )
    return (out, err)


@pytest.fixture
def ls():
    if sys.platform == "linux":
        return sh("ls", "--color=never")
    return sh("ls")


async def test_python():
    "Test running the python executable."
    result = await sh(sys.executable, "-c", "print('test1')")
    assert result == "test1\n"


async def test_echo():
    "Test running the echo command."
    result = await sh("echo", "-n", "foo")
    assert result == "foo"


async def test_echo_bytes():
    "Test running the echo command with bytes output (deprecated)."
    with pytest.raises(TypeError, match="encoding cannot be None"):
        await sh("echo", "-n", "foo").set(encoding=None)


async def test_echo_with_result():
    "Test running the echo command using the Result object."
    result = await sh("echo", "-n", "foo").set(return_result=True)
    assert result == Result(
        exit_code=0,
        output_bytes=b"foo",
        error_bytes=b"",
        cancelled=False,
        encoding="utf-8",
        extra=None,
    )
    assert result.output == "foo"


async def test_gh_100133():
    "Specific test for https://github.com/python/cpython/issues/100133"

    async def _run(cmd, *args):
        return (await sh(cmd, *args)).strip()

    outputs = [f"foo{i}" for i in range(10)]
    cmds = [_run("echo", out) for out in outputs]
    res = await asyncio.gather(*cmds)
    assert res == outputs


async def test_which():
    "Test running the `which` command."
    result = await sh("which", "cat")
    assert result in {"/usr/bin/cat\n", "/bin/cat\n"}


async def test_context_env():
    "Test running the env command with a custom environment context."
    env = {
        "PATH": "/bin:/usr/bin",
    }
    xsh = sh.env(**env).set(inherit_env=False)
    result = await xsh("env")
    assert result == "PATH=/bin:/usr/bin\n"


async def test_default_env():
    "Test running the env command with an augmented default environment."
    result = await sh("env").env(MORE="less")
    assert "MORE=less" in result.split("\n")


async def test_augmented_env():
    "Test running the env command with an augmented custom environment."
    env = {
        "PATH": "/bin:/usr/bin",
    }
    xsh = sh.env(**env).set(inherit_env=False)
    result = await xsh("env").env(MORE="less")
    assert sorted(result.rstrip().split("\n")) == [
        "MORE=less",
        "PATH=/bin:/usr/bin",
    ]


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_empty_env():
    "Test running the env command with an empty environment."
    result = await sh("/usr/bin/env").set(inherit_env=False)
    assert result == ""


async def test_custom_echo_func():
    "Test defining a custom echo function."

    def excited(*args):
        return sh("echo", "-n", *(args + ("!!",)))

    result = await excited("x", "z")
    assert result == "x z !!"


async def test_custom_echo_shorthand():
    "Test defining an echo function that always passes -n (shorthand)."
    echo = sh("echo", "-n")
    result = await echo("x", "y")
    assert result == "x y"


async def test_missing_executable():
    "Test invoking a non-existant command raises a FileNotFoundError."
    with pytest.raises(FileNotFoundError):
        await sh("/bin/does_not_exist")


async def test_task():
    "Test converting an awaitable command into an asyncio Task object."
    task = asyncio.create_task(sh("echo", "***").coro())
    result = await task
    assert result == "***\n"


async def test_task_cancel():
    "Test that we can cancel a running command task."
    task = asyncio.create_task(sh("sleep", "5").coro())
    await asyncio.sleep(0.1)

    # Cancel task and wait for it to exit.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_task_cancel_incomplete_result():
    "Test that we can cancel a running command task."
    task = asyncio.create_task(sh("sleep", "5").set(catch_cancelled_error=True).coro())
    await asyncio.sleep(0.1)

    # Cancel task and wait for it to exit.
    task.cancel()
    with pytest.raises(ResultError):
        await task


async def test_task_immediate_cancel():
    "Test that we can cancel a running command task."
    task = asyncio.create_task(sh("sleep", "5").coro())
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_timeout_fail():
    "Test that an awaitable command can be called with a timeout."
    cmd = sh("sleep", "5").set(catch_cancelled_error=True)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, 0.25)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        exit_code=_CANCELLED_EXIT_CODE,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_timeout_fail_no_capturing():
    "Test that an awaitable command can be called with a timeout."
    cmd = (
        sh("sleep", "5")
        .stdin(sh.DEVNULL)
        .stdout(sh.DEVNULL)
        .set(catch_cancelled_error=True)
    )

    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, 0.25)

    assert exc_info.value.result == Result(
        exit_code=_CANCELLED_EXIT_CODE,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_timeout_okay():
    "Test awaitable command that doesn't timeout."
    result = await asyncio.wait_for(sh("echo", "jjj"), 1.0)
    assert result == "jjj\n"


async def test_input():
    "Test calling a command with input string."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    result = await tr.stdin("some input")
    assert result == "SOME INPUT"


async def test_input_bytes():
    "Test calling a command with input bytes, and encoding is utf-8."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    result = await tr.stdin(b"some input")
    assert result == "SOME INPUT"


async def test_input_none_encoding():
    "Test calling a command with input string, but bytes encoding expected."
    with pytest.raises(TypeError, match="encoding cannot be None"):
        sh("tr", "[:lower:]", "[:upper:]").set(encoding=None)


async def test_exit_code_error():
    "Test calling a command that returns a non-zero exit status."
    with pytest.raises(ResultError) as exc_info:
        await sh("false")

    assert exc_info.value.result == Result(
        exit_code=1,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=False,
        encoding="utf-8",
        extra=None,
    )


async def test_redirect_output_path(tmp_path):
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


async def test_redirect_output_str(tmp_path):
    "Test redirecting command output to a filename string."
    out = tmp_path / "test_redirect_output_str"

    # Erase and write to file.
    with pytest.raises(TypeError, match="output file"):
        await sh("echo", "test", 4, 5, 6).stdout(str(out))


async def test_redirect_output_bytes(tmp_path):
    "Test redirecting command output to a filename string."
    out = tmp_path / "test_redirect_output_str"

    with pytest.raises(TypeError, match="output file"):
        await sh("echo", "test", 4, 5, 6).stdout(bytes(out))


async def test_redirect_output_file(tmp_path):
    "Test redirecting command output to a File-like object."
    out = tmp_path / "test_redirect_output_file"

    with open(out, "w") as fp:
        result = await sh("echo", "123").stdout(fp)
        assert result == ""

    assert out.read_bytes() == b"123\n"


async def test_redirect_output_stringio():
    "Test redirecting command output to a StringIO buffer."
    buf = io.StringIO()
    result = await sh("echo", "456").stdout(buf)
    assert result == ""
    assert buf.getvalue() == "456\n"


async def test_redirect_output_devnull():
    "Test redirecting command output to /dev/null."
    result = await sh("echo", "789").stdout(sh.DEVNULL)
    assert result == ""


async def test_redirect_output_stdout():
    "Test redirecting command output to STDOUT."
    with pytest.raises(ValueError, match="STDOUT is only supported by stderr"):
        await sh("echo", "789").stdout(sh.STDOUT)


async def test_redirect_output_none():
    "Test redirecting command output to None."
    with pytest.raises(TypeError, match="None"):
        await sh("echo", "789").stdout(None)


async def test_redirect_output_capture():
    "Test redirecting command output to CAPTURE."
    # FIXME: Should be an error.
    result = await sh("echo", "789").stdout(sh.CAPTURE)
    assert result == ""


async def test_redirect_output_inherit(capfd):
    "Test redirecting command output to INHERIT."
    result = await sh("echo", "789").stdout(sh.INHERIT)
    assert result == ""
    assert _readouterr(capfd) == ("789\n", "")


async def test_redirect_input_path(tmp_path):
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
sys.stderr.flush()
sys.stdout.write("goodbye!")
"""
    return sh(sys.executable, "-").stdin(script)


async def test_redirection(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    err = io.StringIO()
    result = await python_script().stderr(err)
    assert result == "hi stdout\ngoodbye!"
    assert err.getvalue() == "hi stderr\n"
    assert _readouterr(capfd) == ("", "")


async def test_redirect_output_to_devnull(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stdout(sh.DEVNULL)
    assert result == ""
    assert _readouterr(capfd) == ("", "")


async def test_redirect_output_to_inherit(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stdout(sh.INHERIT)
    assert result == ""
    assert _readouterr(capfd) == ("hi stdout\ngoodbye!", "")


async def test_redirect_error_to_stdout(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stderr(sh.STDOUT)
    assert result == "hi stdout\nhi stderr\ngoodbye!"
    assert _readouterr(capfd) == ("", "")


async def test_capture_error_only(python_script, capfd):
    "Test redirection options with stderr output."
    # FIXME: This should raise a ValueError...
    result = await python_script.stderr(sh.CAPTURE).stdout(sh.DEVNULL)
    assert result == ""
    assert _readouterr(capfd) == ("", "")


async def test_redirect_error_to_inherit(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stderr(sh.INHERIT)
    assert result == "hi stdout\ngoodbye!"
    assert _readouterr(capfd) == ("", "hi stderr\n")


async def test_redirect_error_to_devnull(python_script, capfd):
    "Test redirection options with both stdout and stderr output."
    result = await python_script.stderr(sh.DEVNULL)
    assert result == "hi stdout\ngoodbye!"
    assert _readouterr(capfd) == ("", "")


async def test_redirect_error_to_capture(python_script):
    "Test using CAPTURE when not using `async with`."
    with pytest.raises(ValueError, match="multiple capture requires 'async with'"):
        await python_script.stdout(sh.CAPTURE).stderr(sh.CAPTURE)


async def test_redirect_error_to_logger(python_script, capfd, caplog):
    "Test redirection options with both stdout and stderr output (logger)."
    test_logger = logging.getLogger("test_logger")
    result = await python_script.stderr(test_logger)
    assert result == "hi stdout\ngoodbye!"
    assert _readouterr(capfd) == ("", "")

    logs = [tup for tup in caplog.record_tuples if tup[0] == "test_logger"]
    assert logs == [
        ("test_logger", 40, "hi stderr"),
    ]


async def test_async_context_manager():
    "Use `async with` to read/write bytes incrementally."
    tr = sh("tr", "[:lower:]", "[:upper:]").stdin(sh.CAPTURE)

    async with tr.stdout(sh.CAPTURE).run() as run:
        assert run.stderr is None

        # N.B. We won't deadlock writing/reading a single byte.
        run.stdin.write(b"a")
        run.stdin.close()
        result = await run.stdout.read()

    assert result == b"A"


async def test_async_iteration():
    "Use `async for` to read stdout line by line."
    echo = (
        sh("echo", "-n", "line1\n", "line2\n", "line3")
        .stdout(sh.CAPTURE)
        .stderr(sh.DEVNULL)
    )
    async with echo.run() as run:
        result = [line async for line in run]
    assert result == ["line1\n", " line2\n", " line3"]


async def test_manually_created_pipeline():
    "You can create a pipeline manually using input redirection and os.pipe()."
    (r, w) = os.pipe()
    echo = sh("echo", "-n", "abc").stdout(w, close=True)
    tr = sh("tr", "[:lower:]", "[:upper:]").stdin(r, close=True)
    result = await asyncio.gather(tr, echo)
    assert result == ["ABC", ""]


async def test_pipeline():
    "Test a simple pipeline."
    pipe = sh("echo", "-n", "xyz") | sh("tr", "[:lower:]", "[:upper:]")
    result = await pipe
    assert result == "XYZ"


async def test_pipeline_with_env():
    "Test a simple pipeline with an augmented environment."
    pipe = sh("echo", "-n", "xyz").env(FOO=1) | sh("tr", "[:lower:]", "[:upper:]")
    result = await pipe
    assert result == "XYZ"


async def test_pipeline_with_result():
    "Test a simple pipeline with `return_result` set to True."
    echo = sh("echo", "-n", "xyz")
    tr = sh("tr", "[:lower:]", "[:upper:]").set(return_result=True)
    result = await (echo | tr)
    assert result == Result(
        exit_code=0,
        output_bytes=b"XYZ",
        error_bytes=b"",
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


async def test_pipeline_single_cmd():
    "Test a single command pipeline."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    result = await ("abc" | tr)
    assert result == "ABC"


async def test_pipeline_invalid_cmd1():
    pipe = sh(" non_existant ", "xyz") | sh("tr", "[:lower:]", "[:upper:]")
    # uvloop's error message does not include filename.
    with pytest.raises(FileNotFoundError):
        await pipe


async def test_pipeline_invalid_cmd2():
    pipe = sh("echo", "abc") | sh(" non_existant ", "xyz")
    # uvloop's error message does not include filename.
    with pytest.raises(FileNotFoundError):
        await pipe


async def test_exit_codes():
    "Test `allows_exit_codes` option."
    xsh = sh.stderr(sh.STDOUT).set(exit_codes={1})
    cmd = xsh("cat", "/tmp/__does_not_exist__")
    result = await cmd
    # "cat" may be displayed as "/usr/bin/cat" on some systems.
    assert re.fullmatch(
        r".*cat: .*__does_not_exist__'?: No such file or directory\n", result
    )


async def test_pipeline_async_iteration():
    "Use `async for` to read stdout line by line."
    echo = sh("echo", "-n", "line1\n", "line2\n", "line3")
    cat = sh("cat").stdout(sh.CAPTURE)
    async with (echo | cat).run() as run:
        result = [line async for line in run]
    assert result == ["line1\n", " line2\n", " line3"]


async def test_pipeline_async_context_manager():
    "Use `async with` to read/write bytes incrementally."
    tr = sh("tr", "[:lower:]", "[:upper:]")
    pipe = (tr | sh("cat")).stdin(sh.CAPTURE)
    async with pipe.stdout(sh.CAPTURE).run() as run:
        assert run.stderr is None

        # N.B. We won't deadlock writing/reading a single byte.
        run.stdin.write(b"a")
        run.stdin.close()
        result = await run.stdout.read()

    assert result == b"A"
    assert run.name == "tr|cat"
    assert (
        repr(run) == "<PipeRunner 'tr|cat' results=["
        "Result(exit_code=0, output_bytes=b'', error_bytes=b'', cancelled=False, encoding='utf-8', extra=None), "
        "Result(exit_code=0, output_bytes=b'', error_bytes=b'', cancelled=False, encoding='utf-8', extra=None)]>"
    )


async def test_gather_same_cmd():
    """Test passing the same cmd to harvest().

    This test fails with `asyncio.gather`.
    """
    cmd = sh(sys.executable, "-c", "import secrets; print(secrets.randbits(31))")
    results = await harvest_results(cmd, cmd)
    assert results[0] != results[1]


async def test_cancelled_antipattern():
    """Test the ResultError/cancellation anti-pattern.

    This occurs *only* when catch_cancelled_error=True.

    Catching `ResultError` using try/except conceals the `CancelledError`
    when you want to cancel the current task.
    """

    sleep_cmd = sh("sleep", 3600).set(return_result=True, catch_cancelled_error=True)

    async def _subtask():
        try:
            await sleep_cmd
        except ResultError as ex:
            # sleep_cmd has catch_cancelled_error set to True.
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


async def test_cancelled_antipattern_fix():
    """Test the ResultError/cancellation anti-pattern fix.

    Catching `ResultError` using try/except conceals the `CancelledError`
    when you want to cancel the current task.

    The fix is to explicitly re-raise the CancelledError when you are done with
    the `ResultError`.
    """

    sleep_cmd = sh("sleep", 3600).set(return_result=True, catch_cancelled_error=True)

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


@pytest.mark.skipif(_IS_ALPINE, reason="test hangs on alpine")
async def test_multiple_capture():
    "Test the multiple capture example from the documentation."
    cmd = sh("cat").stdin(sh.CAPTURE)

    async with cmd.stdout(sh.CAPTURE).run() as run:
        run.stdin.write(b"abc\n")
        output, _ = await asyncio.gather(run.stdout.readline(), run.stdin.drain())
        run.stdin.close()

    result = run.result()
    assert result == Result(
        exit_code=0,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=False,
        encoding="utf-8",
        extra=None,
    )


async def test_multiple_capture_alpine():
    "Test alternate implementation of test_multiple_capture to see if it hangs."
    result = await sh("cat").stdin(b"abc\n")
    assert result == "abc\n"


async def test_cancel_timeout():
    "Test the `cancel_timeout` setting."
    sleep = sh("nohup", "sleep").set(
        cancel_timeout=0.25,
        cancel_signal=signal.SIGHUP,
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sleep(10.0), 0.25)


async def test_shell_cmd():
    "Test a shell command.  (https://bugs.python.org/issue43884)"
    shell = sh("/bin/sh", "-c").set(
        return_result=True,
        catch_cancelled_error=True,
        exit_codes={0, _CANCELLED_EXIT_CODE},
    )

    task = asyncio.create_task(shell("sleep 2 && echo done").coro())
    await asyncio.sleep(0.25)
    task.cancel()

    with pytest.raises(ResultError) as exc_info:
        await task

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        exit_code=_CANCELLED_EXIT_CODE,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


async def test_large_cat():
    "Test cat with some decent sized data."
    data = b"x" * (4 * 1024 * 1024 + 1)
    cat = sh("cat")
    result = await (data | cat).result
    assert len(result.output_bytes) == len(data)
    assert result.output_bytes == data


async def test_env_ellipsis_unix():
    "Test using `...` in env method to grab value from global environment."
    cmd = sh("env").set(inherit_env=False).env(PATH=...)
    result = await cmd
    assert result.startswith("PATH=")


async def test_env_ellipsis_unix_wrong_case():
    "Test using `...` in env method to grab value from global environment."

    # Fails because actual env is named "PATH", not "path"
    with pytest.raises(KeyError):
        sh("env").set(inherit_env=False).env(path=...)


async def test_process_substitution():
    """Test process substitution.

    ```
    diff <(echo a) <(echo b)
    ```
    """

    cmd = sh("diff", sh("echo", "a"), sh("echo", "b")).set(exit_codes={0, 1})
    result = await cmd

    if _IS_ALPINE:
        assert result.endswith("@@ -1 +1 @@\n-a\n+b\n")
    else:
        assert result == "1c1\n< a\n---\n> b\n"


async def test_process_substitution_with_pipe():
    """Test process substitution.

    ```
    diff <(echo a | cat) <(echo b | cat)
    ```
    """

    pipe1 = sh("echo", "a") | sh("cat")
    pipe2 = sh("echo", "b") | sh("cat")
    cmd = sh("diff", pipe1, pipe2).set(exit_codes={0, 1})
    result = await cmd

    if _IS_ALPINE:
        assert result.endswith("@@ -1 +1 @@\n-a\n+b\n")
    else:
        assert result == "1c1\n< a\n---\n> b\n"


async def test_process_substitution_write(tmp_path):
    """Test process substitution with write_mode.

    ```
    echo a | tee >(cat > tmpfile)
    ```
    """

    out = tmp_path / "test_process_sub_write"
    cat = sh("cat") | out
    pipe = sh("echo", "a") | sh("tee", cat().writable)

    result = await pipe
    assert result == "a\n"
    assert out.read_bytes() == b"a\n"


async def test_process_substitution_write_pipe(tmp_path):
    """Test process substitution with write_mode.

    ```
    echo b | tee >(cat | cat > tmpfile)
    ```
    """

    out = tmp_path / "test_process_sub_write"
    cat = sh("cat") | sh("cat") | out
    pipe = sh("echo", "b") | sh("tee", cat().writable)

    result = await pipe
    assert result == "b\n"
    assert out.read_bytes() == b"b\n"


async def test_process_substitution_write_pipe_alt(tmp_path):
    """Test process substitution with write_mode.

    Change where the .writable appears; put it on the first command of the pipe.

    ```
    echo b | tee >(cat | cat > tmpfile)
    ```
    """

    out = tmp_path / "test_process_sub_write"
    cat = sh("cat").writable | sh("cat") | out
    pipe = sh("echo", "b") | sh("tee", cat()).stderr(sh.INHERIT)

    result = await pipe
    assert result == "b\n"
    assert out.read_bytes() == b"b\n"


async def test_process_substitution_error_filenotfound():
    """Test process substitution with FileNotFoundError error."""

    cmd = sh("diff", sh("echo", "a"), sh("_unknown_", "b")).set(exit_codes={0, 1})

    with pytest.raises(FileNotFoundError, match="_unknown_"):
        await cmd


async def test_process_substitution_error_exit_1():
    """Test process substitution with FileNotFoundError error."""

    # sleep exits with code 1 if no argument passed.
    cmd = sh("diff", sh("echo", "a"), sh("sleep")).set(exit_codes={0, 1})

    with pytest.raises(ResultError) as exc_info:
        await cmd

    result = exc_info.value.result

    assert result.output_bytes == b""
    assert result.exit_code == 1
    assert result.cancelled is False
    assert result.encoding == "utf-8"
    assert result.extra is None  # FIXME: This should indicate that "sleep" failed...
    assert b"sleep" in result.error_bytes


async def test_start_new_session():
    """Test `_start_new_session` option."""

    script = """import os; print(os.getsid(0) == os.getpid())"""
    cmd = sh(sys.executable, "-c", script)

    result = await cmd
    assert result == "False\n"

    result = await cmd.set(_start_new_session=False)
    assert result == "False\n"

    result = await cmd.set(_start_new_session=True)
    assert result == "True\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_manual():
    """Test setting up a pty manually."""

    import pty  # import not supported on windows

    parent_fd, child_fd = pty.openpty()

    tr = sh("tr", "[:lower:]", "[:upper:]")
    cmd = (
        tr.stdin(child_fd, close=True)
        .stdout(child_fd, close=True)
        .set(_start_new_session=True)
    )

    async with cmd.run():
        # Use synchronous functions to test pty directly.
        os.write(parent_fd, b"abc\n")
        await asyncio.sleep(0.1)
        result = os.read(parent_fd, 1024)
        os.close(parent_fd)

    assert result == b"abc\r\nABC\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_manual_ls(ls):
    """Test setting up a pty manually."""

    import pty  # import not supported on windows

    import shellous
    from shellous.log import log_timer

    parent_fd, child_fd = pty.openpty()
    ttyname = os.ttyname(child_fd)

    cmd = (
        ls("README.md")
        .stdin(child_fd, close=False)
        .stdout(child_fd, close=False)
        .set(
            _start_new_session=True,
            _preexec_fn=lambda: shellous.pty_util.set_ctty(ttyname),
        )
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
async def test_pty_manual_streams():
    """Test setting up a pty manually."""

    import pty  # import not supported on windows

    parent_fd, child_fd = pty.openpty()

    tr = sh("tr", "[:lower:]", "[:upper:]")
    cmd = (
        tr.stdin(child_fd, close=True)
        .stdout(child_fd, close=True)
        .set(_start_new_session=True)
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
async def test_pty():
    "Test the `pty` option."
    cmd = sh("tr", "[:lower:]", "[:upper:]").stdin(sh.CAPTURE).set(pty=True)

    async with cmd.stdout(sh.CAPTURE).run() as run:
        run.stdin.write(b"abc\n")
        await run.stdin.drain()
        await asyncio.sleep(0.1)
        result = await run.stdout.read(1024)
        run.stdin.close()

    assert result == b"abc\r\nABC\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_ctermid():
    "Test the `pty` option and print out the ctermid, ttyname for stdin/stdout."
    cmd = (
        sh(
            sys.executable,
            "-c",
            "import os; print(os.ctermid(), os.ttyname(0), os.ttyname(1))",
        )
        .stdin(sh.CAPTURE)
        .set(pty=True)
    )

    async with cmd.stdout(sh.CAPTURE).run() as run:
        result = await run.stdout.read(1024)
        run.stdin.close()

    ctermid, stdin_tty, stdout_tty = result.split()

    print(ctermid, stdin_tty, stdout_tty)
    assert re.fullmatch(rb"/dev/(?:ctty|tty|pts/\d+)", ctermid), ctermid
    assert re.fullmatch(rb"/dev/(?:ttys|pts/)\d+", stdin_tty), stdin_tty
    assert re.fullmatch(rb"/dev/(?:ttys|pts/)\d+", stdout_tty), stdout_tty


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


@pytest.mark.xfail(_is_uvloop() or _IS_ALPINE, reason="uvloop,alpine")
async def test_pty_stty_all(tmp_path):
    "Test the `pty` option and print out the result of stty -a"

    err = tmp_path / "test_pty_stty_all"
    cmd = sh("stty", "-a").stdin(sh.CAPTURE).stderr(err).set(pty=True)

    buf = b""
    async with cmd.stdout(sh.CAPTURE).run() as run:
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
async def test_pty_tr_eot():
    "Test the `pty` option with a control-D (EOT = 0x04)"
    cmd = sh("tr", "[:lower:]", "[:upper:]").stdin(sh.CAPTURE).set(pty=True)

    async with cmd.stdout(sh.CAPTURE).run() as run:
        run.stdin.write(b"abc\n\x04")
        await run.stdin.drain()
        await asyncio.sleep(0.1)
        result = await run.stdout.read(1024)

    if sys.platform == "linux":
        assert result == b"abc\r\nABC\r\n"
    else:
        assert result == b"abc\r\n^D\x08\x08ABC\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cat_eot():
    "Test the `pty` option with a control-D (EOT = 0x04)"
    cmd = sh("cat").stdin(sh.CAPTURE).set(pty=True)

    async with cmd.stdout(sh.CAPTURE).run() as run:
        run.stdin.write(b"abc\x04\x04")
        await run.stdin.drain()
        await asyncio.sleep(0.1)
        result = await run.stdout.read(1024)

    if sys.platform == "linux":
        assert result == b"abcabc"
    else:
        assert result == b"abc^D\x08\x08^D\x08\x08abc"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_raw_size():
    "Test the `pty` option in raw mode."

    cmd = sh("stty", "size").set(pty=raw(rows=17, cols=41))
    result = await cmd
    assert result == "17 41\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cbreak_size():
    "Test the `pty` option in cbreak mode."

    cmd = sh("stty", "size").set(pty=cbreak(rows=19, cols=43))
    result = await cmd
    assert result == "19 43\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_raw_ls(ls):
    "Test the `pty` option in raw mode."

    cmd = ls.set(pty=raw(rows=24, cols=40)).stderr(sh.STDOUT)
    result = await cmd

    assert "README.md" in result
    for line in io.StringIO(result):
        assert len(line.rstrip()) <= 40


@pytest.mark.xfail(_is_uvloop() or _IS_ALPINE, reason="uvloop,alpine")
async def test_pty_raw_size_inherited():
    "Test the `pty` option in raw mode."

    cmd = sh("stty", "size").set(pty=raw(rows=..., cols=...))
    result = await cmd

    rows, cols = (int(n) for n in result.rstrip().split())
    assert rows >= 0 and cols >= 0


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cat_auto_eof():
    "Test the `pty` option with string input and auto-EOF."
    cmd = "abc" | sh("cat").set(pty=True)
    result = await cmd

    if sys.platform == "linux":
        assert result == "abcabc"
    else:
        assert result == "abc^D\x08\x08^D\x08\x08abc"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cat_iteration_no_echo():
    "Test the `pty` option with string input, iteration, and echo=False."

    cmd = "abc\ndef\nghi" | sh("cat").set(pty=cooked(echo=False))

    async with cmd.stdout(sh.CAPTURE).run() as run:
        lines = [line async for line in run]

    assert lines == ["abc\r\n", "def\r\n", "ghi"]


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_canonical_ls(ls):
    "Test canonical ls output through pty is in columns."
    cmd = ls("README.md", "CHANGELOG.md").set(pty=cooked(cols=20, rows=10, echo=False))
    result = await cmd
    assert result == "CHANGELOG.md\r\nREADME.md\r\n"


@pytest.mark.skipif(_is_uvloop() or _is_codecov(), reason="uvloop,codecov")
@pytest.mark.timeout(90)
async def test_pty_compare_large_ls_output(ls):
    "Compare pty output to non-pty output (with large output)."
    cmd = ls("-l", "/usr/lib")
    regular_result = await cmd

    pty_result = await cmd.set(pty=True)
    pty_result = pty_result.replace("^D\x08\x08", "").replace("\r", "")

    assert pty_result == regular_result


@pytest.mark.skipif(_is_uvloop() or _is_codecov(), reason="uvloop,codecov")
@pytest.mark.timeout(90)
async def test_pty_compare_huge_ls_output(ls):
    "Compare pty output to non-pty output (with huge recursive output)."
    cmd = ls("-lr", "/usr/lib")
    regular_result = await cmd

    pty_result = await cmd.set(pty=True)
    pty_result = pty_result.replace("^D\x08\x08", "").replace("\r", "")

    assert pty_result == regular_result


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_compare_small_ls_output(ls):
    "Compare pty output to non-pty output."
    cmd = ls("README.md")
    regular_result = await cmd

    pty_result = await cmd.set(pty=True)
    pty_result = pty_result.replace("^D\x08\x08", "").replace("\r", "")

    assert pty_result == regular_result


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_stress_pty_canonical_ls_parallel(ls, report_children):
    "Test canonical ls output through pty is in columns (parallel stress test)."
    pty = cooked(cols=20, rows=10, echo=False)
    cmd = ls("README.md", "CHANGELOG.md").set(pty=pty)

    # Execute command 10 times in parallel.
    multi = [cmd] * 10
    _, results = await harvest_results(*multi, timeout=7.0)

    for result in results:
        assert result == "CHANGELOG.md\r\nREADME.md\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_stress_pty_canonical_ls_sequence(ls, report_children):
    "Test canonical ls output through pty is in columns (sequence stress test)."
    pty = cooked(cols=20, rows=10, echo=False)
    cmd = ls("README.md", "CHANGELOG.md").set(pty=pty)

    # Execute command 10 times sequentially.
    for _ in range(10):
        result = await cmd()
        assert result == "CHANGELOG.md\r\nREADME.md\r\n"


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_timeout_fail():
    "Test that a pty command can be called with a timeout."
    cmd = sh("sleep", "5").set(catch_cancelled_error=True, pty=True)
    with pytest.raises(ResultError) as exc_info:
        await asyncio.wait_for(cmd, 0.25)

    assert exc_info.type is ResultError
    assert exc_info.value.result == Result(
        exit_code=_CANCELLED_EXIT_CODE,
        output_bytes=b"",
        error_bytes=b"",
        cancelled=True,
        encoding="utf-8",
        extra=None,
    )


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_default_redirect_stderr(ls):
    "Test that pty redirects stderr to stdout."

    cmd = ls("DOES_NOT_EXIST")

    # Non-pty mode redirects stderr to /dev/null.
    result = await cmd.set(exit_codes={1, 2})
    assert result == ""

    # Pty mode redirects stderr to stdout.
    result = await cmd.set(exit_codes={1, 2}, pty=True)
    assert result.endswith("No such file or directory\r\n")

    # Test that pty's runner.stdin is still available...
    async with cmd.stdout(sh.CAPTURE).set(exit_codes={1, 2}, pty=True).run() as run:
        assert run.stdin is not None
        result = await run.stdout.read()
        assert result.endswith(b"No such file or directory\r\n")


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_cat_hangs():
    "Test that cat under pty hangs because we don't send EOF."

    cmd = sh("cat")

    # Non-pty mode we read from b"".
    result = await cmd
    assert result == ""

    # Pty mode reads from CAPTURE; which causes cat to hang.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(cmd.set(pty=True), 2.0)


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_separate_stderr(caplog):
    "Test that stderr can be separately redirected from stdout with pty."

    script = """
import sys
print('test1', flush=True)
print('__test2__', file=sys.stderr, flush=True)
"""
    cmd = (
        sh(sys.executable, "-c", script)
        .set(pty=True)
        .stderr(logging.getLogger("test_logger"))
    )

    result = await cmd
    assert result == "test1\r\n"

    test_logs = [entry for entry in caplog.record_tuples if entry[0] == "test_logger"]
    assert len(test_logs) == 1
    assert "__test2__" in test_logs[0][2]


async def test_redirect_stdin_streamreader():
    "Test reading stdin from StreamReader."

    def _hello(_reader, writer):
        writer.write(b"hello")
        writer.close()

    try:
        sock_path = "/tmp/__streamreader__"
        server = await asyncio.start_unix_server(_hello, sock_path)

        reader, writer = await asyncio.open_unix_connection(sock_path)
        result = await sh("cat").stdin(reader)
        assert result == "hello"

    finally:
        writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_redirect_stdin_streamreader():
    "Test reading stdin from StreamReader."

    def _hello(_reader, writer):
        writer.write(b"hello\n")
        writer.close()

    try:
        sock_path = "/tmp/__streamreader__"
        server = await asyncio.start_unix_server(_hello, sock_path)

        reader, writer = await asyncio.open_unix_connection(sock_path)
        result = await (reader | sh("cat").set(pty=True))
        assert result.replace("^D\x08\x08", "") == "hello\r\nhello\r\n"

    finally:
        writer.close()
        server.close()
        await server.wait_closed()


async def test_redirect_stdout_streamwriter():
    "Test writing stdout to a StreamWriter."

    buf = io.BytesIO()

    async def _hello(reader, _writer):
        buf.write(await reader.read())
        _writer.close()

    try:
        sock_path = "/tmp/__streamwriter__"
        server = await asyncio.start_unix_server(_hello, sock_path)

        _reader, writer = await asyncio.open_unix_connection(sock_path)
        result = await sh("echo", "hello").stdout(writer)
        assert result == ""
        assert buf.getvalue() == b"hello\n"

    finally:
        writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_pty_redirect_stdout_streamwriter():
    "Test writing stdout to a StreamWriter."

    buf = io.BytesIO()

    async def _hello(reader, _writer):
        buf.write(await reader.read())
        _writer.close()

    try:
        sock_path = "/tmp/__streamwriter__"
        server = await asyncio.start_unix_server(_hello, sock_path)

        _reader, writer = await asyncio.open_unix_connection(sock_path)
        result = await (sh("echo", "hello").set(pty=True) | writer)
        assert result == ""
        assert buf.getvalue() == b"hello\r\n"

    finally:
        writer.close()
        server.close()
        await server.wait_closed()


async def test_audit_cancel_nohup():
    "Test audit callback when a command is cancelled."

    calls = []

    def _audit(phase, info):
        runner = info["runner"]
        signal = info.get("signal")
        calls.append((phase, runner.name, runner.returncode, runner.cancelled, signal))

    xsh = sh.set(
        cancel_signal=signal.SIGHUP,
        cancel_timeout=0.2,
        audit_callback=_audit,
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(xsh("nohup", "sleep", "10"), timeout=0.25)

    assert calls == [
        ("start", "nohup", None, False, None),
        ("signal", "nohup", None, True, "SIGHUP"),
        ("signal", "nohup", None, True, "SIGKILL"),
        ("stop", "nohup", -9, True, None),
    ]


async def test_set_cancel_signal_invalid():
    "Test audit callback when a command is cancelled."

    calls = []

    def _audit(phase, info):
        runner = info["runner"]
        signal = info.get("signal")
        calls.append((phase, runner.name, runner.returncode, runner.cancelled, signal))

    xsh = sh.set(
        cancel_signal="INVALID",
        cancel_timeout=0.2,
        audit_callback=_audit,
    )

    with pytest.raises(TypeError):
        await asyncio.wait_for(xsh("nohup", "sleep", "10"), timeout=0.25)

    assert calls == [
        ("start", "nohup", None, False, None),
        ("signal", "nohup", None, True, "INVALID"),
        ("signal", "nohup", None, True, "SIGKILL"),
        ("stop", "nohup", -9, True, None),
    ]


async def test_timeout_and_wait_for():
    "Test combinining `asyncio.wait_for` with timeout and cancel_timeout."

    cmd = sh("nohup", "sleep", 10).set(
        timeout=0.5,
        cancel_signal=signal.SIGHUP,
        cancel_timeout=3.0,
    )

    with pytest.raises(asyncio.TimeoutError):
        # Process will be cancelled after sending SIGHUP and while waiting
        # for `cancel_timeout` to expire.
        await asyncio.wait_for(cmd, 1.0)


# Grab the FD(4), TYPE(5) and NAME(9) columns only where the FD starts with
# an integer. Replace any integer /dev/tty numbers in NAME with `N`.
_AWK_SCRIPT = """
$4 ~ /^[0-9]+/ { sub(/[0-9]+/, "N", $9); print $4, $5, $9 }
"""


@pytest.mark.skipif(_is_lsof_unsupported(), reason="uvloop,codecov,alpine")
async def test_open_file_descriptors():
    "Test what file descriptors are open in the subprocess."

    cmd = sh("cat").stdin(sh.CAPTURE).stderr(sh.DEVNULL)
    lsof = sh("lsof", "-n", "-P", "-p").stderr(sh.STDOUT)
    awk = sh("awk", _AWK_SCRIPT).stderr(sh.STDOUT)

    async with cmd.set(close_fds=True) as run:
        result = await (lsof(run.pid) | awk)
        run.stdin.close()

    if sys.platform == "linux":
        assert result in (
            "0u unix type=STREAM\n1w FIFO pipe\n2u CHR /dev/null\n",
            "0r FIFO pipe\n1w FIFO pipe\n2u CHR /dev/null\n",  # py3.11/18.04
        )
    elif sys.platform.startswith("freebsd"):
        assert result == "0u unix \n1u PIPE \n2u VCHR /dev/null\n"
    else:
        assert result in (
            "0u unix \n1 PIPE \n2u CHR /dev/null\n",
            "0 PIPE \n1 PIPE \n2u CHR /dev/null\n",
        )


@pytest.mark.skipif(_is_lsof_unsupported(), reason="uvloop,codecov,alpine")
async def test_open_file_descriptors_unclosed_fds():
    "Test what file descriptors are open in the subprocess (close_fds=False)."

    cmd = sh("cat").stdin(sh.CAPTURE).stderr(sh.DEVNULL)
    lsof = sh("lsof", "-n", "-P", "-p").stderr(sh.STDOUT)
    awk = sh("awk", _AWK_SCRIPT).stderr(sh.STDOUT)

    async with cmd.set(close_fds=False) as run:
        result = await (lsof(run.pid) | awk)
        run.stdin.close()

    if sys.platform == "linux":
        assert result in (
            "0u unix type=STREAM\n1w FIFO pipe\n2u CHR /dev/null\n",
            "0r FIFO pipe\n1w FIFO pipe\n2u CHR /dev/null\n",  # py3.11/18.04
        )
    elif sys.platform.startswith("freebsd"):
        assert result == "0u unix \n1u PIPE \n2u VCHR /dev/null\n"
    else:
        assert result in (
            "0u unix \n1 PIPE \n2u CHR /dev/null\n",
            "0 PIPE \n1 PIPE \n2u CHR /dev/null\n",
        )


@pytest.mark.skipif(_is_lsof_unsupported(), reason="uvloop,codecov,alpine")
async def test_open_file_descriptors_pty():
    "Test what file descriptors are open in the pty subprocess."

    cmd = sh("cat").stdin(sh.CAPTURE)
    lsof = sh("lsof", "-n", "-P", "-p").stderr(sh.STDOUT)
    awk = sh("awk", _AWK_SCRIPT).stderr(sh.STDOUT)

    async with cmd.set(close_fds=True, pty=True) as run:
        result = await (lsof(run.pid) | awk)
        run.stdin.close()

    if sys.platform == "linux":
        assert result == "0u CHR /dev/pts/N\n1u CHR /dev/pts/N\n2u CHR /dev/pts/N\n"
    elif sys.platform.startswith("freebsd"):
        assert result in (
            "0u VCHR /dev/pts/N\n1u VCHR /dev/pts/N\n2u VCHR /dev/pts/N\n",
            "0u VCHR /dev\n1u VCHR /dev\n2u VCHR /dev\n",
        )
    else:
        assert result == "0u CHR /dev/ttysN\n1u CHR /dev/ttysN\n2u CHR /dev/ttysN\n"


@pytest.mark.skipif(_is_lsof_unsupported(), reason="uvloop,codecov,alpine")
async def test_open_file_descriptors_pty_unclosed_fds():
    "Test what file descriptors are open in the pty (close_fds=False)."

    cmd = sh("cat").stdin(sh.CAPTURE)
    lsof = sh("lsof", "-n", "-P", "-p").stderr(sh.STDOUT)
    awk = sh("awk", _AWK_SCRIPT).stderr(sh.STDOUT)

    async with cmd.set(close_fds=False, pty=True) as run:
        result = await (lsof(run.pid) | awk)
        run.stdin.close()

    if sys.platform == "linux":
        assert result == "0u CHR /dev/pts/N\n1u CHR /dev/pts/N\n2u CHR /dev/pts/N\n"
    elif sys.platform.startswith("freebsd"):
        assert result in (
            "0u VCHR /dev/pts/N\n1u VCHR /dev/pts/N\n2u VCHR /dev/pts/N\n",
            "0u VCHR /dev\n1u VCHR /dev\n2u VCHR /dev\n",
        )
    else:
        assert result == "0u CHR /dev/ttysN\n1u CHR /dev/ttysN\n2u CHR /dev/ttysN\n"


@contextlib.contextmanager
def _limited_descriptors(limit):
    "Context manager to limit file descriptors in this process."
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


@pytest.mark.skipif(_is_uvloop() or _IS_PY3_NO_FD_COUNT, reason="uvloop/no_fd_count")
async def test_limited_file_descriptors(report_children):
    "Test running out of file descriptors."
    cmds = [sh("sleep", "1").stderr(sh.DEVNULL)] * 2

    with _limited_descriptors(13):
        with pytest.raises(
            OSError, match="Too many open files|No file descriptors available"
        ):
            await harvest(*cmds)

    # Yield time for any killed processes to be reaped.
    await asyncio.sleep(0.025)


async def test_simultaneous_context_manager():
    "Test multiple simultaneous context managers."

    async with sh("sleep", 3) as sleep1:
        async with sh("sleep", 2) as sleep2:
            await asyncio.sleep(0.1)
            sleep2.send_signal(signal.SIGTERM)
        await asyncio.sleep(0.1)
        sleep1.cancel()


async def test_context_manager_running():
    "Test context manager updates the running status of a process."

    async with sh("sleep", 3) as sleep1:
        await asyncio.sleep(0.1)
        sleep1.cancel()
        await asyncio.sleep(0.1)
        assert sleep1.returncode == _CANCELLED_EXIT_CODE


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_context_manager_running_pty():
    "Test context manager in pty mode may NOT update running status of process."

    async with sh("sleep", 3).stdout(sh.CAPTURE).set(pty=True) as sleep1:
        await asyncio.sleep(0.1)
        sleep1.cancel()
        await asyncio.sleep(0.1)

        if sys.platform == "linux":
            assert sleep1.returncode == _CANCELLED_EXIT_CODE
        else:
            # Note: PTY mode disables child watcher on MacOS/FreeBSD.
            assert sleep1.returncode is None
