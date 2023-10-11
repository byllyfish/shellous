"Implements utilities to run a command."

import asyncio
import io
import os
import sys
from logging import Logger
from pathlib import Path
from types import TracebackType
from typing import Any, AsyncIterator, Coroutine, Optional, TextIO, TypeVar, Union, cast

import shellous
import shellous.redirect as redir
from shellous import pty_util
from shellous.harvest import harvest, harvest_results
from shellous.log import LOG_DETAIL, LOGGER, log_method, log_timer
from shellous.redirect import Redirect
from shellous.result import (
    RESULT_STDERR_LIMIT,
    Result,
    check_result,
    convert_result_list,
)
from shellous.util import (
    BSD_DERIVED,
    SupportsClose,
    close_fds,
    encode_bytes,
    poll_wait_pid,
    uninterrupted,
    verify_dev_fd,
)

# pyright: reportPrivateUsage=false

_KILL_TIMEOUT = 3.0
_CLOSE_TIMEOUT = 0.25
_UNKNOWN_EXIT_CODE = 255

EVENT_SHELLOUS_EXEC = "shellous.exec"
"""Audit event raised by sys.audit() when shellous runs a subprocess.
The audit event has one argument: the name of the command.
"""

CANCELLED_EXIT_CODE = -1000
"""Special exit code used in audit callbacks when the process launch itself
was cancelled."""


_T = TypeVar("_T")


def _is_cancelled(ex: BaseException):
    return isinstance(ex, asyncio.CancelledError)


def _is_writable(cmd: "Union[shellous.Command[Any], shellous.Pipeline[Any]]"):
    "Return true if command/pipeline has `_writable` set."
    if isinstance(cmd, shellous.Pipeline):
        # Pipelines need to check both the last/first commands.
        return cmd.options._writable or cmd[0].options._writable
    return cmd.options._writable


class _RunOptions:
    """_RunOptions is context manager to assist in running a command.

    This class sets up low-level I/O redirection and helps close open file
    descriptors.

    ```
    with _RunOptions(cmd) as options:
        proc = await create_subprocess_exec(*options.pos_args, **options.kwd_args)
        # etc.
    ```
    """

    command: "shellous.Command[Any]"
    encoding: str
    open_fds: list[Union[int, SupportsClose]]
    input_bytes: Optional[bytes]
    pos_args: list[Union[str, bytes, os.PathLike[Any]]]
    kwd_args: dict[str, Any]
    subcmds: "list[Union[shellous.Command[Any], shellous.Pipeline[Any]]]"
    pty_fds: Optional[pty_util.PtyFds]
    output_bytes: Optional[bytearray]
    error_bytes: Optional[bytearray]
    is_stderr_only: bool = False

    def __init__(self, command: "shellous.Command[Any]"):
        self.command = command
        self.encoding = command.options.encoding
        self.open_fds = []
        self.input_bytes = None
        self.pos_args = []
        self.kwd_args = {}
        self.subcmds = []
        self.pty_fds = None
        self.output_bytes = None
        self.error_bytes = None

    def __enter__(self):
        "Set up I/O redirections."
        _cmd = self.command

        try:
            if _uses_process_substitution(self.command):
                self.pos_args = self._setup_process_substitution()
            else:
                self.pos_args = cast(
                    list[Union[str, bytes, os.PathLike[Any]]], list(self.command.args)
                )

            self._setup_redirects()
            self._setup_pass_fds()

            # If executable does not include an absolute/relative directory,
            # resolve it using PATH.
            executable = self.pos_args[0]
            if not os.path.dirname(executable):
                found_executable = _cmd.options.which(executable)
                if found_executable is None:
                    raise FileNotFoundError(executable)
                self.pos_args[0] = found_executable

        except Exception as ex:
            if LOG_DETAIL:
                LOGGER.debug("_RunOptions.enter %r ex=%r", self.command.name, ex)
            _cleanup(self.command)
            raise

        assert _cmd is self.command, "self.command should remain unchanged"
        return self

    def __exit__(
        self,
        _exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        _exc_tb: Optional[TracebackType],
    ):
        "Make sure those file descriptors are cleaned up."
        close_fds(self.open_fds)
        if exc_value:
            if LOG_DETAIL:
                LOGGER.debug(
                    "_RunOptions.exit %r exc_value=%r",
                    self.command.name,
                    exc_value,
                )
            for subcmd in self.subcmds:
                _cleanup(subcmd)

    def _setup_process_substitution(self) -> list[Union[str, bytes, os.PathLike[Any]]]:
        """Set up process substitution.

        Returns new command line arguments with /dev/fd path substitutions.
        """
        if sys.platform == "win32":
            raise RuntimeError("process substitution not supported")  # pragma: no cover

        new_args: list[Union[str, bytes, os.PathLike[Any]]] = []
        new_fds: list[int] = []

        for arg in self.command.args:
            if not isinstance(arg, (shellous.Command, shellous.Pipeline)):
                new_args.append(arg)
                continue

            (read_fd, write_fd) = os.pipe()
            if _is_writable(arg):
                new_args.append(f"/dev/fd/{write_fd}")
                new_fds.append(write_fd)
                subcmd = arg.stdin(read_fd, close=True)
            else:
                new_args.append(f"/dev/fd/{read_fd}")
                new_fds.append(read_fd)
                subcmd = arg.stdout(write_fd, close=True)

            self.subcmds.append(subcmd)

        # We need to include `new_fds` in our `pass_fds` list. We also need
        # to close them after process launch.
        self.kwd_args["pass_fds"] = new_fds
        self.kwd_args["close_fds"] = True
        self.open_fds.extend(new_fds)

        if BSD_DERIVED:
            verify_dev_fd(new_fds[0])

        return new_args

    def _setup_redirects(self):
        "Set up I/O redirections."
        options = self.command.options

        stdin, input_bytes = self._setup_input(
            Redirect.from_default(options.input, 0, options.pty),
            options.input_close,
            self.encoding,
        )

        # Handle "stderr only" case when stdout -> DEVNULL, stderr -> STDOUT.
        if options.output == Redirect.DEVNULL and options.error == Redirect.STDOUT:
            self.is_stderr_only = True

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
        start_session = options._start_new_session
        preexec_fn = options._preexec_fn

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
        self.kwd_args.update(
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=options.runtime_env(),
            start_new_session=start_session,
            preexec_fn=preexec_fn,
        )

    def _setup_pass_fds(self):
        "Set up `pass_fds` and `close_fds` if pass_fds is configured."
        options = self.command.options

        if options.pass_fds:
            # Setting pass_fds causes close_fds to be True. Set close_fds to
            # True manually to avoid a RuntimeWarning.
            self.kwd_args["close_fds"] = True
            self.kwd_args.setdefault("pass_fds", []).extend(options.pass_fds)
            if options.pass_fds_close:
                self.open_fds.extend(options.pass_fds)
        elif "close_fds" not in self.kwd_args:
            # Only set close_fds if it is not already set.
            self.kwd_args["close_fds"] = options.close_fds

    def _setup_input(self, input_: Any, close: bool, encoding: str):
        "Set up process input."
        assert input_ is not None

        stdin = asyncio.subprocess.PIPE
        input_bytes = None

        if isinstance(input_, (bytes, bytearray)):
            input_bytes = bytes(input_)
        elif isinstance(input_, Path):
            stdin = open(input_, "rb")  # pylint: disable=consider-using-with
            self.open_fds.append(stdin)
        elif isinstance(input_, Redirect) and input_.is_custom():
            # Custom support for Redirect constants.
            if input_ == Redirect.INHERIT:
                stdin = sys.stdin
            elif input_ == Redirect.BUFFER:
                raise TypeError(f"unsupported input type: {input_!r}")
            else:
                # CAPTURE uses stdin == PIPE.
                assert input_ == Redirect.CAPTURE
                assert stdin == asyncio.subprocess.PIPE
        elif isinstance(input_, int):  # file descriptor
            stdin = input_
            if close:
                self.open_fds.append(stdin)
        elif isinstance(input_, str):
            input_bytes = encode_bytes(input_, encoding)
        elif isinstance(input_, (asyncio.StreamReader, io.BytesIO, io.StringIO)):
            # Shellous-supported input classes.
            assert stdin == asyncio.subprocess.PIPE
            assert input_bytes is None
        else:
            raise TypeError(f"unsupported input type: {input_!r}")

        return stdin, input_bytes

    def _setup_output(self, output: Any, append: bool, close: bool, sys_stream: TextIO):
        "Set up process output. Used for both stdout and stderr."
        assert output is not None

        stdout = asyncio.subprocess.PIPE

        if isinstance(output, os.PathLike):
            mode = "ab" if append else "wb"
            stdout = open(  # pylint: disable=consider-using-with
                cast(os.PathLike[str], output), mode=mode
            )
            self.open_fds.append(stdout)
        elif self.is_stderr_only and sys_stream == sys.stderr:
            assert output == Redirect.STDOUT
            self.output_bytes = bytearray()
            assert stdout == asyncio.subprocess.PIPE
        elif isinstance(output, Redirect) and output.is_custom():
            # Custom support for Redirect constants.
            if output == Redirect.BUFFER:
                if sys_stream == sys.stdout:
                    self.output_bytes = bytearray()
                else:
                    self.error_bytes = bytearray()
                assert stdout == asyncio.subprocess.PIPE
            elif output == Redirect.INHERIT:
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
            _set_position(output, append)
            assert stdout == asyncio.subprocess.PIPE
        elif isinstance(output, io.IOBase):
            # Client-managed File-like object.
            _set_position(output, append)
            stdout = output
        else:
            raise TypeError(f"unsupported output type: {output!r}")

        return stdout

    def _setup_pty1(
        self,
        stdin: Any,
        stdout: Any,
        stderr: Any,
        pty: pty_util.PtyAdapterOrBool,
    ):
        """Set up pseudo-terminal and return (stdin, stdout, stderr, preexec_fn).

        Initializes `self.pty_fds`.
        """
        self.pty_fds = pty_util.open_pty(pty)

        # On BSD-derived systems like FreeBSD and Darwin, we delay closing the
        # pty's child_fd in the parent process until after the first read
        # succeeds. On Linux, we close the child_fd as soon as possible.

        if not BSD_DERIVED:
            self.open_fds.append(self.pty_fds.child_fd)

        if LOG_DETAIL:
            LOGGER.debug("_setup_pty1: %r", self.pty_fds)

        child_fd = int(self.pty_fds.child_fd)

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
    async with Runner(cmd) as run:
        # process streams: run.stdin, run.stdout, run.stderr (if not None)
    result = run.result()
    ```
    """

    stdin: Optional[asyncio.StreamWriter] = None
    "Process standard input."

    stdout: Optional[asyncio.StreamReader] = None
    "Process standard output."

    stderr: Optional[asyncio.StreamReader] = None
    "Process standard error."

    _options: _RunOptions
    _tasks: list[asyncio.Task[Any]]
    _proc: Optional["asyncio.subprocess.Process"] = None
    _cancelled: bool = False
    _timer: Optional[asyncio.TimerHandle] = None
    _timed_out: bool = False
    _last_signal: Optional[int] = None

    def __init__(self, command: "shellous.Command[Any]"):
        self._options = _RunOptions(command)
        self._tasks = []

    @property
    def name(self) -> str:
        "Return name of process being run."
        return self.command.name

    @property
    def command(self) -> "shellous.Command[Any]":
        "Return the command being run."
        return self._options.command

    @property
    def pid(self) -> Optional[int]:
        "Return the command's process ID."
        if not self._proc:
            return None
        return self._proc.pid

    @property
    def returncode(self) -> Optional[int]:
        "Process's exit code."
        if not self._proc:
            if self._cancelled:
                # The process was cancelled before starting.
                return CANCELLED_EXIT_CODE
            return None
        code = self._proc.returncode
        if code == _UNKNOWN_EXIT_CODE and self._last_signal is not None:
            # Rarely after sending a SIGTERM, waitpid fails to locate the child
            # process. In this case, map the status to the last signal we sent.
            return -self._last_signal  # pylint: disable=invalid-unary-operand-type
        return code

    @property
    def cancelled(self) -> bool:
        "Return True if the command was cancelled."
        return self._cancelled

    @property
    def pty_fd(self) -> Optional[int]:
        """The file descriptor used to communicate with the child PTY process.

        Returns None if the process is not using a PTY.
        """
        pty_fds = self._options.pty_fds
        if pty_fds is not None:
            return pty_fds.parent_fd
        return None

    @property
    def pty_eof(self) -> Optional[bytes]:
        """Byte sequence used to indicate EOF when written to the PTY child.

        Returns None if process is not using a PTY.
        """
        pty_fds = self._options.pty_fds
        if pty_fds is not None:
            return pty_fds.eof
        return None

    def result(self) -> Result:
        "Check process exit code and raise a ResultError if necessary."
        code = self.returncode
        assert code is not None

        result = Result(
            exit_code=code,
            output_bytes=bytes(self._options.output_bytes or b""),
            error_bytes=bytes(self._options.error_bytes or b""),
            cancelled=self._cancelled,
            encoding=self._options.encoding,
        )

        return check_result(
            result,
            self.command.options,
            self._cancelled,
            self._timed_out,
        )

    def add_task(
        self,
        coro: Coroutine[Any, Any, _T],
        tag: str = "",
    ) -> asyncio.Task[_T]:
        "Add a background task."
        task_name = f"{self.name}#{tag}"
        task = asyncio.create_task(coro, name=task_name)
        self._tasks.append(task)
        return task

    def send_signal(self, sig: int) -> None:
        "Send an arbitrary signal to the process if it is running."
        if self.returncode is None:
            self._signal(sig)

    def cancel(self) -> None:
        "Cancel the running process if it is running."
        if self.returncode is None:
            self._signal(self.command.options.cancel_signal)

    def _is_bsd_pty(self) -> bool:
        "Return true if we're running a pty on BSD."
        return BSD_DERIVED and bool(self._options.pty_fds)

    @log_method(LOG_DETAIL)
    async def _wait(self) -> None:
        "Normal wait for background I/O tasks and process to finish."
        assert self._proc

        try:
            if self._tasks:
                await harvest(*self._tasks, trustee=self)
            if self._is_bsd_pty():
                await self._waiter()

        except asyncio.CancelledError:
            LOGGER.debug("Runner.wait cancelled %r", self)
            self._set_cancelled()
            self._tasks.clear()  # all tasks were cancelled
            await self._kill()

        except Exception as ex:
            LOGGER.debug("Runner.wait exited with error %r ex=%r", self, ex)
            self._tasks.clear()  # all tasks were cancelled
            await self._kill()
            raise  # re-raise exception

    @log_method(LOG_DETAIL)
    async def _wait_pid(self):
        "Manually poll `waitpid` until process finishes."
        assert self._is_bsd_pty()

        while True:
            assert self._proc is not None  # (pyright)

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
                self._signal(cancel_signal)

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

    def _signal(self, sig: Optional[int]):
        "Send a signal to the process."
        assert self._proc is not None  # (pyright)

        if LOG_DETAIL:
            LOGGER.debug("Runner.signal %r signal=%r", self, sig)
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
            self._signal(None)
            await harvest(self._waiter(), timeout=_KILL_TIMEOUT, trustee=self)
        except asyncio.TimeoutError as ex:
            # Manually check if the process is still running.
            if poll_wait_pid(self._proc):
                LOGGER.warning("%r process reaped manually %r", self, self._proc)
            else:
                LOGGER.error("%r failed to kill process %r", self, self._proc)
                raise RuntimeError(f"Unable to kill process {self._proc!r}") from ex

    @log_method(LOG_DETAIL)
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
            if self._cancelled and self.command.options._catch_cancelled_error:
                # Raises ResultError instead of CancelledError.
                self.result()

    @log_method(LOG_DETAIL)
    async def _start(self):
        "Set up redirections and launch subprocess."
        # assert self._proc is None
        assert not self._tasks

        try:
            # Set up subprocess arguments and launch subprocess.
            with self._options as opts:
                await self._subprocess_spawn(opts)

            assert self._proc is not None
            stdin = self._proc.stdin
            stdout = self._proc.stdout
            stderr = self._proc.stderr

            # Assign pty streams.
            if opts.pty_fds:
                assert (stdin, stdout) == (None, None)
                stdin, stdout = opts.pty_fds.writer, opts.pty_fds.reader

            if stderr is not None:
                limit = -1
                if opts.error_bytes is not None:
                    error = opts.error_bytes
                    limit = RESULT_STDERR_LIMIT
                elif opts.is_stderr_only:
                    assert stdout is None
                    assert opts.output_bytes is not None
                    error = opts.output_bytes
                else:
                    error = opts.command.options.error
                stderr = self._setup_output_sink(
                    stderr, error, opts.encoding, "stderr", limit
                )

            if stdout is not None:
                limit = -1
                if opts.output_bytes is not None:
                    output = opts.output_bytes
                else:
                    output = opts.command.options.output
                stdout = self._setup_output_sink(
                    stdout, output, opts.encoding, "stdout", limit
                )

            if stdin is not None:
                stdin = self._setup_input_source(stdin, opts)

        except (Exception, asyncio.CancelledError) as ex:
            LOGGER.debug("Runner._start %r ex=%r", self, ex)
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
    async def _subprocess_spawn(self, opts: _RunOptions):
        "Start the subprocess."
        assert self._proc is None

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
    async def _subprocess_exec(self, opts: _RunOptions):
        "Start the subprocess and assign to `self.proc`."
        with log_timer("asyncio.create_subprocess_exec"):
            sys.audit(EVENT_SHELLOUS_EXEC, opts.pos_args[0])
            with pty_util.set_ignore_child_watcher(
                BSD_DERIVED and opts.pty_fds is not None
            ):
                self._proc = await asyncio.create_subprocess_exec(
                    *opts.pos_args,
                    **opts.kwd_args,
                )

    @log_method(LOG_DETAIL)
    async def _waiter(self):
        "Run task that waits for process to exit."
        assert self._proc is not None  # (pyright)

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

    def _start_timer(self, timeout: Optional[float]):
        "Start an optional timer to cancel the process if `timeout` desired."
        assert self._timer is None
        if timeout is not None:
            loop = asyncio.get_running_loop()
            self._timer = loop.call_later(
                timeout,
                self._set_timer_expired,
                asyncio.current_task(),
            )

    def _set_timer_expired(self, main_task: asyncio.Task[Any]):
        "Set a flag when the timer expires and cancel the main task."
        self._timed_out = True
        self._timer = None
        main_task.cancel()

    def _stop_timer(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _setup_input_source(
        self,
        stream: asyncio.StreamWriter,
        opts: _RunOptions,
    ):
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
            input_bytes = encode_bytes(source.getvalue(), opts.encoding)
            self.add_task(redir.write_stream(input_bytes, stream, eof), tag)
            return None

        return stream

    def _setup_output_sink(
        self,
        stream: asyncio.StreamReader,
        sink: Any,
        encoding: str,
        tag: str,
        limit: int = -1,
    ) -> Optional[asyncio.StreamReader]:
        "Set up a task to write to custom output sink."
        if isinstance(sink, io.StringIO):
            self.add_task(redir.copy_stringio(stream, sink, encoding), tag)
            return None

        if isinstance(sink, io.BytesIO):
            self.add_task(redir.copy_bytesio(stream, sink), tag)
            return None

        if isinstance(sink, bytearray):
            # N.B. `limit` is only supported for bytearray.
            if limit >= 0:
                self.add_task(redir.copy_bytearray_limit(stream, sink, limit), tag)
            else:
                self.add_task(redir.copy_bytearray(stream, sink), tag)
            return None

        if isinstance(sink, Logger):
            self.add_task(redir.copy_logger(stream, sink, encoding), tag)
            return None

        if isinstance(sink, asyncio.StreamWriter):
            self.add_task(redir.copy_streamwriter(stream, sink), tag)
            return None

        return stream

    @log_method(LOG_DETAIL)
    async def __aexit__(
        self,
        _exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        _exc_tb: Optional[TracebackType],
    ):
        "Wait for process to exit and handle cancellation."
        suppress = False
        try:
            suppress = await self._finish(exc_value)
        except asyncio.CancelledError:
            LOGGER.debug("Runner cancelled inside _finish %r", self)
            self._set_cancelled()
        finally:
            self._stop_timer()  # failsafe just in case
            self._audit_callback("stop")
        # If `timeout` expired, raise TimeoutError rather than CancelledError.
        if (
            self._cancelled
            and self._timed_out
            and not self.command.options._catch_cancelled_error
        ):
            raise asyncio.TimeoutError()
        return suppress

    @log_method(LOG_DETAIL)
    async def _finish(self, exc_value: Union[BaseException, None]):
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
        assert self._proc is not None

        if self._options.pty_fds:
            self._options.pty_fds.close()

        # Make sure the transport is closed (for asyncio and uvloop).
        self._proc._transport.close()  # pyright: ignore

        # _close can be called when unwinding exceptions. We need to handle
        # the case that the process has not exited yet.
        if self._proc.returncode is None:
            LOGGER.critical("Runner._close process still running %r", self._proc)
            return

        try:
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

    def _audit_callback(
        self,
        phase: str,
        *,
        failure: str = "",
        signal: Optional[int] = None,
    ):
        "Call `audit_callback` if there is one."
        callback = self.command.options.audit_callback
        if callback:
            sig = _signame(signal) if phase == "signal" else ""
            info: shellous.AuditEventInfo = {
                "runner": self,
                "failure": failure,
                "signal": sig,
            }
            callback(phase, info)

    def __repr__(self) -> str:
        "Return string representation of Runner."
        cancelled = " cancelled" if self._cancelled else ""
        if self._proc:
            procinfo = f" pid={self._proc.pid} exit_code={self.returncode}"
        else:
            procinfo = " pid=None"
        return f"<Runner {self.name!r}{cancelled}{procinfo}>"

    async def _readlines(self):
        "Iterate over lines in stdout/stderr"
        stream = self.stdout or self.stderr
        if stream:
            async for line in redir.read_lines(stream, self._options.encoding):
                yield line

    def __aiter__(self) -> AsyncIterator[str]:
        "Return asynchronous iterator over stdout/stderr."
        return self._readlines()

    @staticmethod
    async def run_command(
        command: "shellous.Command[Any]",
        *,
        _run_future: Optional[asyncio.Future["Runner"]] = None,
    ) -> Union[str, Result]:
        "Run a command. This is the main entry point for Runner."
        if not _run_future and _is_multiple_capture(command):
            LOGGER.warning("run_command: multiple capture requires 'async with'")
            _cleanup(command)
            raise ValueError("multiple capture requires 'async with'")

        async with Runner(command) as run:
            if _run_future is not None:
                # Return streams to caller in another task.
                _run_future.set_result(run)

        result = run.result()
        if command.options._return_result:
            return result
        return result.output


class PipeRunner:
    """PipeRunner is an asynchronous context manager that runs a pipeline.

    ```
    async with pipe.run() as run:
        # process run.stdin, run.stdout, run.stderr (if not None)
    result = run.result()
    ```
    """

    stdin: Optional[asyncio.StreamWriter] = None
    "Pipeline standard input."

    stdout: Optional[asyncio.StreamReader] = None
    "Pipeline standard output."

    stderr: Optional[asyncio.StreamReader] = None
    "Pipeline standard error."

    _pipe: "shellous.Pipeline[Any]"
    _capturing: bool
    _tasks: list[asyncio.Task[Any]]
    _encoding: str
    _cancelled: bool = False
    _results: Optional[list[Union[BaseException, Result]]] = None

    def __init__(self, pipe: "shellous.Pipeline[Any]", *, capturing: bool):
        """`capturing=True` indicates we are within an `async with` block and
        client needs to access `stdin` and `stderr` streams.
        """
        assert len(pipe.commands) > 1

        self._pipe = pipe
        self._cancelled = False
        self._tasks = []
        self._capturing = capturing
        self._encoding = pipe.options.encoding

    @property
    def name(self) -> str:
        "Return name of the pipeline."
        return self._pipe.name

    def result(self) -> Result:
        "Return `Result` object for PipeRunner."
        assert self._results is not None

        return check_result(
            convert_result_list(self._results, self._cancelled),
            self._pipe.options,
            self._cancelled,
        )

    def add_task(
        self,
        coro: Coroutine[Any, Any, _T],
        tag: str = "",
    ) -> asyncio.Task[_T]:
        "Add a background task."
        task_name = f"{self.name}#{tag}"
        task = asyncio.create_task(coro, name=task_name)
        self._tasks.append(task)
        return task

    @log_method(LOG_DETAIL)
    async def _wait(self, *, kill: bool = False):
        "Wait for pipeline to finish."
        assert self._results is None

        if kill:
            LOGGER.debug("PipeRunner.wait killing pipe %r", self)
            for task in self._tasks:
                task.cancel()

        cancelled, self._results = await harvest_results(*self._tasks, trustee=self)
        if cancelled:
            self._cancelled = True
        self._tasks.clear()  # clear all tasks when done

    @log_method(LOG_DETAIL)
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

    @log_method(LOG_DETAIL)
    async def __aexit__(
        self,
        _exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        _exc_tb: Optional[TracebackType],
    ):
        "Wait for pipeline to exit and handle cancellation."
        suppress = False
        try:
            suppress = await self._finish(exc_value)
        except asyncio.CancelledError:
            LOGGER.warning("PipeRunner cancelled inside _finish %r", self)
            self._cancelled = True
        return suppress

    @log_method(LOG_DETAIL)
    async def _finish(self, exc_value: Optional[BaseException]) -> bool:
        "Wait for pipeline to exit and handle cancellation."
        if exc_value is not None:
            LOGGER.warning("PipeRunner._finish exc_value=%r", exc_value)
            if _is_cancelled(exc_value):
                self._cancelled = True
            await self._wait(kill=True)
            return self._cancelled

        await self._wait()
        return False

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

    def _setup_pipeline(self, open_fds: list[int]):
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
            cmds[i] = cmds[i].set(_return_result=True, _catch_cancelled_error=True)

        return cmds

    @log_method(LOG_DETAIL)
    async def _setup_capturing(self, cmds: "list[shellous.Command[Any]]"):
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

    def __repr__(self) -> str:
        "Return string representation of PipeRunner."
        cancelled_info = ""
        if self._cancelled:
            cancelled_info = " cancelled"
        result_info = ""
        if self._results:
            result_info = f" results={self._results!r}"
        return f"<PipeRunner {self.name!r}{cancelled_info}{result_info}>"

    async def _readlines(self) -> AsyncIterator[str]:
        "Iterate over lines in stdout/stderr"
        stream = self.stdout or self.stderr
        if stream:
            async for line in redir.read_lines(stream, self._encoding):
                yield line

    def __aiter__(self) -> AsyncIterator[str]:
        "Return asynchronous iterator over stdout/stderr."
        return self._readlines()

    @staticmethod
    async def run_pipeline(pipe: "shellous.Pipeline[Any]") -> Union[str, Result]:
        "Run a pipeline. This is the main entry point for PipeRunner."
        run = PipeRunner(pipe, capturing=False)
        async with run:
            pass

        result = run.result()
        if pipe.options._return_result:
            return result
        return result.output


def _uses_process_substitution(cmd: "shellous.Command[Any]") -> bool:
    "Return True if command uses process substitution."
    return any(
        isinstance(arg, (shellous.Command, shellous.Pipeline)) for arg in cmd.args
    )


def _is_multiple_capture(cmd: "shellous.Command[Any]"):
    "Return true if both stdout and stderr are CAPTURE."
    output = Redirect.from_default(cmd.options.output, 1, cmd.options.pty)
    error = Redirect.from_default(cmd.options.error, 2, cmd.options.pty)
    return output == Redirect.CAPTURE and error == Redirect.CAPTURE


def _cleanup(command: "Union[shellous.Command[Any], shellous.Pipeline[Any]]"):
    "Close remaining file descriptors that need to be closed."

    def _add_close(close: bool, fdesc: Any):
        if close:
            if isinstance(fdesc, (int, io.IOBase)):
                open_fds.append(fdesc)

    open_fds: list[Any] = []

    _add_close(command.options.input_close, command.options.input)
    _add_close(command.options.output_close, command.options.output)
    _add_close(command.options.error_close, command.options.error)

    if command.options.pass_fds_close:
        open_fds.extend(command.options.pass_fds)

    close_fds(open_fds)


def _signame(signal: Any) -> str:
    "Return string name of signal."
    if signal is None:
        return "SIGKILL"  # SIGKILL (even on win32)
    try:
        return signal.name
    except AttributeError:
        return str(signal)


def _set_position(output: Any, append: bool):
    "Truncate/seek output stream object."
    if isinstance(output, bytearray):
        if not append:
            output.clear()
    elif isinstance(output, io.IOBase):
        if output.seekable():
            if append:
                output.seek(0, io.SEEK_END)
            else:
                output.truncate(0)
                output.seek(0, io.SEEK_SET)
