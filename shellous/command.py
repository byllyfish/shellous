"Implements the Context, Options, and Command classes."

import asyncio
import dataclasses
import os
from dataclasses import dataclass
from typing import Any, Optional

from shellous.runner import Runner, run, run_iter
from shellous.util import Redirect

# Sentinel used in "mergable" keyword arguments to indicate that a value
# was not set by the caller.
_UNSET = object()


@dataclass(frozen=True)
class Context:
    """Concrete class for an immutable execution context."""

    env: Optional[dict[str, str]]
    encoding: Optional[str]

    def __call__(self, *command):
        return Command(self, self._coerce(command))

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


def context(*, env=None, encoding="utf-8") -> Context:
    "Construct a new execution context."
    return Context(env, encoding)


def pipeline(*commands):
    "Construct a new Pipeline object."
    from shellous.pipeline import Pipeline

    return Pipeline(commands)


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    "Concrete class for per-command options."

    env: Optional[dict[str, str]] = None
    input: Any = b""
    input_close: bool = False
    output: Any = Redirect.CAPTURE
    output_append: bool = False
    output_close: bool = False
    error: Any = Redirect.DEVNULL
    error_append: bool = False
    error_close: bool = False
    return_result: bool = False
    allowed_exit_codes: Optional[set] = None

    def merge_env(self, context_env):
        "Return our `env` merged with the global environment."
        if not self.env:
            return context_env
        if context_env is not None:
            return context_env | self.env
        return os.environ | self.env


_DEFAULT_OPTIONS = Options()


@dataclass(frozen=True)
class Command:
    """Concrete class for a command.

    A Command instance is lightweight and immutable.
    """

    context: Context
    args: Any
    options: Options = _DEFAULT_OPTIONS

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
        if input_ is None:
            input_ = Redirect.DEVNULL

        new_options = dataclasses.replace(
            self.options,
            input=input_,
            input_close=close,
        )
        return Command(self.context, self.args, new_options)

    def stdout(self, output, *, append=False, close=False):
        "Redirect standard output to `output`."
        if output is None:
            output = Redirect.DEVNULL

        new_options = dataclasses.replace(
            self.options,
            output=output,
            output_append=append,
            output_close=close,
        )
        return Command(self.context, self.args, new_options)

    def stderr(self, error, *, append=False, close=False):
        "Redirect standard error to `error`."
        if error is None:
            error = Redirect.DEVNULL

        new_options = dataclasses.replace(
            self.options,
            error=error,
            error_append=append,
            error_close=close,
        )
        return Command(self.context, self.args, new_options)

    def env(self, **kwds):
        """Return new command with augmented environment."""
        options = self.options
        current = options.env or {}
        updates = {k: str(v) for k, v in kwds.items()}
        new_options = dataclasses.replace(options, env=current | updates)
        return Command(self.context, self.args, new_options)

    def set(self, *, return_result=_UNSET, allowed_exit_codes=_UNSET):
        "Return new command with custom options set."
        kwds = dict(
            return_result=return_result,
            allowed_exit_codes=allowed_exit_codes,
        )
        kwds = {key: value for key, value in kwds.items() if value is not _UNSET}
        new_options = dataclasses.replace(self.options, **kwds)
        return Command(self.context, self.args, new_options)

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
            self.context,
            self.args + self.context._coerce(args),
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
