"Implements utilities to run a command."

import asyncio
import functools
import io
import os
import sys

from shellous.log import LOGGER
from shellous.result import Result, ResultError, make_result
from shellous.util import Redirect, decode, gather_collect


class RunOptions:
    """RunOptions is context manager to assist in running a command.

    This class sets up low-level I/O redirection and helps close open file
    descriptors.

    ```
    with RunOptions(cmd) as options:
        proc = await create_subprocess_exec(*options.args, **options.kwd_args)
        # etc.
    ```
    """

    def __init__(self, command):
        self.command = command
        self.encoding = command.options.encoding
        self.open_fds = []
        self.input_bytes = None
        self.args = None
        self.kwd_args = None

    def close_fds(self):
        "Close all open file descriptors in `open_fds`."
        _close_fds(self.open_fds)

    def __enter__(self):
        "Set up I/O redirections."
        try:
            self._setup()
            return self
        except Exception as ex:
            LOGGER.warning("RunOptions.__enter__ %r ex=%r", self.command.name, ex)
            raise

    def __exit__(self, *exc):
        "Make sure those file descriptors are cleaned up."
        self.close_fds()

    def _setup(self):
        "Set up I/O redirections."
        options = self.command.options

        stdin, input_bytes = self._setup_input(
            options.input, options.input_close, self.encoding
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

        self.input_bytes = input_bytes
        self.args = self.command.args
        self.kwd_args = {
            "stdin": stdin,
            "stdout": stdout,
            "stderr": stderr,
            "env": options.merge_env(),
        }

    def _setup_input(self, input_, close, encoding):
        "Set up process input."
        assert input_ is not None

        stdin = asyncio.subprocess.PIPE
        input_bytes = None

        if isinstance(input_, bytes):
            input_bytes = input_
        elif isinstance(input_, os.PathLike):
            stdin = open(input_, "rb")
            self.open_fds.append(stdin)
        elif isinstance(input_, Redirect) and input_.is_custom():
            # Custom support for Redirect constants.
            if input_ == Redirect.INHERIT:
                stdin = sys.stdin
            else:
                # CAPTURE uses stdin == PIPE.
                assert input_ == Redirect.CAPTURE
                assert stdin == asyncio.subprocess.PIPE
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
        assert output is not None

        stdout = asyncio.subprocess.PIPE

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

    @property
    def name(self):
        "Return name of process to run."
        return self.options.command.name

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

    async def wait(self, *, kill=False):
        "Wait for background I/O tasks and process to finish."
        if self.proc is None:
            LOGGER.info("Runner.wait %r never started", self.name)
            return

        try:
            if kill:
                LOGGER.info("Runner.wait %r killing %r", self.name, self.proc)
                self.proc.kill()

            if self.tasks:
                await gather_collect(*self.tasks)

        except asyncio.CancelledError as ex:
            LOGGER.info("Runner.wait %r cancelled proc=%r", self.name, self.proc)
            self.cancelled = True
            if not kill:
                self.proc.kill()
            await self.proc.wait()

        except Exception as ex:
            LOGGER.warning("Runner.wait %r ex=%r", self.name, ex)
            raise

    async def __aenter__(self):
        "Set up redirections and launch subprocess."
        LOGGER.info("Runner entering %r", self.name)
        try:
            return await self._setup()
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("Runner enter %r ex=%r", self.name, ex)
            await self.wait(kill=True)
            raise
        finally:
            LOGGER.info(
                "Runner entered %r proc=%r ex=%r",
                self.name,
                self.proc,
                sys.exc_info()[1],
            )

    async def _setup(self):
        "Set up redirections and launch subprocess."
        with self.options as opts:
            self.proc = await asyncio.create_subprocess_exec(
                *opts.args,
                **opts.kwd_args,
            )

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

        if stdin is not None:
            if opts.input_bytes is not None:
                self.add_task(_feed_writer(opts.input_bytes, stdin))
                stdin = None

        self.add_task(self.proc.wait())

        return (stdin, stdout, stderr)

    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for process to exit and handle cancellation."

        LOGGER.info("Runner exiting %r exc_value=%r", self.name, exc_value)
        try:
            if exc_value is not None:
                return await self._cleanup(exc_value)
            await self.wait()
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("Runner exit %r ex=%r", self.name, ex)
            raise
        finally:
            LOGGER.info(
                "Runner exited %r proc=%r exit_code=%r ex=%r",
                self.name,
                self.proc,
                self.proc.returncode if self.proc else "n/a",
                sys.exc_info()[1],
            )

    async def _cleanup(self, exc_value):
        "Clean up when there is an exception. Return true to suppress exception."

        # We only suppress CancelledError.
        self.cancelled = isinstance(exc_value, asyncio.CancelledError)
        await self.wait(kill=True)

        return self.cancelled

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


class PipeRunner:
    """PipeRunner is an asynchronous context manager that runs a pipeline.

    runner = PipeRunner(pipe)
    async with runner as (stdin, stdout, stderr):
        # process stdin, stdout, stderr (if not None)
    # Do something with finished `runner`.
    """

    def __init__(self, pipe, *, capturing=False):
        """`capturing=True` indicates we are within an `async with` block and
        client needs to access `stdin` and `stderr` streams.
        """
        self.pipe = pipe
        self.cancelled = False
        self.tasks = None
        self.results = None
        self.capturing = capturing
        self.encoding = pipe.commands[-1].options.encoding

    @property
    def name(self):
        "Return name of the pipeline."
        return self.pipe.name

    def make_result(self):
        "Return `Result` object for PipeRunner."
        return make_result(self.pipe, self.results)

    async def wait(self, *, kill=False):
        "Wait for pipeline to finish."
        try:
            assert self.results is None
            assert self.tasks is not None

            if kill:
                LOGGER.info("PipeRunner.wait killing pipe %r", self.name)
                for task in self.tasks:
                    task.cancel()

            self.results = await gather_collect(*self.tasks)

        except Exception as ex:
            LOGGER.warning("PipeRunner.wait ex=%r", ex)
            raise

    async def __aenter__(self):
        "Set up redirections and launch pipeline."
        LOGGER.info("PipeRunner entering %r", self.name)
        try:
            return await self._setup()
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("PipeRunner enter %r ex=%r", self.name, ex)
            await self.wait(kill=True)  # FIXME
            raise
        finally:
            LOGGER.info("PipeRunner entered %r ex=%r", self.name, sys.exc_info()[1])

    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for pipeline to exit and handle cancellation."
        LOGGER.info("PipeRunner exiting %r exc_value=%r", self.name, exc_value)
        try:
            if exc_value is not None:
                return await self._cleanup(exc_value)
            await self.wait()
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("PipeRunner exit %r ex=%r", self.name, ex)
            raise
        finally:
            LOGGER.info("PipeRunner exited %r ex=%r", self.name, sys.exc_info()[1])

    async def _cleanup(self, exc_value):
        "Clean up when there is an exception."

        self.cancelled = isinstance(exc_value, asyncio.CancelledError)
        await self.wait(kill=True)

        return self.cancelled

    async def _setup(self):
        "Set up redirection and launch pipeline."
        open_fds = []

        try:
            stdin = None
            stdout = None
            stderr = None

            cmds = self._setup_pipeline(open_fds)

            if self.capturing:
                stdin, stdout, stderr = await self._setup_capturing(cmds)
            else:
                self.tasks = [cmd.task() for cmd in cmds]

            return (stdin, stdout, stderr)

        except (Exception, asyncio.CancelledError):
            _close_fds(open_fds)
            raise

    def _setup_pipeline(self, open_fds):
        """Return the pipeline stitched together with pipe fd's.

        Each created open file descriptor is added to `open_fds` so it can
        be closed if there's an exception later.
        """
        cmds = list(self.pipe.commands)

        cmd_count = len(cmds)
        for i in range(cmd_count - 1):
            (read_fd, write_fd) = os.pipe()
            open_fds.extend((read_fd, write_fd))

            cmds[i] = cmds[i].stdout(write_fd, close=True)
            cmds[i + 1] = cmds[i + 1].stdin(read_fd, close=True)

        for i in range(cmd_count):
            cmds[i] = cmds[i].set(return_result=True)

        return cmds

    async def _setup_capturing(self, cmds):
        """Set up capturing and return (stdin, stdout, stderr) streams."""

        loop = asyncio.get_event_loop()
        first_fut = loop.create_future()
        last_fut = loop.create_future()
        first_task = cmds[0].task(_streams_future=first_fut)
        last_task = cmds[-1].task(_streams_future=last_fut)

        middle_tasks = [cmd.task() for cmd in cmds[1:-1]]
        self.tasks = [first_task] + middle_tasks + [last_task]

        # When capturing, we need the first and last commands in the
        # pipe to signal when they are ready.
        first_ready, last_ready = await gather_collect(first_fut, last_fut)
        stdin, stdout, stderr = (first_ready[0], last_ready[1], last_ready[2])

        return (stdin, stdout, stderr)


def _log_cmd(func):
    @functools.wraps(func)
    async def _wrapper(*args, **kwargs):
        try:
            LOGGER.info("enter %s(%r)", func.__name__, args[0].name)
            result = await func(*args, **kwargs)
            LOGGER.info("exit %s(%r)", func.__name__, args[0].name)
            return result
        except ResultError as ex:
            LOGGER.info(
                "exit %s(%r) error: %r",
                func.__name__,
                args[0].name,
                ex.result,
            )
            raise
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning(
                "exit %s(%r) exception %r",
                func.__name__,
                args[0].name,
                ex,
            )
            raise

    return _wrapper


async def run(command, *, _streams_future=None):
    "Run a command."
    if not _streams_future and command.multiple_capture:
        raise ValueError("multiple capture requires 'async with'")

    output_bytes = None
    runner = Runner(command)
    async with runner as (stdin, stdout, stderr):
        if _streams_future is not None:
            # Return streams to caller in another task.
            _streams_future.set_result((stdin, stdout, stderr))

        else:
            # Read the output here and return it.
            stream = stdout or stderr
            if stream:
                output_bytes = await stream.read()

    return runner.make_result(output_bytes)


async def run_iter(command):
    "Run a command and iterate over output."
    if command.multiple_capture:
        raise ValueError("multiple capture requires 'async with'")

    runner = Runner(command)
    async with runner as (_stdin, stdout, stderr):
        encoding = runner.options.encoding
        stream = stdout or stderr
        if stream:
            async for line in stream:
                yield decode(line, encoding)

    runner.make_result(None)


async def run_pipe(pipe):
    "Run a pipeline"

    cmd_count = len(pipe.commands)
    if cmd_count == 1:
        return await run(pipe.commands[0])

    runner = PipeRunner(pipe)
    async with runner:
        pass
    return runner.make_result()


async def run_pipe_iter(pipe):
    "Run a pipeline and iterate over standard output."
    runner = PipeRunner(pipe, capturing=True)
    async with runner as (stdin, stdout, stderr):
        assert stdin is None
        assert stderr is None

        encoding = runner.encoding
        async for line in stdout:
            yield decode(line, encoding)

    runner.make_result()


def _log_exception(func):
    @functools.wraps(func)
    async def _wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            LOGGER.info("Task cancelled!")
            raise
        except Exception as ex:
            LOGGER.warning("Task ex=%r", ex)
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
