"Implements utilities to run a command."

import asyncio
import io
import os
import sys
from logging import Logger

import shellous
import shellous.redirect as redir
from shellous import pty_util
from shellous.harvest import harvest, harvest_results
from shellous.log import LOG_DETAIL, LOG_ENTER, LOG_EXIT, LOGGER, log_method, log_timer
from shellous.redirect import Redirect
from shellous.result import Result, make_result
from shellous.util import close_fds, verify_dev_fd, wait_pid

_KILL_TIMEOUT = 3.0
_CLOSE_TIMEOUT = 0.25

_KILL_EXIT_CODE = -9 if sys.platform != "win32" else 1
_FLAKY_EXIT_CODE = 255
_BSD = sys.platform.startswith("freebsd") or sys.platform == "darwin"

AUDIT_EVENT_SUBPROCESS_SPAWN = "byllyfish/shellous.subprocess_spawn"


def _is_cancelled(ex):
    return isinstance(ex, asyncio.CancelledError)


def _is_cmd(cmd):
    return isinstance(cmd, (shellous.Command, shellous.Pipeline))


def _is_write_mode(cmd):
    "Return true if command/pipeline has write_mode set."
    if isinstance(cmd, shellous.Pipeline):
        # Pipelines need to check both the last/first commands.
        return cmd.options.write_mode or cmd[0].options.write_mode
    return cmd.options.write_mode


class _RunOptions:  # pylint: disable=too-many-instance-attributes
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
        self.subcmds = []
        self.pty_fds = None

    def close_fds(self):
        "Close all open file descriptors in `open_fds`."
        close_fds(self.open_fds)

    def __enter__(self):
        "Set up I/O redirections."
        try:
            self._setup_proc_sub()
            self._setup_redirects()
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
            for subcmd in self.subcmds:
                _cleanup(subcmd)

    def _setup_proc_sub(self):
        "Set up process substitution."
        if not any(_is_cmd(arg) for arg in self.command.args):
            return

        if sys.platform == "win32":
            raise RuntimeError("process substitution not supported on Windows")

        new_args = []
        pass_fds = []

        for arg in self.command.args:
            if not _is_cmd(arg):
                new_args.append(arg)
                continue

            (read_fd, write_fd) = os.pipe()
            if _is_write_mode(arg):
                new_args.append(f"/dev/fd/{write_fd}")
                pass_fds.append(write_fd)
                subcmd = arg.stdin(read_fd, close=True)
            else:
                new_args.append(f"/dev/fd/{read_fd}")
                pass_fds.append(read_fd)
                subcmd = arg.stdout(write_fd, close=True)

            self.subcmds.append(subcmd)

        # pylint: disable=protected-access
        self.command = self.command._replace_args(new_args).set(
            pass_fds=pass_fds,
            pass_fds_close=True,
        )

        if _BSD:
            verify_dev_fd(pass_fds[0])

    def _setup_redirects(self):
        "Set up I/O redirections."
        options = self.command.options

        stdin, input_bytes = self._setup_input(
            Redirect.from_default(options.input, 0, options.pty),
            options.input_close,
            self.encoding,
        )

        stdout = self._setup_output(
            Redirect.from_default(options.output, 1, options.pty),
            options.output_append,
            options.output_close,
            sys.stdout,
        )

        stderr = self._setup_output(
            Redirect.from_default(options.error, 2, options.pty),
            options.error_append,
            options.error_close,
            sys.stderr,
        )

        # Set up PTY here. This is the first half. Second half in `Runner`.
        start_new_session = options.start_new_session
        preexec_fn = options.preexec_fn
        if options.pty:
            assert preexec_fn is None
            stdin, stdout, stderr, preexec_fn = self._setup_pty1(
                stdin,
                stdout,
                stderr,
                options.pty,
            )
            start_new_session = True

        self.input_bytes = input_bytes
        self.args = self.command.args
        self.kwd_args = {
            "stdin": stdin,
            "stdout": stdout,
            "stderr": stderr,
            "env": options.merge_env(),
            "start_new_session": start_new_session,
            "preexec_fn": preexec_fn,
        }

        if options.pass_fds:
            self.kwd_args["pass_fds"] = options.pass_fds
            if options.pass_fds_close:
                self.open_fds.extend(options.pass_fds)

    def _setup_input(self, input_, close, encoding):
        "Set up process input."
        assert input_ is not None

        stdin = asyncio.subprocess.PIPE
        input_bytes = None

        if isinstance(input_, (bytes, bytearray)):
            input_bytes = input_
        elif isinstance(input_, os.PathLike):
            stdin = open(input_, "rb")  # pylint: disable=consider-using-with
            self.open_fds.append(stdin)
        elif isinstance(input_, Redirect) and input_.is_custom():
            # Custom support for Redirect constants.
            if input_ == Redirect.INHERIT:
                stdin = sys.stdin
            elif input_ == Redirect.IGNORE:
                input_bytes = None
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
            input_bytes = input_.encode(*encoding.split(maxsplit=1))

        return stdin, input_bytes

    def _setup_output(self, output, append, close, sys_stream):
        "Set up process output. Used for both stdout and stderr."
        assert output is not None

        stdout = asyncio.subprocess.PIPE

        if isinstance(output, (str, bytes, os.PathLike)):
            mode = "ab" if append else "wb"
            stdout = open(output, mode=mode)  # pylint: disable=consider-using-with
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
        elif isinstance(output, (io.StringIO, io.BytesIO, bytearray, Logger)):
            pass
        elif isinstance(output, io.IOBase):
            # Client-managed File-like object.
            stdout = output
        else:
            raise TypeError(f"unsupported type: {output!r}")

        return stdout

    def _setup_pty1(self, stdin, stdout, stderr, pty):
        """Set up pseudo-terminal and return (stdin, stdout, stderr, preexec_fn).

        Initializes `self.pty_fds`.
        """

        self.pty_fds = pty_util.open_pty(pty)
        child_fd = int(self.pty_fds.child_fd)

        # On BSD-derived systems like FreeBSD and Darwin, we delay closing the
        # pty's child_fd in the parent process until after the first read
        # succeeds. On Linux, we close the child_fd as soon as possible.

        if not _BSD:
            self.open_fds.append(self.pty_fds.child_fd)

        if LOG_DETAIL:
            LOGGER.info("_setup_pty1: %r", self.pty_fds)

        if stdin == asyncio.subprocess.PIPE:
            stdin = child_fd

        if stdout == asyncio.subprocess.PIPE:
            stdout = child_fd

        if stderr == asyncio.subprocess.STDOUT:
            stderr = child_fd
        elif stderr == asyncio.subprocess.PIPE:
            raise RuntimeError("pty can't separate stderr from stdout")

        return stdin, stdout, stderr, lambda: pty_util.set_ctty(child_fd)


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

    @property
    def pid(self):
        "Return the command's process ID."
        if not self.proc:
            return None
        return self.proc.pid

    def result(self, output_bytes=b""):
        "Check process exit code and raise a ResultError if necessary."
        if self.proc:
            code = self.proc.returncode
        else:
            # The process was started but cancelled immediately; `self.proc`
            # was never returned.
            assert self.cancelled
            code = _KILL_EXIT_CODE

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
        return task

    def _is_bsd_pty(self):
        "Return true if we're running a pty on BSD."
        return _BSD and self.options.pty_fds

    @log_method(LOG_DETAIL)
    async def _wait(self):
        "Normal wait for background I/O tasks and process to finish."
        assert self.proc

        try:
            if self.tasks:
                await harvest(*self.tasks, trustee=self)
            if self._is_bsd_pty():
                await self._waiter()

        except asyncio.CancelledError:
            LOGGER.info("Runner.wait cancelled %r", self)
            self.cancelled = True
            self.tasks.clear()  # all tasks were cancelled
            await self._kill()

    @log_method(LOG_DETAIL)
    async def _wait_pid(self):
        "Manually poll `waitpid` until process finishes."
        assert self._is_bsd_pty()
        while True:
            status = wait_pid(self.proc.pid)
            if status is not None:
                # pylint: disable=protected-access
                self.proc._transport._returncode = status
                self.proc._transport._proc.returncode = status
                break

            await asyncio.sleep(0.025)

    @log_method(LOG_DETAIL)
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
                await harvest(*self.tasks, timeout=cancel_timeout, trustee=self)

            if self.proc.returncode is None:
                await harvest(self._waiter(), timeout=cancel_timeout, trustee=self)

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

    @log_method(LOG_DETAIL)
    async def _kill_wait(self):
        "Wait for killed process to exit."
        assert self.proc

        # Check if process is already done.
        if self.proc.returncode is not None:
            return

        try:
            self._send_signal(None)
            await harvest(self._waiter(), timeout=_KILL_TIMEOUT, trustee=self)
        except asyncio.TimeoutError as ex:
            LOGGER.error("%r failed to kill process %r", self, self.proc)
            raise RuntimeError(f"Unable to kill process {self.proc!r}") from ex

    @log_method(LOG_ENTER, _info=True)
    async def __aenter__(self):
        "Set up redirections and launch subprocess."
        try:
            return await self._start()
        finally:
            if self.cancelled and self.command.options.incomplete_result:
                # Raises ResultError instead of CancelledError.
                self.result()

    @log_method(LOG_DETAIL)
    async def _start(self):
        "Set up redirections and launch subprocess."
        assert self.proc is None
        assert not self.tasks

        try:
            # Set up subprocess arguments and launch subprocess.
            with self.options as opts:
                await self._subprocess_spawn(opts)

            stdin = self.proc.stdin
            stdout = self.proc.stdout
            stderr = self.proc.stderr

            # Assign pty streams.
            if opts.pty_fds:
                assert (stdin, stdout, stderr) == (None, None, None)
                stdin, stdout = opts.pty_fds.writer, opts.pty_fds.reader

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
                    eof = opts.pty_fds.eof if opts.pty_fds else None
                    self.add_task(
                        redir.write_stream(opts.input_bytes, stdin, eof),
                        "stdin",
                    )
                    stdin = None

        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.info("Runner._start %r ex=%r", self, ex)
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

        # Add a task to monitor for when the process finishes.
        if not self._is_bsd_pty():
            self.add_task(self._waiter(), "waiter")

        return self

    @log_method(LOG_DETAIL)
    async def _subprocess_spawn(self, opts):
        "Start the subprocess and assign to `self.proc`."

        # Second half of pty setup.
        if opts.pty_fds:
            opts.pty_fds = await opts.pty_fds.open_streams()
            if _BSD:
                pty_util.patch_child_watcher()

        with log_timer("asyncio.create_subprocess_exec"):
            # AUDIT: subprocess spawn
            sys.audit(AUDIT_EVENT_SUBPROCESS_SPAWN, opts.args[0])
            self.proc = await asyncio.create_subprocess_exec(
                *opts.args,
                **opts.kwd_args,
            )

        # Launch the process substitution commands (if any).
        for cmd in opts.subcmds:
            self.add_task(cmd.coro(), "procsub")

    @log_method(LOG_DETAIL)
    async def _waiter(self):
        "Run task that waits for process to exit."
        if self._is_bsd_pty():
            await self._wait_pid()
        else:
            await self.proc.wait()

    def _setup_output_sink(self, stream, sink, encoding, tag):
        "Set up a task to write to custom output sink."
        if isinstance(sink, io.StringIO):
            if encoding is None:
                raise TypeError("StringIO not supported when encoding=None")
            self.add_task(redir.copy_stringio(stream, sink, encoding), tag)
            stream = None
        elif isinstance(sink, io.BytesIO):
            self.add_task(redir.copy_bytesio(stream, sink), tag)
            stream = None
        elif isinstance(sink, bytearray):
            self.add_task(redir.copy_bytearray(stream, sink), tag)
            stream = None
        elif isinstance(sink, Logger):
            self.add_task(redir.copy_logger(stream, sink, encoding), tag)
            stream = None
        return stream

    @log_method(LOG_EXIT, exc_value=2)
    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for process to exit and handle cancellation."
        suppress = False
        try:
            suppress = await self._finish(exc_value)
        except asyncio.CancelledError:
            LOGGER.info("Runner cancelled inside _finish %r", self)
            self.cancelled = True
        return suppress

    @log_method(LOG_DETAIL)
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

    @log_method(LOG_DETAIL)
    async def _close(self):
        "Make sure that our resources are properly closed."
        assert self.proc

        if self.options.pty_fds:
            self.options.pty_fds.close()

        # _close can be called when unwinding exceptions. We need to handle
        # the case that the process has not exited yet. Remember to close the
        # transport.
        if self.proc.returncode is None:
            LOGGER.critical("Runner._close process still running %r", self.proc)
            self.proc._transport.close()  # pylint: disable=protected-access
            return

        # asyncio child watcher artifact.
        if self.proc.returncode == _FLAKY_EXIT_CODE:
            LOGGER.warning("Runner._close exit code=%r", self.proc.returncode)

        try:
            # Make sure the transport is closed (for asyncio and uvloop).
            self.proc._transport.close()  # pylint: disable=protected-access

            # Make sure that original stdin is properly closed. `wait_closed`
            # will raise a BrokenPipeError if not all input was properly written.
            if self.proc.stdin is not None:
                self.proc.stdin.close()
                await harvest(
                    self.proc.stdin.wait_closed(),
                    timeout=_CLOSE_TIMEOUT,
                    cancel_finish=True,  # finish `wait_closed` if cancelled
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

        stream = self.stdout or self.stderr
        if stream:
            async for line in redir.read_lines(stream, self.options.encoding):
                yield line

    def __aiter__(self):
        "Return asynchronous iterator over stdout/stderr."
        return self._readlines()

    @staticmethod
    async def run_command(command, *, _run_future=None):
        "Run a command. This is the main entry point for Runner."
        if not _run_future and _is_multiple_capture(command):
            LOGGER.warning("run_command: multiple capture requires 'async with'")
            _cleanup(command)
            raise ValueError("multiple capture requires 'async with'")

        output_bytes = bytearray()

        async with command.run() as run:
            if _run_future is not None:
                # Return streams to caller in another task.
                _run_future.set_result(run)

            else:
                # Read the output here and return it.
                stream = run.stdout or run.stderr
                if stream:
                    await redir.copy_bytearray(stream, output_bytes)

        return run.result(bytes(output_bytes))


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
        self.tasks = []
        self.results = None
        self.capturing = capturing
        self.encoding = pipe.options.encoding
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

    def add_task(self, coro, tag=None):
        "Add a background task."
        if tag:
            task_name = f"{self.name}#{tag}"
        else:
            task_name = self.name
        task = asyncio.create_task(coro, name=task_name)
        self.tasks.append(task)
        return task

    @log_method(LOG_DETAIL)
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

    @log_method(LOG_ENTER)
    async def __aenter__(self):
        "Set up redirections and launch pipeline."
        try:
            return await self._start()
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("PipeRunner enter %r ex=%r", self, ex)
            if _is_cancelled(ex):
                self.cancelled = True
            await self._wait(kill=True)
            raise

    @log_method(LOG_EXIT, exc_value=2)
    async def __aexit__(self, _exc_type, exc_value, _exc_tb):
        "Wait for pipeline to exit and handle cancellation."
        suppress = False
        try:
            suppress = await self._finish(exc_value)
        except asyncio.CancelledError:
            LOGGER.warning("PipeRunner cancelled inside _finish %r", self)
            self.cancelled = True
        return suppress

    @log_method(LOG_DETAIL)
    async def _finish(self, exc_value):
        "Wait for pipeline to exit and handle cancellation."
        if exc_value is not None:
            if _is_cancelled(exc_value):
                self.cancelled = True
            await self._wait(kill=True)
            return self.cancelled

        await self._wait()

    @log_method(LOG_DETAIL)
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
                for cmd in cmds:
                    self.add_task(cmd.coro())

            self.stdin = stdin
            self.stdout = stdout
            self.stderr = stderr

            return self

        except BaseException:  # pylint: disable=broad-except
            # Clean up after any exception *including* CancelledError.
            close_fds(open_fds)
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
            cmds[i] = cmds[i].set(return_result=True, incomplete_result=True)

        return cmds

    @log_method(LOG_DETAIL)
    async def _setup_capturing(self, cmds):
        """Set up capturing and return (stdin, stdout, stderr) streams."""

        loop = asyncio.get_event_loop()
        first_fut = loop.create_future()
        last_fut = loop.create_future()

        first_coro = cmds[0].coro(_run_future=first_fut)
        last_coro = cmds[-1].coro(_run_future=last_fut)
        middle_coros = [cmd.coro() for cmd in cmds[1:-1]]

        self.add_task(first_coro)
        for coro in middle_coros:
            self.add_task(coro)
        self.add_task(last_coro)

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

        stream = self.stdout or self.stderr
        if stream:
            async for line in redir.read_lines(stream, self.encoding):
                yield line

    def __aiter__(self):
        "Return asynchronous iterator over stdout/stderr."
        return self._readlines()

    @staticmethod
    async def run_pipeline(pipe):
        "Run a pipeline. This is the main entry point for PipeRunner."

        run = PipeRunner(pipe, capturing=False)
        async with run:
            pass
        return run.result()


def _is_multiple_capture(cmd):
    "Return true if stdin is CAPTURE or both stdout and stderr are CAPTURE."
    input_ = Redirect.from_default(cmd.options.input, 0, cmd.options.pty)
    if input_ == Redirect.CAPTURE:
        return True

    output = Redirect.from_default(cmd.options.output, 1, cmd.options.pty)
    error = Redirect.from_default(cmd.options.error, 2, cmd.options.pty)
    return output == Redirect.CAPTURE and error == Redirect.CAPTURE


def _cleanup(command):
    "Close remaining file descriptors that need to be closed."

    def _add_close(close, fdesc):
        if close:
            if isinstance(fdesc, (int, io.IOBase)):
                open_fds.append(fdesc)

    open_fds = []

    _add_close(command.options.input_close, command.options.input)
    _add_close(command.options.output_close, command.options.output)
    _add_close(command.options.error_close, command.options.error)

    if command.options.pass_fds_close:
        open_fds.extend(command.options.pass_fds)

    close_fds(open_fds)
