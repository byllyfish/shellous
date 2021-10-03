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
from typing import Any, Optional, TypeVar, Union

from immutables import Map as ImmutableDict

import shellous
from shellous.redirect import STDIN_TYPES, STDOUT_APPEND_TYPES, STDOUT_TYPES, Redirect
from shellous.runner import Runner
from shellous.util import coerce_env

# Sentinel used in "mergable" keyword arguments to indicate that a value
# was not set by the caller. This is an enum class to make UNSET more visible
# in generated documentation.
_UnsetEnum = enum.Enum("UNSET", "UNSET")
_UNSET = _UnsetEnum.UNSET

# Use Unset[T] for unset variable types.
_T = TypeVar("_T")
Unset = Union[_T, _UnsetEnum]


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    "Concrete class for per-command options."

    context: "CmdContext" = field(compare=False, repr=False)
    "Root context object."

    env: Optional[ImmutableDict] = field(default=None, repr=False)
    "Additional environment variables for command."

    inherit_env: bool = True
    "True if subprocess should inherit the current environment variables."

    input: Any = Redirect.DEFAULT
    "Input object to bind to stdin."

    input_close: bool = False
    "True if input object should be closed after subprocess launch."

    output: Any = Redirect.DEFAULT
    "Output object to bind to stdout."

    output_append: bool = False
    "True if output object should be opened in append mode."

    output_close: bool = False
    "True if output object should be closed after subprocess launch."

    error: Any = Redirect.DEFAULT
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

    cancel_timeout: float = 3.0
    "Timeout in seconds that we wait for a cancelled process to terminate."

    cancel_signal: Optional[Any] = signal.SIGTERM
    "The signal sent to terminate a cancelled process."

    alt_name: Optional[str] = None
    "Alternate name for the command to use when logging."

    pass_fds: Iterable[int] = ()
    "File descriptors to pass to the command."

    pass_fds_close: bool = False
    "True if pass_fds should be closed after subprocess launch."

    write_mode: bool = False
    "True if using process substitution in write mode."

    start_new_session: bool = False
    "True if child process should start a new session with setsid()."

    preexec_fn: Any = None
    "Function to call in child process after fork from parent."

    pty: bool = False
    "True if child process should be controlled using a pseudo-terminal (pty)."

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
        if input_ is None:
            input_ = Redirect.DEVNULL
        elif input_ == Redirect.STDOUT:
            raise ValueError("STDOUT is only supported by stderr")

        return dataclasses.replace(
            self,
            input=input_,
            input_close=close,
        )

    def set_stdout(self, output, append, close):
        "Return new options with `output` configured."
        if output is None:
            output = Redirect.DEVNULL
        elif output == Redirect.STDOUT:
            raise ValueError("STDOUT is only supported by stderr")

        return dataclasses.replace(
            self,
            output=output,
            output_append=append,
            output_close=close,
        )

    def set_stderr(self, error, append, close):
        "Return new options with `error` configured."
        if error is None:
            error = Redirect.DEVNULL

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
        "Return new options with given properties updated."
        kwds = {key: value for key, value in kwds.items() if value is not _UNSET}
        return dataclasses.replace(self, **kwds)


@dataclass(frozen=True)
class CmdContext:
    """Concrete class for an immutable execution context."""

    options: Options = None  # type: ignore
    "Default command options."

    def __post_init__(self):
        if self.options is None:
            # Initialize `context` in Options to `self`.
            object.__setattr__(self, "options", Options(self))

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
        cancel_timeout=_UNSET,
        cancel_signal=_UNSET,
        alt_name=_UNSET,
        pass_fds=_UNSET,
        pass_fds_closed=_UNSET,
        write_mode=_UNSET,
        start_new_session=_UNSET,
        preexec_fn=_UNSET,
        pty=_UNSET,
    ) -> "CmdContext":
        "Return new context with custom options set."
        kwargs = locals()
        del kwargs["self"]
        return CmdContext(self.options.set(kwargs))

    def __call__(self, *args):
        "Construct a new command."
        return Command(self._coerce(args), self.options)

    def _cmd_apply(self, cmd, args):
        """Apply arguments to an existing command.

        This method is an extension point.
        """
        return Command(
            cmd.args + self._coerce(args),
            cmd.options,
        )

    def _pipe_apply(self, _pipe, args):  # pylint: disable=no-self-use
        """Apply arguments to an existing pipeline.

        This method is an extension point.
        """
        assert len(args) > 0
        raise TypeError("Calling pipeline with 1 or more arguments.")

    def _coerce(self, args):
        """Flatten lists and coerce arguments to string.

        This method is an extension point.
        """
        result = []
        for arg in args:
            if isinstance(
                arg, (str, bytes, bytearray, os.PathLike, Command, shellous.Pipeline)
            ):
                result.append(arg)
            elif isinstance(arg, (list, tuple)):
                result.extend(self._coerce(arg))
            elif isinstance(arg, (dict, set)):
                raise NotImplementedError("syntax is reserved")
            elif arg is Ellipsis:
                raise NotImplementedError("syntax is reserved")
            elif arg is None:
                raise TypeError("None in argument list")
            else:
                result.append(str(arg))
        return tuple(result)


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
        cancel_timeout: Unset[float] = _UNSET,
        cancel_signal: Unset[Any] = _UNSET,
        alt_name: Unset[Optional[str]] = _UNSET,
        pass_fds: Unset[Iterable[int]] = _UNSET,
        pass_fds_close: Unset[bool] = _UNSET,
        write_mode: Unset[bool] = _UNSET,
        start_new_session: Unset[bool] = _UNSET,
        preexec_fn: Unset[Any] = _UNSET,
        pty: Unset[bool] = _UNSET,
    ) -> "Command":
        """Return new command with custom options set.

        - Set `inherit_env` to False to prevent the command from inheriting
        the parent environment.
        - Set `encoding` to a string encoding like "utf-8", or "utf-8 replace".
        If `encoding` is None, use raw bytes.
        - Set `return_result` to True to return a `Result` object instead of
        the standard output. The `Result` object includes the exit code.
        - Set `incomplete_result` to True to return a `ResultError` object when
        the command is cancelled, instead of a CancelledError.
        - Set `exit_codes` to the set of allowed exit codes that will not raise
        a `ResultError`. None means {0}.
        - Set `cancel_timeout` to the timeout in seconds to wait for a process
        to terminate after sending it the cancel signal.
        - Set `cancel_signal` to the signal used to stop a process when it is
        cancelled.
        - Set `alt_name` to an alternative name of the command to be displayed
        in logs. Used to resolve ambiguity when the actual command name is a
        scripting language.
        - Set `pass_fds` to pass open file descriptors to the command.
        - Set `pass_fds_close` to True to auto-close the `pass_fds`.
        - Set `write_mode` to True when using process substitution for writing.
        - Set `start_new_session` to True to start a new session.
        - Set `preexec_fn` to a function to call in child process.
        - Set `pty` to True to use a pseudo-terminal (pty) to control the child
        process. You may also set `pty` to a 1-arg function to call on the
        child_fd for setup purposes. Setting `pty` forces `start_new_session`
        to True.
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

    def __call__(self, *args):
        "Apply more arguments to the end of the command."
        if not args:
            return self
        return self.options.context._cmd_apply(self, args)

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

    def __invert__(self):
        "Unary ~ operator sets write_mode to True."
        return self.set(write_mode=True)


def _check_args(out, append):
    if append and not isinstance(out, STDOUT_APPEND_TYPES):
        raise TypeError(f"{type(out)} does not support append")
