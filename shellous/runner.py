"Implements utilities to run a command."

import asyncio
import io
import os
import sys

import shellous.redirect as redir
from shellous.harvest import harvest, harvest_results
from shellous.log import LOGGER, log_method
from shellous.redirect import Redirect
from shellous.result import Result, make_result
from shellous.util import close_fds, decode

_NORMAL_LOGGING = True
_DETAILED_LOGGING = True

_KILL_TIMEOUT = 3.0
_CLOSE_TIMEOUT = 0.25


def _is_cancelled(ex):
    return isinstance(ex, asyncio.CancelledError)


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
        close_fds(self.open_fds)

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
            LOGGER.info(
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
    async with cmd.run() as run:
        # process run.stdin, run.stdout, run.stderr (if not None)
    result = run.result()
    ```
    """

    def __init__(self, command):
        self.options = _RunOptions(command)
        self.cancelled = False
        self.proc = None
        self.tasks = []
        self.stdin = None
        self.stdout = None
        self.stderr = None

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

        return make_result(self.command, result, self.cancelled)

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
            await harvest(*self.tasks, trustee=self)

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
            done = self.proc.returncode is not None

            # If not already done, send cancel signal.
            if not done:
                self._send_signal(cancel_signal)

            if self.tasks:
                await harvest(*self.tasks, timeout=cancel_timeout, trustee=self)
            elif not done:
                await harvest(self.proc.wait(), timeout=cancel_timeout, trustee=self)

        except (asyncio.CancelledError, asyncio.TimeoutError) as ex:
            LOGGER.warning("Runner.kill %r (ex)=%r", self, ex)
            if _is_cancelled(ex):
                self.cancelled = True
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

        try:
            self.proc.kill()
            await harvest(self.proc.wait(), timeout=_KILL_TIMEOUT, trustee=self)
        except asyncio.TimeoutError as ex:
            LOGGER.error("%r failed to kill process %r", self, self.proc)
            raise RuntimeError(f"Unable to kill process {self.proc!r}") from ex

    @log_method(_NORMAL_LOGGING, _info=True)
    async def __aenter__(self):
        "Set up redirections and launch subprocess."
        try:
            return await self._start()
        finally:
            if self.cancelled and self.command.options.incomplete_result:
                # Raises ResultError instead of CancelledError.
                self.result()

    @log_method(_DETAILED_LOGGING)
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
            LOGGER.info("Runner.start %r ex=%r", self, ex)
            if _is_cancelled(ex):
                self.cancelled = True
            if self.proc:
                await self._kill()
            raise

        # Make final streams available. These may be different from `self.proc`
        # versions.
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr

        return self

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

    @log_method(_NORMAL_LOGGING, exc_value=2)
    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for process to exit and handle cancellation."
        suppress = False
        try:
            suppress = await self._finish(exc_value)
        except asyncio.CancelledError:
            LOGGER.info("Runner cancelled inside _finish %r", self)
            self.cancelled = True
        return suppress

    @log_method(_DETAILED_LOGGING)
    async def _finish(self, exc_value):
        "Finish the run. Return True only if `exc_value` should be suppressed."
        assert self.proc

        try:
            if exc_value is not None:
                if _is_cancelled(exc_value):
                    self.cancelled = True
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

        # _close can be called when unwinding exceptions. We need to handle
        # the case that the process has not exited yet. Remember to close the
        # transport.
        if self.proc.returncode is None:
            LOGGER.critical("Runner._close process still running %r", self.proc)
            self.proc._transport.close()
            return

        try:
            # Make sure the transport is closed (for asyncio and uvloop).
            self.proc._transport.close()

            # Make sure that original stdin is properly closed. `wait_closed`
            # will raise a BrokenPipeError if not all input was properly written.
            if self.proc.stdin is not None:
                self.proc.stdin.close()
                await harvest(
                    self.proc.stdin.wait_closed(),
                    timeout=_CLOSE_TIMEOUT,
                    trustee=self,
                )

        except asyncio.TimeoutError:
            LOGGER.critical("Runner._close %r timeout stdin=%r", self, self.proc.stdin)

    def __repr__(self):
        "Return string representation of Runner."
        cancelled = " cancelled" if self.cancelled else ""
        if self.proc:
            procinfo = f" pid={self.proc.pid} exit_code={self.proc.returncode}"
        else:
            procinfo = " pid=None"
        return f"<Runner {self.name!r}{cancelled}{procinfo}>"

    async def _readlines(self):
        "Iterate over lines in stdout/stderr"
        if self.stdin or (self.stdout and self.stderr):
            raise RuntimeError("multiple capture not supported in iterator")

        encoding = self.options.encoding
        stream = self.stdout or self.stderr
        if stream:
            async for line in stream:
                yield decode(line, encoding)

    def __aiter__(self):
        "Return asynchronous iterator over stdout/stderr."
        return self._readlines()


class PipeRunner:  # pylint: disable=too-many-instance-attributes
    """PipeRunner is an asynchronous context manager that runs a pipeline.

    ```
    async with pipe.run() as run:
        # process run.stdin, run.stdout, run.stderr (if not None)
    result = run.result()
    ```
    """

    def __init__(self, pipe, *, capturing):
        """`capturing=True` indicates we are within an `async with` block and
        client needs to access `stdin` and `stderr` streams.
        """
        assert len(pipe.commands) > 1

        self.pipe = pipe
        self.cancelled = False
        self.tasks = None
        self.results = None
        self.capturing = capturing
        self.encoding = pipe.commands[-1].options.encoding
        self.stdin = None
        self.stdout = None
        self.stderr = None

    @property
    def name(self):
        "Return name of the pipeline."
        return self.pipe.name

    def result(self):
        "Return `Result` object for PipeRunner."
        return make_result(self.pipe, self.results, self.cancelled)

    @log_method(_DETAILED_LOGGING)
    async def _wait(self, *, kill=False):
        "Wait for pipeline to finish."
        assert self.results is None
        assert self.tasks is not None

        if kill:
            LOGGER.info("PipeRunner.wait killing pipe %r", self)
            for task in self.tasks:
                task.cancel()

        cancelled, self.results = await harvest_results(*self.tasks, trustee=self)
        if cancelled:
            self.cancelled = True

    @log_method(_NORMAL_LOGGING)
    async def __aenter__(self):
        "Set up redirections and launch pipeline."
        try:
            return await self._start()
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("PipeRunner enter %r ex=%r", self, ex)
            if _is_cancelled(ex):
                self.cancelled = True
            await self._wait(kill=True)  # FIXME
            raise

    @log_method(_NORMAL_LOGGING, exc_value=2)
    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for pipeline to exit and handle cancellation."
        suppress = False
        try:
            suppress = await self._finish(exc_value)
        except asyncio.CancelledError:
            LOGGER.warning("PipeRunner cancelled inside _finish %r", self)
            self.cancelled = True
        return suppress

    @log_method(_DETAILED_LOGGING)
    async def _finish(self, exc_value):
        "Wait for pipeline to exit and handle cancellation."
        if exc_value is not None:
            if _is_cancelled(exc_value):
                self.cancelled = True
            await self._wait(kill=True)
            return self.cancelled

        await self._wait()

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

            self.stdin = stdin
            self.stdout = stdout
            self.stderr = stderr

            return self

        except BaseException:  # pylint: disable=broad-except
            # Clean up after any exception *including* CancelledError.
            close_fds(open_fds)

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
            cmds[i] = cmds[i].set(return_result=True, incomplete_result=True)

        return cmds

    @log_method(_DETAILED_LOGGING)
    async def _setup_capturing(self, cmds):
        """Set up capturing and return (stdin, stdout, stderr) streams."""

        loop = asyncio.get_event_loop()
        first_fut = loop.create_future()
        last_fut = loop.create_future()
        first_task = cmds[0].task(_run_future=first_fut)
        last_task = cmds[-1].task(_run_future=last_fut)

        middle_tasks = [cmd.task() for cmd in cmds[1:-1]]
        self.tasks = [first_task] + middle_tasks + [last_task]

        # When capturing, we need the first and last commands in the
        # pipe to signal when they are ready.
        first_ready, last_ready = await asyncio.gather(first_fut, last_fut)

        stdin, stdout, stderr = (
            first_ready.stdin,
            last_ready.stdout,
            last_ready.stderr,
        )

        return (stdin, stdout, stderr)

    def __repr__(self):
        "Return string representation of PipeRunner."
        cancelled_info = ""
        if self.cancelled:
            cancelled_info = " cancelled"
        result_info = ""
        if self.results:
            result_info = f" results={self.results!r}"
        return f"<PipeRunner {self.name!r}{cancelled_info}{result_info}>"

    async def _readlines(self):
        "Iterate over lines in stdout/stderr"
        if self.stdin or (self.stdout and self.stderr):
            raise RuntimeError("multiple capture not supported in iterator")

        encoding = self.encoding
        stream = self.stdout or self.stderr
        if stream:
            async for line in stream:
                yield decode(line, encoding)

    def __aiter__(self):
        "Return asynchronous iterator over stdout/stderr."
        return self._readlines()


async def run_cmd(command, *, _run_future=None):
    "Run a command."
    if not _run_future and command.multiple_capture:
        raise ValueError("multiple capture requires 'async with'")

    output_bytes = None

    async with command.run() as run:
        if _run_future is not None:
            # Return streams to caller in another task.
            _run_future.set_result(run)

        else:
            # Read the output here and return it.
            stream = run.stdout or run.stderr
            if stream:
                output_bytes = await stream.read()

    return run.result(output_bytes)


async def run_pipe(pipe):
    "Run a pipeline"

    run = PipeRunner(pipe, capturing=False)
    async with run:
        pass
    return run.result()
