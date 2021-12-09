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
from shellous.util import close_fds, poll_wait_pid, uninterrupted, verify_dev_fd, which

_KILL_TIMEOUT = 3.0
_CLOSE_TIMEOUT = 0.25
_UNKNOWN_EXIT_CODE = 255
_UNLAUNCHED_EXIT_CODE = -255

_BSD = sys.platform.startswith("freebsd") or sys.platform == "darwin"

AUDIT_EVENT_SUBPROCESS_SPAWN = "byllyfish/shellous.subprocess_spawn"


def _is_cancelled(ex):
    return isinstance(ex, asyncio.CancelledError)


def _is_cmd(cmd):
    return isinstance(cmd, (shellous.Command, shellous.Pipeline))


def _is_writable(cmd):
    "Return true if command/pipeline has `writable` set."
    if isinstance(cmd, shellous.Pipeline):
        # Pipelines need to check both the last/first commands.
        return cmd.options.writable or cmd[0].options.writable
    return cmd.options.writable


def _enc(encoding):
    "Helper function to split the encoding name into codec and errors."
    if encoding is None:
        raise TypeError("when encoding is None, input must be bytes")
    return encoding.split(maxsplit=1)


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
            if LOG_DETAIL:
                LOGGER.debug("_RunOptions.enter %r ex=%r", self.command.name, ex)
            _cleanup(self.command)
            raise

    def __exit__(self, _exc_type, exc_value, _exc_tb):
        "Make sure those file descriptors are cleaned up."
        self.close_fds()
        if exc_value:
            if LOG_DETAIL:
                LOGGER.debug(
                    "_RunOptions.exit %r exc_value=%r",
                    self.command.name,
                    exc_value,
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
            if _is_writable(arg):
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
        start_session = options._start_new_session  # pylint: disable=protected-access
        preexec_fn = options._preexec_fn  # pylint: disable=protected-access

        if options.pty:
            assert preexec_fn is None
            stdin, stdout, stderr, preexec_fn = self._setup_pty1(
                stdin,
                stdout,
                stderr,
                options.pty,
            )
            start_session = True

        assert not preexec_fn or callable(preexec_fn)

        self.input_bytes = input_bytes
        self.kwd_args = {
            "stdin": stdin,
            "stdout": stdout,
            "stderr": stderr,
            "env": options.merge_env(),
            "start_new_session": start_session,
            "preexec_fn": preexec_fn,
            "close_fds": options.close_fds,
        }

        if options.pass_fds:
            self.kwd_args["close_fds"] = True
            self.kwd_args["pass_fds"] = options.pass_fds
            if options.pass_fds_close:
                self.open_fds.extend(options.pass_fds)

        self.args = list(self.command.args)
        if not os.path.dirname(self.args[0]):
            self.args[0] = which(self.args[0])

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
            else:
                # CAPTURE uses stdin == PIPE.
                assert input_ == Redirect.CAPTURE
                assert stdin == asyncio.subprocess.PIPE
        elif isinstance(input_, int):  # file descriptor
            stdin = input_
            if close:
                self.open_fds.append(stdin)
        elif isinstance(input_, str):
            input_bytes = input_.encode(*_enc(encoding))
        elif isinstance(input_, (asyncio.StreamReader, io.BytesIO, io.StringIO)):
            # Shellous-supported input classes.
            assert stdin == asyncio.subprocess.PIPE
            assert input_bytes is None
        else:
            raise TypeError(f"unsupported input type: {input_!r}")

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
        elif isinstance(
            output, (io.StringIO, io.BytesIO, bytearray, Logger, asyncio.StreamWriter)
        ):
            # Shellous-supported output classes.
            assert stdout == asyncio.subprocess.PIPE
        elif isinstance(output, io.IOBase):
            # Client-managed File-like object.
            stdout = output
        else:
            raise TypeError(f"unsupported output type: {output!r}")

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

        ttyname = os.ttyname(child_fd)
        return stdin, stdout, stderr, lambda: pty_util.set_ctty(ttyname)


class Runner:
    """Runner is an asynchronous context manager that runs a command.

    ```
    async with cmd.run() as run:
        # process run.stdin, run.stdout, run.stderr (if not None)
    result = run.result()
    ```
    """

    stdin = None
    "Process standard input."

    stdout = None
    "Process standard output."

    stderr = None
    "Process standard error."

    def __init__(self, command):
        self._options = _RunOptions(command)
        self._cancelled = False
        self._proc = None
        self._tasks = []
        self._timer = None  # asyncio.TimerHandle
        self._timed_out = False  # True if runner timeout expired
        self._last_signal = None

    @property
    def name(self):
        "Return name of process being run."
        return self.command.name

    @property
    def command(self):
        "Return the command being run."
        return self._options.command

    @property
    def pid(self):
        "Return the command's process ID."
        if not self._proc:
            return None
        return self._proc.pid

    @property
    def returncode(self):
        "Process's exit code."
        if not self._proc:
            if self._cancelled:
                # The process was cancelled before starting.
                return _UNLAUNCHED_EXIT_CODE
            return None
        code = self._proc.returncode
        if code == _UNKNOWN_EXIT_CODE and self._last_signal is not None:
            # Rarely after sending a SIGTERM, waitpid fails to locate the child
            # process. In this case, map the status to the last signal we sent.
            return -self._last_signal  # pylint: disable=invalid-unary-operand-type
        return code

    @property
    def cancelled(self):
        "Return True if the command was cancelled."
        return self._cancelled

    def result(self, output_bytes=b""):
        "Check process exit code and raise a ResultError if necessary."
        code = self.returncode
        assert code is not None

        result = Result(
            output_bytes,
            code,
            self._cancelled,
            self._options.encoding,
        )

        return make_result(self.command, result, self._cancelled, self._timed_out)

    def add_task(self, coro, tag=""):
        "Add a background task."
        task_name = f"{self.name}#{tag}"
        task = asyncio.create_task(coro, name=task_name)
        self._tasks.append(task)
        return task

    def _is_bsd_pty(self):
        "Return true if we're running a pty on BSD."
        return _BSD and self._options.pty_fds

    @log_method(LOG_DETAIL)
    async def _wait(self):
        "Normal wait for background I/O tasks and process to finish."
        assert self._proc

        try:
            if self._tasks:
                await harvest(*self._tasks, trustee=self)
            if self._is_bsd_pty():
                await self._waiter()

        except asyncio.CancelledError:
            LOGGER.info("Runner.wait cancelled %r", self)
            self._set_cancelled()
            self._tasks.clear()  # all tasks were cancelled
            await self._kill()

        except Exception as ex:
            LOGGER.info("Runner.wait exited with error %r ex=%r", self, ex)
            self._tasks.clear()  # all tasks were cancelled
            await self._kill()
            raise  # re-raise exception

    @log_method(LOG_DETAIL)
    async def _wait_pid(self):
        "Manually poll `waitpid` until process finishes."
        assert self._is_bsd_pty()
        while True:
            if poll_wait_pid(self._proc):
                break
            await asyncio.sleep(0.025)

    @log_method(LOG_DETAIL)
    async def _kill(self):
        "Kill process and wait for it to finish."
        assert self._proc

        cancel_timeout = self.command.options.cancel_timeout
        cancel_signal = self.command.options.cancel_signal

        try:
            # If not already done, send cancel signal.
            if self._proc.returncode is None:
                self._send_signal(cancel_signal)

            if self._tasks:
                await harvest(*self._tasks, timeout=cancel_timeout, trustee=self)

            if self._proc.returncode is None:
                await harvest(self._waiter(), timeout=cancel_timeout, trustee=self)

        except (asyncio.CancelledError, asyncio.TimeoutError) as ex:
            LOGGER.warning("Runner.kill %r (ex)=%r", self, ex)
            if _is_cancelled(ex):
                self._set_cancelled()
            await self._kill_wait()

        except (Exception, GeneratorExit) as ex:
            LOGGER.warning("Runner.kill %r ex=%r", self, ex)
            await self._kill_wait()
            raise

    def _send_signal(self, sig):
        "Send a signal to the process."

        LOGGER.info("Runner.signal %r signal=%r", self, sig)
        self._audit_callback("signal", signal=sig)

        if sig is None:
            self._proc.kill()
        else:
            self._proc.send_signal(sig)
            self._last_signal = sig

    @log_method(LOG_DETAIL)
    async def _kill_wait(self):
        "Wait for killed process to exit."
        assert self._proc

        # Check if process is already done.
        if self._proc.returncode is not None:
            return

        try:
            self._send_signal(None)
            await harvest(self._waiter(), timeout=_KILL_TIMEOUT, trustee=self)
        except asyncio.TimeoutError as ex:
            # Manually check if the process is still running.
            if poll_wait_pid(self._proc):
                LOGGER.warning("%r process reaped manually %r", self, self._proc)
            else:
                LOGGER.error("%r failed to kill process %r", self, self._proc)
                raise RuntimeError(f"Unable to kill process {self._proc!r}") from ex

    @log_method(LOG_ENTER, _info=True)
    async def __aenter__(self):
        "Set up redirections and launch subprocess."
        self._audit_callback("start")
        try:
            return await self._start()
        except BaseException as ex:
            self._stop_timer()  # failsafe just in case
            self._audit_callback("stop", failure=type(ex).__name__)
            raise
        finally:
            if self._cancelled and self.command.options.incomplete_result:
                # Raises ResultError instead of CancelledError.
                self.result()

    @log_method(LOG_DETAIL)
    async def _start(self):
        "Set up redirections and launch subprocess."
        assert self._proc is None
        assert not self._tasks

        try:
            # Set up subprocess arguments and launch subprocess.
            with self._options as opts:
                await self._subprocess_spawn(opts)

            stdin = self._proc.stdin
            stdout = self._proc.stdout
            stderr = self._proc.stderr

            # Assign pty streams.
            if opts.pty_fds:
                assert (stdin, stdout) == (None, None)
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
                stdin = self._setup_input_source(stdin, opts)

        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.info("Runner._start %r ex=%r", self, ex)
            if _is_cancelled(ex):
                self._set_cancelled()
            if self._proc:
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

        # Set a timer to cancel the current task after a timeout.
        self._start_timer(self.command.options.timeout)

        return self

    @log_method(LOG_DETAIL)
    async def _subprocess_spawn(self, opts):
        "Start the subprocess."

        # Second half of pty setup.
        if opts.pty_fds:
            opts.pty_fds = await opts.pty_fds.open_streams()

        # Check for task cancellation and yield right before exec'ing. If the
        # current task is already cancelled, this will raise a CancelledError,
        # and we save ourselves the work of launching and immediately killing
        # a process.
        await asyncio.sleep(0)

        # Launch the subprocess (always completes even if cancelled).
        await uninterrupted(self._subprocess_exec(opts))

        # Launch the process substitution commands (if any).
        for cmd in opts.subcmds:
            self.add_task(cmd.coro(), "procsub")

    @log_method(LOG_DETAIL)
    async def _subprocess_exec(self, opts):
        "Start the subprocess and assign to `self.proc`."
        with log_timer("asyncio.create_subprocess_exec"):
            sys.audit(AUDIT_EVENT_SUBPROCESS_SPAWN, opts.args[0])
            if opts.pty_fds and _BSD:
                pty_util.patch_child_watcher()
            self._proc = await asyncio.create_subprocess_exec(
                *opts.args,
                **opts.kwd_args,
            )

    @log_method(LOG_DETAIL)
    async def _waiter(self):
        "Run task that waits for process to exit."
        try:
            if self._is_bsd_pty():
                await self._wait_pid()
            else:
                await self._proc.wait()
        finally:
            self._stop_timer()

    def _set_cancelled(self):
        "Set the cancelled flag, and cancel any inflight timers."
        self._cancelled = True
        self._stop_timer()

    def _start_timer(self, timeout):
        "Start an optional timer to cancel the process if `timeout` desired."
        assert self._timer is None
        if timeout is not None:
            loop = asyncio.get_running_loop()
            self._timer = loop.call_later(
                timeout,
                self._set_timer_expired,
                asyncio.current_task(),
            )

    def _set_timer_expired(self, main_task):
        "Set a flag when the timer expires and cancel the main task."
        self._timed_out = True
        self._timer = None
        main_task.cancel()

    def _stop_timer(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _setup_input_source(self, stream, opts):
        "Set up a task to read from custom input source."
        tag = "stdin"
        eof = opts.pty_fds.eof if opts.pty_fds else None

        if opts.input_bytes is not None:
            self.add_task(redir.write_stream(opts.input_bytes, stream, eof), tag)
            return None

        source = opts.command.options.input

        if isinstance(source, asyncio.StreamReader):
            self.add_task(redir.write_reader(source, stream, eof), tag)
            return None

        if isinstance(source, io.BytesIO):
            self.add_task(redir.write_stream(source.getvalue(), stream, eof), tag)
            return None

        if isinstance(source, io.StringIO):
            input_bytes = source.getvalue().encode(*_enc(opts.encoding))
            self.add_task(redir.write_stream(input_bytes, stream, eof), tag)
            return None

        return stream

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
        elif isinstance(sink, asyncio.StreamWriter):
            self.add_task(redir.copy_streamwriter(stream, sink), tag)
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
            self._set_cancelled()
        finally:
            self._stop_timer()  # failsafe just in case
            self._audit_callback("stop")
        # If `timeout` expired, raise TimeoutError rather than CancelledError.
        if (
            self._cancelled
            and self._timed_out
            and not self.command.options.incomplete_result
        ):
            raise asyncio.TimeoutError()
        return suppress

    @log_method(LOG_DETAIL)
    async def _finish(self, exc_value):
        "Finish the run. Return True only if `exc_value` should be suppressed."
        assert self._proc

        try:
            if exc_value is not None:
                if _is_cancelled(exc_value):
                    self._set_cancelled()
                await self._kill()
                return self._cancelled

            await self._wait()
            return False

        finally:
            await self._close()

    @log_method(LOG_DETAIL)
    async def _close(self):
        "Make sure that our resources are properly closed."
        assert self._proc

        if self._options.pty_fds:
            self._options.pty_fds.close()

        # _close can be called when unwinding exceptions. We need to handle
        # the case that the process has not exited yet. Remember to close the
        # transport.
        if self._proc.returncode is None:
            LOGGER.critical("Runner._close process still running %r", self._proc)
            self._proc._transport.close()  # pylint: disable=protected-access
            return

        try:
            # Make sure the transport is closed (for asyncio and uvloop).
            self._proc._transport.close()  # pylint: disable=protected-access

            # Make sure that original stdin is properly closed. `wait_closed`
            # will raise a BrokenPipeError if not all input was properly written.
            if self._proc.stdin is not None:
                self._proc.stdin.close()
                await harvest(
                    self._proc.stdin.wait_closed(),
                    timeout=_CLOSE_TIMEOUT,
                    cancel_finish=True,  # finish `wait_closed` if cancelled
                    trustee=self,
                )

        except asyncio.TimeoutError:
            LOGGER.critical("Runner._close %r timeout stdin=%r", self, self._proc.stdin)

    def _audit_callback(self, phase, *, failure=None, signal=None):
        "Call `audit_callback` if there is one."
        callback = self.command.options.audit_callback
        if callback:
            info = {"runner": self}
            if failure:
                info["failure"] = failure
            if phase == "signal":
                if signal is None:
                    signal = "Signals.SIGKILL"  # SIGKILL (even on win32)
                info["signal"] = str(signal)
            callback(phase, info)

    def __repr__(self):
        "Return string representation of Runner."
        cancelled = " cancelled" if self._cancelled else ""
        if self._proc:
            procinfo = f" pid={self._proc.pid} exit_code={self.returncode}"
        else:
            procinfo = " pid=None"
        return f"<Runner {self.name!r}{cancelled}{procinfo}>"

    async def _readlines(self):
        "Iterate over lines in stdout/stderr"
        if self.stdin or (self.stdout and self.stderr):
            raise RuntimeError("multiple capture not supported in iterator")

        stream = self.stdout or self.stderr
        if stream:
            async for line in redir.read_lines(stream, self._options.encoding):
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


class PipeRunner:
    """PipeRunner is an asynchronous context manager that runs a pipeline.

    ```
    async with pipe.run() as run:
        # process run.stdin, run.stdout, run.stderr (if not None)
    result = run.result()
    ```
    """

    stdin = None
    "Pipeline standard input."

    stdout = None
    "Pipeline standard output."

    stderr = None
    "Pipeline standard error."

    def __init__(self, pipe, *, capturing):
        """`capturing=True` indicates we are within an `async with` block and
        client needs to access `stdin` and `stderr` streams.
        """
        assert len(pipe.commands) > 1

        self._pipe = pipe
        self._cancelled = False
        self._tasks = []
        self._results = None
        self._capturing = capturing
        self._encoding = pipe.options.encoding

    @property
    def name(self):
        "Return name of the pipeline."
        return self._pipe.name

    def result(self):
        "Return `Result` object for PipeRunner."
        return make_result(self._pipe, self._results, self._cancelled)

    def add_task(self, coro, tag=""):
        "Add a background task."
        task_name = f"{self.name}#{tag}"
        task = asyncio.create_task(coro, name=task_name)
        self._tasks.append(task)
        return task

    @log_method(LOG_DETAIL)
    async def _wait(self, *, kill=False):
        "Wait for pipeline to finish."
        assert self._results is None

        if kill:
            LOGGER.info("PipeRunner.wait killing pipe %r", self)
            for task in self._tasks:
                task.cancel()

        cancelled, self._results = await harvest_results(*self._tasks, trustee=self)
        if cancelled:
            self._cancelled = True
        self._tasks.clear()  # clear all tasks when done

    @log_method(LOG_ENTER)
    async def __aenter__(self):
        "Set up redirections and launch pipeline."
        try:
            return await self._start()
        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.warning("PipeRunner enter %r ex=%r", self, ex)
            if _is_cancelled(ex):
                self._cancelled = True
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
            self._cancelled = True
        return suppress

    @log_method(LOG_DETAIL)
    async def _finish(self, exc_value):
        "Wait for pipeline to exit and handle cancellation."
        if exc_value is not None:
            LOGGER.warning("PipeRunner._finish exc_value=%r", exc_value)
            if _is_cancelled(exc_value):
                self._cancelled = True
            await self._wait(kill=True)
            return self._cancelled

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

            if self._capturing:
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
        cmds = list(self._pipe.commands)

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

        # Tag each task name with the index of the command in the pipe.
        self.add_task(first_coro, "0")
        for i, coro in enumerate(middle_coros):
            self.add_task(coro, str(i + 1))
        self.add_task(last_coro, str(len(cmds) - 1))

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
        if self._cancelled:
            cancelled_info = " cancelled"
        result_info = ""
        if self._results:
            result_info = f" results={self._results!r}"
        return f"<PipeRunner {self.name!r}{cancelled_info}{result_info}>"

    async def _readlines(self):
        "Iterate over lines in stdout/stderr"
        if self.stdin or (self.stdout and self.stderr):
            raise RuntimeError("multiple capture not supported in iterator")

        stream = self.stdout or self.stderr
        if stream:
            async for line in redir.read_lines(stream, self._encoding):
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
    "Return true if both stdout and stderr are CAPTURE."
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
