"Implements utilities to run a command."

import asyncio
import io
import os
import sys

import shellous.redirect as redir
from shellous.log import LOGGER
from shellous.redirect import Redirect
from shellous.result import Result, make_result
from shellous.util import decode, gather_collect, log_method, platform_info

_DETAILED_LOGGING = True


def _exc():
    "Return the current exception value. Useful in logging."
    return sys.exc_info()[1]


class _RunOptions:
    """_RunOptions is context manager to assist in running a command.

    This class sets up low-level I/O redirection and helps close open file
    descriptors.

    ```
    with _RunOptions(cmd) as options:
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
            LOGGER.warning("_RunOptions.enter %r ex=%r", self.command.name, ex)
            raise

    def __exit__(self, _exc_type, exc_value, _exc_tb):
        "Make sure those file descriptors are cleaned up."
        self.close_fds()
        if exc_value:
            LOGGER.warning(
                "_RunOptions.exit %r exc_value=%r", self.command.name, exc_value
            )

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

        if isinstance(input_, (bytes, bytearray)):
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
        elif isinstance(output, (io.StringIO, io.BytesIO, bytearray)):
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
    result = runner.result()
    ```
    """

    def __init__(self, command):
        self.options = _RunOptions(command)
        self.cancelled = False
        self.proc = None
        self.tasks = []
        self.stdin = None

    @property
    def name(self):
        "Return name of process to run."
        return self.command.name

    @property
    def command(self):
        "Return the command being run."
        return self.options.command

    def result(self, output_bytes=None):
        "Check process exit code and raise a ResultError if necessary."
        if self.proc:
            code = self.proc.returncode
        else:
            code = None

        result = Result(
            output_bytes,
            code,
            self.cancelled,
            self.options.encoding,
        )

        return make_result(self.command, result)

    def add_task(self, coro, tag=None):
        "Add a background task."
        if tag:
            task_name = f"{self.name}#{tag}"
        else:
            task_name = self.name
        task = asyncio.create_task(coro, name=task_name)
        self.tasks.append(task)

    @log_method(_DETAILED_LOGGING)
    async def _wait(self):
        "Normal wait for background I/O tasks and process to finish."
        assert self.proc

        try:
            await gather_collect(*self.tasks, trustee=self)

        except asyncio.CancelledError:
            LOGGER.info("Runner.wait cancelled %r", self)
            self.cancelled = True
            self.tasks.clear()  # all tasks were cancelled
            await self._kill()

    @log_method(_DETAILED_LOGGING)
    async def _kill(self):
        "Kill process and wait for it to finish."
        assert self.proc

        cancel_timeout = self.command.options.cancel_timeout
        cancel_signal = self.command.options.cancel_signal

        try:
            # If not already done, send cancel signal.
            if self.proc.returncode is None:
                self._send_signal(cancel_signal)

            if self.tasks:
                await gather_collect(*self.tasks, timeout=cancel_timeout, trustee=self)
            else:
                await gather_collect(
                    self.proc.wait(), timeout=cancel_timeout, trustee=self
                )

        except (asyncio.CancelledError, asyncio.TimeoutError) as ex:
            LOGGER.warning("Runner.kill %r (ex)=%r", self, ex)
            await self._kill_wait()

        except Exception as ex:
            LOGGER.warning("Runner.kill %r ex=%r", self, ex)
            await self._kill_wait()
            raise

    def _send_signal(self, sig):
        "Send a signal to the process."

        LOGGER.info("Runner.signal %r signal=%r", self, sig)
        if sig is None:
            self.proc.kill()
        else:
            self.proc.send_signal(sig)

    @log_method(_DETAILED_LOGGING)
    async def _kill_wait(self):
        "Wait for killed process to exit."
        assert self.proc

        # Check if process is already done.
        if self.proc.returncode is not None:
            return

        self.proc.kill()
        await self.proc.wait()  # FIXME: needs timeout...

    async def __aenter__(self):
        "Set up redirections and launch subprocess."
        LOGGER.info("Runner entering %r (%s)", self, platform_info())
        try:
            return await self._start()

        finally:
            LOGGER.info("Runner entered %r ex=%r", self, _exc())

    async def _start(self):
        "Set up redirections and launch subprocess."
        assert self.proc is None
        assert not self.tasks

        try:
            # Set up subprocess arguments and launch subprocess.
            with self.options as opts:
                self.proc = await asyncio.create_subprocess_exec(
                    *opts.args,
                    **opts.kwd_args,
                )

            # Add a task to monitor for when the process finishes.
            self.add_task(self.proc.wait(), "proc.wait")

            stdin = self.proc.stdin
            stdout = self.proc.stdout
            stderr = self.proc.stderr

            # Keep track of stdin so we can close it later.
            self.stdin = stdin

            if stderr is not None:
                error = opts.command.options.error
                stderr = self._setup_output_sink(stderr, error, opts.encoding, "stderr")

            if stdout is not None:
                output = opts.command.options.output
                stdout = self._setup_output_sink(
                    stdout, output, opts.encoding, "stdout"
                )

            if stdin is not None:
                if opts.input_bytes is not None:
                    self.add_task(redir.write_stream(opts.input_bytes, stdin), "stdin")
                    stdin = None

        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("Runner.start %r ex=%r", self, ex)
            self.cancelled = isinstance(ex, asyncio.CancelledError)
            if self.proc:
                await self._kill()
            raise

        return (stdin, stdout, stderr)

    def _setup_output_sink(self, stream, sink, encoding, tag):
        "Set up a task to write to custom output sink."
        if isinstance(sink, io.StringIO):
            self.add_task(redir.copy_stringio(stream, sink, encoding), tag)
            stream = None
        elif isinstance(sink, io.BytesIO):
            self.add_task(redir.copy_bytesio(stream, sink), tag)
            stream = None
        elif isinstance(sink, bytearray):
            self.add_task(redir.copy_bytearray(stream, sink), tag)
            stream = None
        return stream

    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for process to exit and handle cancellation."

        LOGGER.info("Runner exiting %r exc_value=%r", self, exc_value)
        suppress = False
        try:
            suppress = await self._finish(exc_value)
        except asyncio.CancelledError:
            LOGGER.warning("Runner cancelled inside _finish %r", self)
        finally:
            LOGGER.info(
                "Runner exited %r ex=%r exc_value=%r suppress=%r",
                self,
                _exc(),
                exc_value,
                suppress,
            )
        return suppress

    async def _finish(self, exc_value):
        "Finish the run. Return True only if `exc_value` should be suppressed."
        assert self.proc

        try:
            if exc_value is not None:
                self.cancelled = isinstance(exc_value, asyncio.CancelledError)
                await self._kill()
                return self.cancelled

            await self._wait()
            return False

        finally:
            await self._close()

    @log_method(_DETAILED_LOGGING)
    async def _close(self):
        "Make sure that our resources are properly closed."
        assert self.proc
        assert self.proc.returncode is not None

        try:
            # Make sure the transport is closed (for asyncio and uvloop).
            self.proc._transport.close()

            # Make sure that stdin is properly closed. `wait_closed` will raise
            # a BrokenPipeError if not all input was properly written.
            if self.stdin is not None:
                self.stdin.close()
                await gather_collect(
                    self.stdin.wait_closed(), timeout=0.25, trustee=self
                )

        except asyncio.TimeoutError:
            LOGGER.error("Runner._close %r timeout", self)

    def __repr__(self):
        "Return string representation of Runner."
        if self.proc:
            return f"<Runner {self.name!r} pid={self.proc.pid} exit_code={self.proc.returncode}>"
        return f"<Runner {self.name!r} pid=None>"


class PipeRunner:
    """PipeRunner is an asynchronous context manager that runs a pipeline.

    ```
    runner = PipeRunner(pipe)
    async with runner as (stdin, stdout, stderr):
        # process stdin, stdout, stderr (if not None)
    result = runner.result()
    ```
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

    def result(self):
        "Return `Result` object for PipeRunner."
        return make_result(self.pipe, self.results)

    @log_method(_DETAILED_LOGGING)
    async def _wait(self, *, kill=False):
        "Wait for pipeline to finish."
        assert self.results is None
        assert self.tasks is not None

        if kill:
            LOGGER.info("PipeRunner.wait killing pipe %r", self)
            for task in self.tasks:
                task.cancel()

        self.results = await gather_collect(
            *self.tasks, return_exceptions=True, trustee=self
        )

    async def __aenter__(self):
        "Set up redirections and launch pipeline."
        LOGGER.info("PipeRunner entering %r", self)
        try:
            return await self._start()
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("PipeRunner enter %r ex=%r", self, ex)
            await self.wait(kill=True)  # FIXME
            raise
        finally:
            LOGGER.info("PipeRunner entered %r ex=%r", self, _exc())

    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for pipeline to exit and handle cancellation."
        LOGGER.info("PipeRunner exiting %r exc_value=%r", self, exc_value)
        try:
            if exc_value is not None:
                return await self._cleanup(exc_value)
            await self._wait()
        finally:
            LOGGER.info(
                "PipeRunner exited %r exc_value=%r ex=%r",
                self,
                exc_value,
                _exc(),
            )

    @log_method(_DETAILED_LOGGING)
    async def _cleanup(self, exc_value):
        "Clean up when there is an exception."

        self.cancelled = isinstance(exc_value, asyncio.CancelledError)
        await self._wait(kill=True)

        return self.cancelled

    @log_method(_DETAILED_LOGGING)
    async def _start(self):
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

        except BaseException:
            # Clean up after any exception *including* CancelledError.
            _close_fds(open_fds)

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

    @log_method(_DETAILED_LOGGING)
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
        first_ready, last_ready = await gather_collect(
            first_fut, last_fut, trustee=self
        )
        stdin, stdout, stderr = (first_ready[0], last_ready[1], last_ready[2])

        return (stdin, stdout, stderr)

    def __repr__(self):
        "Return string representation of PipeRunner."
        result_info = ""
        if self.results:
            result_info = f" results={self.results!r}"
        return f"<PipeRunner {self.name!r}{result_info}>"


async def run(command, *, _streams_future=None):
    "Run a command."
    if not _streams_future and command.multiple_capture:
        raise ValueError("multiple capture requires 'async with'")

    output_bytes = None
    runner = Runner(command)

    try:
        async with runner as (stdin, stdout, stderr):
            if _streams_future is not None:
                # Return streams to caller in another task.
                _streams_future.set_result((stdin, stdout, stderr))

            else:
                # Read the output here and return it.
                stream = stdout or stderr
                if stream:
                    output_bytes = await stream.read()

    except asyncio.CancelledError:
        LOGGER.info("run %r cancelled inside enter", command.name)

    return runner.result(output_bytes)


async def run_iter(command):
    "Run a command and iterate over its output lines."
    if command.multiple_capture:
        raise ValueError("multiple capture requires 'async with'")

    runner = Runner(command)
    async with runner as (_stdin, stdout, stderr):
        encoding = runner.options.encoding
        stream = stdout or stderr
        if stream:
            async for line in stream:
                yield decode(line, encoding)

    runner.result()  # No return value; raises exception if needed


async def run_pipe(pipe):
    "Run a pipeline"

    cmd_count = len(pipe.commands)
    if cmd_count == 1:
        return await run(pipe.commands[0])

    runner = PipeRunner(pipe)
    async with runner:
        pass
    return runner.result()


async def run_pipe_iter(pipe):
    "Run a pipeline and iterate over its output lines."
    runner = PipeRunner(pipe, capturing=True)
    async with runner as (stdin, stdout, stderr):
        assert stdin is None
        assert stderr is None

        encoding = runner.encoding
        async for line in stdout:
            yield decode(line, encoding)

    runner.result()  # No return value; raises exception if needed


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
