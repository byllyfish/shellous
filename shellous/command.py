"Implements the Context, Options, and Command classes."

import asyncio
import dataclasses
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from shellous.runner import Runner, run, run_iter
from shellous.util import Redirect

# Sentinel used in "mergable" keyword arguments to indicate that a value
# was not set by the caller.
_UNSET = object()


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    "Concrete class for per-command options."

    context: "Context" = field(compare=False, default=None, repr=False)
    env: Optional[dict[str, str]] = None
    inherit_env: bool = True
    input: Any = b""
    input_close: bool = False
    output: Any = Redirect.CAPTURE
    output_append: bool = False
    output_close: bool = False
    error: Any = Redirect.DEVNULL
    error_append: bool = False
    error_close: bool = False
    encoding: Optional[str] = "utf-8"
    return_result: bool = False
    allowed_exit_codes: Optional[set] = None

    def merge_env(self):
        "Return our `env` merged with the global environment."
        if self.inherit_env:
            if not self.env:
                return None
            return os.environ | self.env

        if self.env:
            return self.env
        return {}

    def set_stdin(self, input_, close):
        "Return new options with `input` configured."
        if input_ is None:
            input_ = Redirect.DEVNULL

        return dataclasses.replace(
            self,
            input=input_,
            input_close=close,
        )

    def set_stdout(self, output, append, close):
        "Return new options with `output` configured."
        if output is None:
            output = Redirect.DEVNULL

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
        current = self.env or {}
        updates = {str(k): str(v) for k, v in env.items()}
        new_env = current | updates
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

    def env(self, **kwds):
        """Return new context with augmented environment."""
        new_options = self.options.set_env(kwds)
        return Context(new_options)

    def set(
        self,
        *,
        inherit_env=_UNSET,
        encoding=_UNSET,
        return_result=_UNSET,
        allowed_exit_codes=_UNSET,
    ):
        "Return new context with custom options set."
        kwargs = locals()
        del kwargs["self"]
        return Context(self.options.set(kwargs))

    def __call__(self, *command):
        return Command(self._coerce(command), self.options)

    def _coerce(self, command):
        """Flatten lists and coerce arguments to string.

        Behavior may be customizable in the future.
        """
        result = []
        for arg in command:
            if isinstance(arg, (str, bytes, os.PathLike)):
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
    from shellous.pipeline import Pipeline

    return Pipeline(commands)


@dataclass(frozen=True)
class Command:
    """Concrete class for a command.

    A Command instance is lightweight and immutable.
    """

    args: Any
    options: Options = None

    def __post_init__(self):
        "Validate the command."
        if len(self.args) == 0:
            raise ValueError("Command must include program name")

    @property
    def name(self) -> str:
        "Returns the name of the program being run."
        return self.args[0]

    def stdin(self, input_, *, close=False):
        "Pass `input` to command's standard input."
        new_options = self.options.set_stdin(input_, close)
        return Command(self.args, new_options)

    def stdout(self, output, *, append=False, close=False):
        "Redirect standard output to `output`."
        new_options = self.options.set_stdout(output, append, close)
        return Command(self.args, new_options)

    def stderr(self, error, *, append=False, close=False):
        "Redirect standard error to `error`."
        new_options = self.options.set_stderr(error, append, close)
        return Command(self.args, new_options)

    def env(self, **kwds):
        """Return new command with augmented environment."""
        new_options = self.options.set_env(kwds)
        return Command(self.args, new_options)

    def set(
        self,
        *,
        inherit_env=_UNSET,
        encoding=_UNSET,
        return_result=_UNSET,
        allowed_exit_codes=_UNSET,
    ):
        "Return new command with custom options set."
        kwargs = locals()
        del kwargs["self"]
        return Command(self.args, self.options.set(kwargs))

    def task(self):
        "Wrap the command in a new asyncio task."
        return asyncio.create_task(
            run(self),
            name=f"{self.name}-{id(self)}",
        )

    def runner(self):
        """Return a `Runner` to help run the process incrementally.

        ```
        runner = cmd.runner()
        async with runner as (stdin, stdout, stderr):
            # do something with stdin, stdout, stderr
        result = await runner.wait()
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
        return Command(
            self.args + self.options.context._coerce(args),
            self.options,
        )

    def __str__(self):
        "Return string representation."

        def _quote(value):
            value = str(value)
            if " " in value:
                return repr(value)
            return value

        cmd = " ".join(_quote(arg) for arg in self.args)
        if self.options.env:
            env = " ".join(f"{k}={_quote(v)}" for k, v in self.options.env.items())
            return f"{env} {cmd}"
        return cmd

    def __or__(self, rhs):
        "Bitwise or operator is used to build pipelines."
        return pipeline(self) | rhs

    def __ror__(self, lhs):
        "Bitwise or operator is used to build pipelines."
        return lhs | pipeline(self)

    def __rshift__(self, rhs):
        "Right shift operator is used to build pipelines."
        return pipeline(self) >> rhs
