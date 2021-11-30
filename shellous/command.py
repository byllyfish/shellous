"""Implements the CmdContext and Command classes.

- A CmdContext creates new command objects. You should use the `context`
function to create these, rather than creating them directly.

- A Command specifies the arguments and options used to run a program.
"""

import dataclasses
import enum
import os
import signal
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar, Union

from immutables import Map as ImmutableDict

import shellous
from shellous.redirect import STDIN_TYPES, STDOUT_APPEND_TYPES, STDOUT_TYPES, Redirect
from shellous.runner import Runner
from shellous.util import coerce_env, context_aenter, context_aexit

# Sentinel used in "mergable" keyword arguments to indicate that a value
# was not set by the caller. This is an enum class to make UNSET more visible
# in generated documentation.
_UnsetEnum = enum.Enum("UNSET", "UNSET")
_UNSET = _UnsetEnum.UNSET

# Use Unset[T] for unset variable types.
_T = TypeVar("_T")
Unset = Union[_T, _UnsetEnum]

_Redirect_T = Any  # type: ignore
_Preexec_Fn_T = Optional[Callable[[], None]]  # pylint: disable=invalid-name
_Audit_Fn_T = Optional[Callable[[str, dict], None]]  # pylint: disable=invalid-name


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    "Concrete class for per-command options."

    env: Optional[ImmutableDict] = field(default=None, repr=False)
    "Additional environment variables for command."

    inherit_env: bool = True
    "True if subprocess should inherit the current environment variables."

    input: _Redirect_T = Redirect.DEFAULT
    "Input object to bind to stdin."

    input_close: bool = False
    "True if input object should be closed after subprocess launch."

    output: _Redirect_T = Redirect.DEFAULT
    "Output object to bind to stdout."

    output_append: bool = False
    "True if output object should be opened in append mode."

    output_close: bool = False
    "True if output object should be closed after subprocess launch."

    error: _Redirect_T = Redirect.DEFAULT
    "Error object to bind to stderr."

    error_append: bool = False
    "True if error object should be opened in append mode."

    error_close: bool = False
    "True if error object should be closed after subprocess launch."

    encoding: Optional[str] = "utf-8"
    "Specifies encoding of input/ouput. None means binary."

    return_result: bool = False
    "True if we should return `Result` object instead of the output text/bytes."

    incomplete_result: bool = False
    "True if we should raise `ResultError` after clean up from cancelled task."

    exit_codes: Optional[set] = None
    "Set of exit codes that do not raise a `ResultError`. None means {0}."

    timeout: Optional[float] = None
    "Timeout in seconds that we wait before cancelling the process."

    cancel_timeout: float = 3.0
    "Timeout in seconds that we wait for a cancelled process to terminate."

    cancel_signal: Optional[signal.Signals] = signal.SIGTERM
    "The signal sent to terminate a cancelled process."

    alt_name: Optional[str] = None
    "Alternate name for the command to use when logging."

    pass_fds: Iterable[int] = ()
    "File descriptors to pass to the command."

    pass_fds_close: bool = False
    "True if pass_fds should be closed after subprocess launch."

    writable: bool = False
    "True if using process substitution in write mode."

    _start_new_session: bool = False
    "True if child process should start a new session with setsid()."

    _preexec_fn: _Preexec_Fn_T = None
    "Function to call in child process after fork from parent."

    pty: bool = False
    "True if child process should be controlled using a pseudo-terminal (pty)."

    close_fds: bool = False
    "True if child process should close all file descriptors."

    audit_callback: _Audit_Fn_T = None
    "Function called to audit stages of process execution."

    def merge_env(self):
        "Return our `env` merged with the global environment."
        if self.inherit_env:
            if not self.env:
                return None
            return os.environ | self.env

        if self.env:
            return dict(self.env)  # convert ImmutableDict to dict (uvloop)
        return {}

    def set_stdin(self, input_, close):
        "Return new options with `input` configured."
        input_ = Redirect.from_literal(input_)
        if input_ == Redirect.STDOUT:
            raise ValueError("STDOUT is only supported by stderr")

        return dataclasses.replace(
            self,
            input=input_,
            input_close=close,
        )

    def set_stdout(self, output, append, close):
        "Return new options with `output` configured."
        output = Redirect.from_literal(output)
        if output == Redirect.STDOUT:
            raise ValueError("STDOUT is only supported by stderr")

        return dataclasses.replace(
            self,
            output=output,
            output_append=append,
            output_close=close,
        )

    def set_stderr(self, error, append, close):
        "Return new options with `error` configured."
        error = Redirect.from_literal(error, True)
        return dataclasses.replace(
            self,
            error=error,
            error_append=append,
            error_close=close,
        )

    def set_env(self, env):
        "Return new options with augmented environment."
        current = self.env or ImmutableDict()
        updates = coerce_env(env)
        new_env = current.update(**updates)
        return dataclasses.replace(self, env=new_env)

    def set(self, kwds):
        """Return new options with given properties updated.

        See `Command.set` for option reference.
        """
        kwds = {key: value for key, value in kwds.items() if value is not _UNSET}
        return dataclasses.replace(self, **kwds)


@dataclass(frozen=True)
class CmdContext:
    """Concrete class for an immutable execution context."""

    options: Options = Options()
    "Default command options."

    def stdin(self, input_, *, close=False) -> "CmdContext":
        "Return new context with updated `input` settings."
        new_options = self.options.set_stdin(input_, close)
        return CmdContext(new_options)

    def stdout(self, output, *, append=False, close=False) -> "CmdContext":
        "Return new context with updated `output` settings."
        new_options = self.options.set_stdout(output, append, close)
        return CmdContext(new_options)

    def stderr(self, error, *, append=False, close=False) -> "CmdContext":
        "Return new context with updated `error` settings."
        new_options = self.options.set_stderr(error, append, close)
        return CmdContext(new_options)

    def env(self, **kwds) -> "CmdContext":
        """Return new context with augmented environment."""
        new_options = self.options.set_env(kwds)
        return CmdContext(new_options)

    def set(  # pylint: disable=unused-argument, too-many-locals
        self,
        *,
        inherit_env=_UNSET,
        encoding=_UNSET,
        return_result=_UNSET,
        incomplete_result=_UNSET,
        exit_codes=_UNSET,
        timeout=_UNSET,
        cancel_timeout=_UNSET,
        cancel_signal=_UNSET,
        alt_name=_UNSET,
        pass_fds=_UNSET,
        pass_fds_closed=_UNSET,
        writable=_UNSET,
        _start_new_session=_UNSET,
        _preexec_fn=_UNSET,
        pty=_UNSET,
        close_fds=_UNSET,
        audit_callback=_UNSET,
    ) -> "CmdContext":
        """Return new context with custom options set.

        See `Command.set` for option reference.
        """
        kwargs = locals()
        del kwargs["self"]
        return CmdContext(self.options.set(kwargs))

    def __call__(self, *args):
        "Construct a new command."
        return Command(coerce(args), self.options)


@dataclass(frozen=True)
class Command:
    """A Command instance is lightweight and immutable object that specifies the
    arguments and options used to run a program. Commands do not do anything
    until they are awaited.

    Commands are always created by a CmdContext.

    ```
    # Create a context.
    sh = shellous.context()

    # Create a new command from the context.
    echo = sh("echo", "hello, world")

    # Run the command.
    result = await echo
    ```
    """

    args: tuple[Union[str, bytes, os.PathLike], ...]
    "Command arguments including the program name as first argument."

    options: Options
    "Command options."

    def __post_init__(self):
        "Validate the command."
        if len(self.args) == 0:
            raise ValueError("Command must include program name")

    @property
    def name(self) -> str:
        """Returns the name of the program being run.

        Names longer than 31 characters are truncated. If `alt_name` option
        is set, return that instead.
        """
        if self.options.alt_name:
            return self.options.alt_name
        name = str(self.args[0])
        if len(name) > 31:
            return f"...{name[-31:]}"
        return name

    def stdin(self, input_, *, close=False) -> "Command":
        "Pass `input` to command's standard input."
        new_options = self.options.set_stdin(input_, close)
        return Command(self.args, new_options)

    def stdout(self, output, *, append=False, close=False) -> "Command":
        "Redirect standard output to `output`."
        _check_args(output, append)
        new_options = self.options.set_stdout(output, append, close)
        return Command(self.args, new_options)

    def stderr(self, error, *, append=False, close=False) -> "Command":
        "Redirect standard error to `error`."
        _check_args(error, append)
        new_options = self.options.set_stderr(error, append, close)
        return Command(self.args, new_options)

    def env(self, **kwds) -> "Command":
        """Return new command with augmented environment."""
        new_options = self.options.set_env(kwds)
        return Command(self.args, new_options)

    def set(  # pylint: disable=unused-argument, too-many-locals
        self,
        *,
        inherit_env: Unset[bool] = _UNSET,
        encoding: Unset[Optional[str]] = _UNSET,
        return_result: Unset[bool] = _UNSET,
        incomplete_result: Unset[bool] = _UNSET,
        exit_codes: Unset[Optional[set]] = _UNSET,
        timeout: Unset[Optional[float]] = _UNSET,
        cancel_timeout: Unset[float] = _UNSET,
        cancel_signal: Unset[Optional[signal.Signals]] = _UNSET,
        alt_name: Unset[Optional[str]] = _UNSET,
        pass_fds: Unset[Iterable[int]] = _UNSET,
        pass_fds_close: Unset[bool] = _UNSET,
        writable: Unset[bool] = _UNSET,
        _start_new_session: Unset[bool] = _UNSET,
        _preexec_fn: Unset[_Preexec_Fn_T] = _UNSET,
        pty: Unset[bool] = _UNSET,
        close_fds: Unset[bool] = _UNSET,
        audit_callback: Unset[_Audit_Fn_T] = _UNSET,
    ) -> "Command":
        """Return new command with custom options set.

        **inherit_env** (bool) default=True<br>
        Subprocess should inherit the parent process environment. If this is
        False, the subprocess will only have environment variables specified
        by `Command.env`. If `inherit_env` is True, the parent process
        environment is augmented/overriden by any variables specified in
        `Command.env`.

        **encoding** (str | None) default="utf-8"<br>
        String encoding to use for subprocess input/output. If `encoding` is
        None, use raw bytes. To specify `errors`, append it after a space. For
        example, use "utf-8 replace" to specify "utf-8" with errors "replace".

        **return_result** (bool) default=False<br>
        When True, return a `Result` object instead of the standard output.

        **incomplete_result** (bool) default=False<br>
        When True, raise a `ResultError` when the command is cancelled. On the
        plus side, this gives you access to the initial output of the command.
        On the negative side, the `ResultError` swallows the `CancelledError`.
        Your code may need to re-raise a CancelledError after dealing with the
        partial result. When `incomplete_result` is False, a cancelled command
        will raise a `CancelledError`.

        **exit_codes** (set[int] | None) default=None<br>
        Set of allowed exit codes that will not raise a `ResultError`. By default,
        `exit_codes` is `None` which indicates that 0 is the only valid exit
        status. Any other exit status will raise a `ResultError`. In addition to
        sets of integers, you can use a `range` object, e.g. `range(256)` for
        any positive exit status.

        **timeout** (float | None) default=None<br>
        Timeout in seconds to wait before we cancel the process. The timer
        begins immediately after the process is launched. This differs from
        using `asyncio.wait_for` which includes the process launch time also.
        If timeout is None (the default), there is no timeout.

        **cancel_timeout** (float) default=3.0 seconds<br>
        Timeout in seconds to wait for a process to exit after sending it a
        `cancel_signal`. If the process does not exit after waiting for
        `cancel_timeout` seconds, we send a kill signal to the process.

        **cancel_signal** (signals.Signal | None) default=signal.SIGTERM<br>
        Signal sent to a process when it is cancelled. If `cancel_signal` is
        None, send a `SIGKILL` on Unix and `SIGTERM` (TerminateProcess) on
        Windows.

        **alt_name** (str| None) default=None<br>
        Alternative name of the command displayed in logs. Used to resolve
        ambiguity when the actual command name is a scripting language.

        **pass_fds** (Iterable[int]) default=()<br>
        Specify open file descriptors to pass to the subprocess.

        **pass_fds_close** (bool) default=False<br>
        Close the file descriptors in `pass_fds` immediately in the current
        process immediately after launching the subprocess.

        **writable** (bool) default=False<br>
        Used to indicate process substitution is writing.

        **_start_new_session** (bool) default=False<br>
        Provided for testing purposes only.

        **_preexec_fn** (Callable() | None) default=None<br>
        Provided for testing purposes only.

        **pty** (bool | Callable(int)) default=False<br>
        If True, use a pseudo-terminal (pty) to control the child process.
        If `pty` is set to a callable, the function must take one int argument
        for the child side of the pty. The function is called to set the child
        pty's termios settings before spawning the subprocess.

        shellous provides three utility functions: `shellous.cooked`,
        `shellous.raw` and `shellous.cbreak` that can be used as arguments to
        the `pty` option.

        **close_fds** (bool) default=False<br>
        Close all unnecessary file descriptors in the child process. This
        defaults to False to align with `posix_spawn` requirements. Please refer
        to the documentation on [inheritance of file descriptors](
        https://docs.python.org/3/library/os.html#inheritance-of-file-descriptors)
        for behavior on Unix and Windows.

        **audit_callback** (Callable(phase, info) | None) default=None<br>
        Specify a function to call as the command execution goes through its
        lifecycle. `audit_callback` is a function called with two arguments,
        *phase* and *info*.

        *phase* can be one of three values:

            "start": The process is about to start.

            "stop": The process finally stopped.

            "signal": The process is being sent a signal.

        *info* is a dictionary providing more information for the callback. The
        following keys are currently defined:

            "runner" (Runner): Reference to the Runner object.

            "failure" (str): When phase is "stop", optional string with the
            name of the exception from launching the process.

            "signal" (str): When phase is "signal", the signal name/number
            sent to the process, e.g. "Signals.SIGTERM".

        The primary use case for `audit_callback` is measuring how long each
        command takes to run and exporting this information to a metrics
        framework like Prometheus.
        """
        kwargs = locals()
        del kwargs["self"]
        return Command(self.args, self.options.set(kwargs))

    def _replace_args(self, new_args):
        """Return new command with arguments replaced by `new_args`.

        Arguments are NOT type-checked by the context. Program name must be the
        exact same object.
        """
        assert new_args
        assert new_args[0] is self.args[0]
        return Command(tuple(new_args), self.options)

    def coro(self, *, _run_future=None):
        "Return coroutine object to run awaitable."
        return Runner.run_command(self, _run_future=_run_future)

    def run(self) -> Runner:
        """Return a `Runner` to run the process incrementally.

        ```
        async with cmd.run() as run:
            # do something with run.stdin, run.stdout, run.stderr...
            # close run.stdin to signal we're done...
        result = run.result()
        ```
        """
        return Runner(self)

    def __await__(self):
        "Run process and return the standard output."
        return self.coro().__await__()

    async def __aenter__(self):
        "Enter the async context manager."
        return await context_aenter(id(self), self.run())

    async def __aexit__(self, exc_type, exc_value, exc_tb):
        "Exit the async context manager."
        return await context_aexit(id(self), exc_type, exc_value, exc_tb)

    def __aiter__(self):
        "Return async iterator to iterate over output lines."
        return self._readlines()

    async def _readlines(self):
        "Async generator to iterate over lines."
        async with self.run() as run:
            async for line in run:
                yield line

    def __call__(self, *args):
        "Apply more arguments to the end of the command."
        if not args:
            return self
        return Command(self.args + coerce(args), self.options)

    def __str__(self):
        """Return string representation for command.

        Display the full name of the command only. Don't include arguments or
        environment variables.
        """
        return str(self.args[0])

    def __or__(self, rhs):
        "Bitwise or operator is used to build pipelines."
        if isinstance(rhs, STDOUT_TYPES):
            return self.stdout(rhs)
        return shellous.Pipeline.create(self) | rhs

    def __ror__(self, lhs):
        "Bitwise or operator is used to build pipelines."
        if isinstance(lhs, STDIN_TYPES):
            return self.stdin(lhs)
        return NotImplemented

    def __rshift__(self, rhs):
        "Right shift operator is used to build pipelines."
        if isinstance(rhs, STDOUT_APPEND_TYPES):
            return self.stdout(rhs, append=True)
        return NotImplemented

    def __mod__(self, rhs):
        "Modulo operator is used to concatenate commands."
        if isinstance(rhs, Command):
            return self(rhs.args)
        return NotImplemented

    @property
    def writable(self):
        "Set `writable` to True."
        return self.set(writable=True)


def _check_args(out, append):
    if append and not isinstance(out, STDOUT_APPEND_TYPES):
        raise TypeError(f"{type(out)} does not support append")


def coerce(args):
    """Flatten lists and coerce arguments to string."""
    result = []
    for arg in args:
        if isinstance(
            arg, (str, bytes, bytearray, os.PathLike, Command, shellous.Pipeline)
        ):
            result.append(arg)
        elif isinstance(arg, (list, tuple)):
            result.extend(coerce(arg))
        elif isinstance(arg, (dict, set)):
            raise NotImplementedError("syntax is reserved")
        elif arg is Ellipsis:
            raise NotImplementedError("syntax is reserved")
        elif arg is None:
            raise TypeError("None in argument list")
        else:
            result.append(str(arg))
    return tuple(result)
