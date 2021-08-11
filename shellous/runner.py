"Implements utilities to run a command."

import asyncio
import functools
import io
import logging
import os
import sys

from shellous.result import Result, make_result
from shellous.util import Redirect, decode

LOGGER = logging.getLogger(__name__)


class RunOptions:
    """RunOptions is context manager to assist in running a command.

    This class set up low-level I/O redirection and helps close open file
    descriptors.

    ```
    with RunOptions(cmd) as options:
        proc = await create_subprocess_exec(*options.args, **options.kwd_args)
        # etc.
    ```
    """

    def __init__(self, command):
        self.command = command
        self.encoding = None
        self.open_fds = []
        self.input_bytes = None
        self.args = None
        self.kwd_args = None

    def close_fds(self):
        "Close all open file descriptors in `open_fds`."
        _close_fds(self.open_fds)

    def __enter__(self):
        context = self.command.context
        options = self.command.options

        stdin, input_bytes = self._setup_input(
            options.input, options.input_close, context.encoding
        )

        stdout = self._setup_output(
            options.output,
            options.output_append,
            options.output_close,
            sys.stdout,
        )

        stderr = self._setup_output(
            options.error,
            options.error_append,
            options.error_close,
            sys.stderr,
        )

        self.encoding = context.encoding
        self.input_bytes = input_bytes
        self.args = self.command.args
        self.kwd_args = {
            "stdin": stdin,
            "stdout": stdout,
            "stderr": stderr,
            "env": options.merge_env(context.env),
        }

        return self

    def __exit__(self, *exc):
        "Make sure those file descriptors are cleaned up."
        self.close_fds()

    def _setup_input(self, input_, close, encoding):
        "Set up process input."
        stdin = asyncio.subprocess.PIPE
        input_bytes = None

        if input_ is None or isinstance(input_, bytes):
            input_bytes = input_
        elif isinstance(input_, os.PathLike):
            stdin = open(input_, "rb")
            self.open_fds.append(stdin)
        elif isinstance(input_, int):  # file descriptor
            stdin = input_
            if close:
                self.open_fds.append(stdin)
        else:
            if encoding is None:
                raise TypeError("when encoding is None, input must be bytes")
            input_bytes = input_.encode(encoding)

        return stdin, input_bytes

    def _setup_output(self, output, append, close, sys_stream):
        "Set up process output. Used for both stdout and stderr."
        stdout = asyncio.subprocess.PIPE

        if output is not None:
            if isinstance(output, (str, bytes, os.PathLike)):
                mode = "ab" if append else "wb"
                # FIXME: we really just need the file descriptor...
                stdout = open(output, mode=mode)
                self.open_fds.append(stdout)
            elif isinstance(output, Redirect) and output.is_custom():
                # Custom support for Redirect constants.
                if output == Redirect.INHERIT:
                    stdout = sys_stream
                else:
                    # CAPTURE uses stdout == PIPE.
                    assert output == Redirect.CAPTURE
                    assert stdout == asyncio.subprocess.PIPE
            elif isinstance(output, int):
                # File descriptor or magic constant (e.g. DEVNULL).
                stdout = output
                if close:
                    self.open_fds.append(stdout)
            elif isinstance(output, io.StringIO):
                pass
            elif isinstance(output, io.IOBase):
                # Client-managed File-like object.
                stdout = output
            else:
                raise TypeError(f"unsupported type: {output!r}")

        return stdout


class Runner:
    """Runner is an asynchronous context manager that runs a command.

    ```
    runner = Runner(cmd)
    async with runner as (stdin, stdout, stderr):
        # process stdin, stdout, stderr (if not None)
    # Do something with finished `runner`.
    ```
    """

    def __init__(self, command):
        self.options = RunOptions(command)
        self.cancelled = False
        self.proc = None
        self.tasks = []

    def make_result(self, output_bytes):
        "Check process exit code and raise a ResultError if necessary."
        code = self.proc.returncode
        assert code is not None

        result = Result(
            output_bytes,
            code,
            self.cancelled,
            self.options.encoding,
        )

        command = self.options.command
        return make_result(command, result)

    def add_task(self, coro):
        "Add a background task."
        task = asyncio.create_task(coro, name=str(self.options.command))
        self.tasks.append(task)

    async def wait(self):
        "Wait for background I/O tasks and process to finish."
        if self.tasks:
            tasks = self.tasks
            self.tasks = []
            await asyncio.gather(*tasks)
        await self.proc.wait()

    async def __aenter__(self):
        "Set up redirections and launch subprocess."
        with self.options as opts:
            self.proc = await asyncio.create_subprocess_exec(
                *opts.args,
                **opts.kwd_args,
            )
            LOGGER.info("Runner.aenter %r", self)

        stdin = self.proc.stdin
        stdout = self.proc.stdout
        stderr = self.proc.stderr

        if stderr is not None:
            error = opts.command.options.error
            if isinstance(error, io.StringIO):
                # Set up task to redirect output to a StringIO.
                self.add_task(_copy_sync(stderr, error, opts.encoding))
                stderr = None

        if stdout is not None:
            output = opts.command.options.output
            if isinstance(output, io.StringIO):
                # Set up task to redirect output to a StringIO.
                self.add_task(_copy_sync(stdout, output, opts.encoding))
                stdout = None

        return (stdin, stdout, stderr)

    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for process to exit and handle cancellation."
        LOGGER.info("Runner.aexit %r exc_value=%r", self, exc_value)

        suppress = False  # set True if exc should be suppressed

        if exc_value is None:
            await self.wait()

        # Check for exceptions that should NEVER be suppressed or delayed.
        if not isinstance(exc_value, asyncio.CancelledError):
            return suppress

        # Check for cancellation.
        if isinstance(exc_value, asyncio.CancelledError):
            self.cancelled = True
            suppress = True
            self.proc.kill()

        # Wait here for process exit just in case context manager's inner code
        # does not. We also need to wait after sending a kill if cancelling...

        # FIXME: This can be cancelled or raise an exception. TEST!
        # Also, we need a timeout if self.cancelled is True.
        for task in self.tasks:
            assert task.done()
        await self.wait()

        return suppress

    def __repr__(self):
        "Return compact representation of Runner instance."
        arginfo = ""
        procinfo = ""
        taskinfo = ""

        if self.options.args:
            arginfo = f" args={self.options.args!r} kwd_args={self.options.kwd_args!r}"

        if self.proc:
            procinfo = f" pid={self.proc.pid} returncode={self.proc.returncode}"

        if self.tasks:
            taskinfo = f" tasks={self.tasks!r}"

        return f"<Runner cancelled={self.cancelled}{arginfo}{procinfo}{taskinfo}>"


async def run(command):
    "Run a command."
    output_bytes = None

    runner = Runner(command)
    async with runner as (stdin, stdout, stderr):
        if stderr is not None:
            raise ValueError("CAPTURE only supported for 'async with'")

        if stdin is not None:
            runner.add_task(_feed_writer(runner.options.input_bytes, stdin))

        if stdout is not None:
            output_bytes = await stdout.read()

    return runner.make_result(output_bytes)


async def run_iter(command):
    "Run a command and iterate over output."
    runner = Runner(command)
    async with runner as (stdin, stdout, stderr):
        if stderr is not None:
            raise ValueError("CAPTURE only supported for 'async with'")

        if stdin is not None:
            runner.add_task(_feed_writer(runner.options.input_bytes, stdin))

        encoding = runner.options.encoding
        async for line in stdout:
            yield decode(line, encoding)

    runner.make_result(None)


async def run_pipe(pipe):
    "Run a pipeline"

    n = len(pipe.commands)
    if n == 1:
        return await run(pipe.commands[0])

    open_fds = []
    try:
        cmds = list(pipe.commands)

        for i in range(n - 1):
            (read_fd, write_fd) = os.pipe()
            open_fds.extend((read_fd, write_fd))

            cmds[i] = cmds[i].stdout(write_fd, close=True)
            cmds[i + 1] = cmds[i + 1].stdin(read_fd, close=True)

        for i in range(n):
            cmds[i] = cmds[i].set(return_result=True)

    except Exception:
        _close_fds(open_fds)
        raise

    # Even though cmd is `Awaitable`, we explicitly create a task for each
    # cmd because internally `gather` relies on an `arg_to_fut` mapping
    # which will be confused if two cmd's are "equal".
    tasks = [cmd.task() for cmd in cmds]

    results = await asyncio.gather(*tasks)

    return make_result(pipe, results)


async def run_pipe_iter(_pipe):
    "Run a pipeline and iterate over standard output."
    return NotImplemented


def _log_exception(func):
    @functools.wraps(func)
    async def _wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception:
            LOGGER.exception("Task failed!")
            raise

    return _wrapper


@_log_exception
async def _feed_writer(input_bytes, stream):
    if input_bytes:
        stream.write(input_bytes)
    try:
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    stream.close()


@_log_exception
async def _copy_sync(source, dest, encoding):
    # Collect partial reads into a BytesIO.
    buf = io.BytesIO()
    try:
        while True:
            data = await source.read(1024)
            if not data:
                break
            buf.write(data)
    finally:
        # Only convert to string once all output is collected.
        # (What if utf-8 codepoint is split between reads?)
        dest.write(decode(buf.getvalue(), encoding))


def _close_fds(open_fds):
    "Close open file descriptors or file objects."
    try:
        for obj in open_fds:
            if isinstance(obj, int):
                if obj >= 0:
                    try:
                        os.close(obj)
                    except OSError:
                        pass
            else:
                obj.close()
    finally:
        open_fds.clear()
