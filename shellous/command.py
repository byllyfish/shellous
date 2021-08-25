"""Implements the Context and Command classes.

- A Context creates new command objects.
- A Command specifies the arguments and options used to run a program.
"""

import asyncio
import dataclasses
import enum
import os
import signal
from dataclasses import dataclass, field
from typing import Any, Optional

from immutables import Map as ImmutableDict

from shellous.redirect import Redirect
from shellous.runner import Runner, run, run_iter
from shellous.util import coerce_env

# Sentinel used in "mergable" keyword arguments to indicate that a value
# was not set by the caller. This is an enum class to make UNSET more visible
# in generated documentation.
_UnsetType = enum.Enum("UNSET", "UNSET")
_UNSET = _UnsetType.UNSET


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    "Concrete class for per-command options."

    context: "Context" = field(compare=False, repr=False)
    "Root context object."

    env: Optional[ImmutableDict] = field(default=None, repr=False)
    "Additional environment variables for command."

    inherit_env: bool = True
    "True if subprocess should inherit the current environment variables."

    input: Any = b""
    "Input object to bind to stdin."

    input_close: bool = False
    "True if input object should be closed after subprocess launch."

    output: Any = Redirect.CAPTURE
    "Output object to bind to stdout."

    output_append: bool = False
    "True if output object should be opened in append mode."

    output_close: bool = False
    "True if output object should be closed after subprocess launch."

    error: Any = Redirect.DEVNULL
    "Error object to bind to stderr."

    error_append: bool = False
    "True if error object should be opened in append mode."

    error_close: bool = False
    "True if error object should be closed after subprocess launch."

    encoding: Optional[str] = "utf-8"
    "Specifies encoding of input/ouput. None means binary."

    return_result: bool = False
    "True if we should return `Result` object instead of the output text/bytes."

    exit_codes: Optional[set] = None
    "Set of exit codes that do not raise a `ResultError`. None means {0}."

    cancel_timeout: float = 3.0
    "Timeout in seconds that we wait for a cancelled process to terminate."

    cancel_signal: Optional[Any] = signal.SIGTERM
    "The signal sent to terminate a cancelled process."

    alt_name: Optional[str] = None
    "Alternate name for the command to use when logging."

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
class Context:
    """Concrete class for an immutable execution context."""

    options: Options = None

    def __post_init__(self):
        if self.options is None:
            # Initialize `context` in Options to `self`.
            object.__setattr__(self, "options", Options(self))

    def stdin(self, input_, *, close=False):
        "Return new context with updated `input` settings."
        new_options = self.options.set_stdin(input_, close)
        return Context(new_options)

    def stdout(self, output, *, append=False, close=False):
        "Return new context with updated `output` settings."
        new_options = self.options.set_stdout(output, append, close)
        return Context(new_options)

    def stderr(self, error, *, append=False, close=False):
        "Return new context with updated `error` settings."
        new_options = self.options.set_stderr(error, append, close)
        return Context(new_options)

    def env(self, **kwds):
        """Return new context with augmented environment."""
        new_options = self.options.set_env(kwds)
        return Context(new_options)

    def set(  # pylint: disable=unused-argument
        self,
        *,
        inherit_env=_UNSET,
        encoding=_UNSET,
        return_result=_UNSET,
        exit_codes=_UNSET,
        cancel_timeout=_UNSET,
        cancel_signal=_UNSET,
        alt_name=_UNSET,
    ):
        "Return new context with custom options set."
        kwargs = locals()
        del kwargs["self"]
        return Context(self.options.set(kwargs))

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

    def _pipe_apply(self, _pipe, args):
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
            if isinstance(arg, (str, bytes, bytearray, os.PathLike)):
                result.append(arg)
            elif isinstance(arg, (list, tuple)):
                result.extend(self._coerce(arg))
            elif isinstance(arg, (Command, dict, set)):
                raise NotImplementedError("syntax is reserved")
            elif arg is Ellipsis:
                raise NotImplementedError("syntax is reserved")
            elif arg is None:
                raise TypeError("None in argument list")
            else:
                result.append(str(arg))
        return tuple(result)


def context() -> Context:
    "Construct a new execution context."
    return Context()


def pipeline(*commands):
    "Construct a new Pipeline object."
    # pylint: disable=import-outside-toplevel,cyclic-import
    from shellous.pipeline import Pipeline

    return Pipeline(commands)


@dataclass(frozen=True)
class Command:
    """A Command instance is lightweight and immutable object that specifies the
    arguments and options used to run a program. Commands do not do anything
    until they are awaited.

    Commands are always created by a Context.

    ```
    # Create a context.
    sh = shellous.context()

    # Create a new command from the context.
    echo = sh("echo", "hello, world")

    # Run the command.
    result = await echo
    ```
    """

    args: Any
    options: Options

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

    @property
    def multiple_capture(self) -> bool:
        """Return true if the stdin is set to CAPTURE or more than one of
        stdout, stderr is set to CAPTURE.
        """
        return self.options.input == Redirect.CAPTURE or (
            self.options.output == Redirect.CAPTURE
            and self.options.error == Redirect.CAPTURE
        )

    def stdin(self, input_, *, close=False):
        "Pass `input` to command's standard input."
        new_options = self.options.set_stdin(input_, close)
        return Command(self.args, new_options)

    def stdout(self, output, *, append=False, close=False):
        "Redirect standard output to `output`."
        _check_args(output, append)
        new_options = self.options.set_stdout(output, append, close)
        return Command(self.args, new_options)

    def stderr(self, error, *, append=False, close=False):
        "Redirect standard error to `error`."
        _check_args(error, append)
        new_options = self.options.set_stderr(error, append, close)
        return Command(self.args, new_options)

    def env(self, **kwds):
        """Return new command with augmented environment."""
        new_options = self.options.set_env(kwds)
        return Command(self.args, new_options)

    def set(  # pylint: disable=unused-argument
        self,
        *,
        inherit_env: bool = _UNSET,
        encoding: Optional[str] = _UNSET,
        return_result: bool = _UNSET,
        exit_codes: Optional[set] = _UNSET,
        cancel_timeout: float = _UNSET,
        cancel_signal: Any = _UNSET,
        alt_name: Optional[str] = _UNSET,
    ):
        """Return new command with custom options set.

        - Set `inherit_env` to False to prevent the command from inheriting
        the parent environment.
        - Set `encoding` to a string encoding like "utf-8", or "utf-8 replace".
        If `encoding` is None, use raw bytes.
        - Set `return_result` to True to return a `Result` object instead of
        the standard output. The `Result` object includes the exit code.
        - Set `exit_codes` to the set of allowed exit codes that will not raise
        a `ResultError`. None means {0}.
        - Set `cancel_timeout` to the timeout in seconds to wait for a process
        to terminate after sending it the cancel signal.
        - Set `cancel_signal` to the signal used to stop a process when it is
        cancelled.
        - Set `alt_name` to an alternative name of the command to be displayed
        in logs. Used to resolve ambiguity when the actual command name is a
        scripting language.
        """
        kwargs = locals()
        del kwargs["self"]
        return Command(self.args, self.options.set(kwargs))

    def task(self, *, _streams_future=None):
        "Wrap the command in a new asyncio task."
        return asyncio.create_task(
            run(self, _streams_future=_streams_future),
            name=f"{self.name}-{id(self)}",
        )

    def runner(self):
        """Return a `Runner` to run the process incrementally.

        ```
        runner = cmd.runner()
        async with runner as (stdin, stdout, stderr):
            # do something with stdin, stdout, stderr...
            # close stdin to signal we're done...
        result = runner.result()
        ```
        """
        return Runner(self)

    def __await__(self):
        "Run process and return the standard output."
        return run(self).__await__()

    def __aiter__(self):
        "Return an asynchronous iterator over the standard output."
        return run_iter(self)

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
        return pipeline(self) | rhs

    def __ror__(self, lhs):
        "Bitwise or operator is used to build pipelines."
        return lhs | pipeline(self)

    def __rshift__(self, rhs):
        "Right shift operator is used to build pipelines."
        return pipeline(self) >> rhs


_SUPPORTS_APPEND = (str, bytes, os.PathLike)


def _check_args(out, append):
    if append and not isinstance(out, _SUPPORTS_APPEND):
        raise TypeError(f"{type(out)} does not support append")
