"Implements support for Pipelines."

import dataclasses
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Coroutine, Generic, Optional, TypeVar, Union, overload

import shellous
from shellous.redirect import (
    STDIN_TYPES,
    STDIN_TYPES_T,
    STDOUT_TYPES,
    STDOUT_TYPES_T,
    Redirect,
)
from shellous.runner import PipeRunner
from shellous.util import context_aenter, context_aexit

# Return type for a Command, CmdContext can be either `str` or `Result`.
_RT = TypeVar("_RT", str, "shellous.Result")
_T = TypeVar("_T", str, "shellous.Result")


@dataclass(frozen=True)
class Pipeline(Generic[_RT]):
    "A Pipeline is a sequence of commands."

    commands: tuple[shellous.Command[_RT], ...] = ()

    @staticmethod
    def create(*commands: shellous.Command[_T]) -> "Pipeline[_T]":
        "Create a new Pipeline."
        return Pipeline(commands)

    def __post_init__(self):
        "Validate the pipeline."
        if len(self.commands) == 0:
            raise ValueError("Pipeline must include at least one command")

    @property
    def name(self) -> str:
        "Return the name of the pipeline."
        return "|".join(cmd.name for cmd in self.commands)

    @property
    def options(self) -> shellous.Options:
        "Return last command's options."
        return self.commands[-1].options

    def stdin(self, input_: Any, *, close: bool = False) -> "Pipeline[_RT]":
        "Set stdin on the first command of the pipeline."
        new_first = self.commands[0].stdin(input_, close=close)
        new_commands = (new_first,) + self.commands[1:]
        return dataclasses.replace(self, commands=new_commands)

    def stdout(
        self,
        output: Any,
        *,
        append: bool = False,
        close: bool = False,
    ) -> "Pipeline[_RT]":
        "Set stdout on the last command of the pipeline."
        new_last = self.commands[-1].stdout(output, append=append, close=close)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def stderr(
        self,
        error: Any,
        *,
        append: bool = False,
        close: bool = False,
    ) -> "Pipeline[_RT]":
        "Set stderr on the last command of the pipeline."
        new_last = self.commands[-1].stderr(error, append=append, close=close)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def _set(self, **kwds: Any):
        "Set options on last command of the pipeline."
        new_last = self.commands[-1].set(**kwds)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def coro(self) -> Coroutine[Any, Any, _RT]:
        "Return coroutine object for pipeline."
        return PipeRunner.run_pipeline(self)

    def _run_(self) -> "PipeRunner":
        """Return a `Runner` to help run the pipeline incrementally.

        ```
        async with pipe.run() as run:
            # do something with run.stdin, run.stdout, run.stderr...
            # close stdin to signal we're done...
        result = run.result()
        ```
        """
        return PipeRunner(self, capturing=True)

    def _add(self, item: Union["shellous.Command[Any]", "Pipeline[Any]"]):
        if isinstance(item, shellous.Command):
            return dataclasses.replace(self, commands=(*self.commands, item))
        return dataclasses.replace(
            self,
            commands=self.commands + item.commands,
        )

    def __len__(self):
        "Return number of commands in pipe."
        return len(self.commands)

    def __getitem__(self, key: int) -> shellous.Command[Any]:
        "Return specified command by index."
        return self.commands[key]

    def __call__(self, *args: Any):
        if args:
            raise TypeError("Calling pipeline with 1 or more arguments.")
        return self

    @overload
    def __or__(
        self, rhs: "Union[shellous.Command[shellous.Result], Pipeline[shellous.Result]]"
    ) -> "Pipeline[shellous.Result]":
        ...

    @overload
    def __or__(
        self, rhs: "Union[shellous.Command[str], Pipeline[str]]"
    ) -> "Pipeline[str]":
        ...

    @overload
    def __or__(self, rhs: STDOUT_TYPES_T) -> "Pipeline[_RT]":
        ...

    def __or__(self, rhs: Any) -> "Pipeline[Any]":
        if isinstance(rhs, (shellous.Command, Pipeline)):
            return self._add(rhs)
        if isinstance(rhs, STDOUT_TYPES):
            return self.stdout(rhs)
        if isinstance(rhs, (str, bytes)):
            raise TypeError(
                f"{type(rhs)!r} unsupported for | output (Use 'pathlib.Path')"
            )
        return NotImplemented

    def __ror__(self, lhs: STDIN_TYPES_T) -> "Pipeline[_RT]":
        if isinstance(lhs, STDIN_TYPES):
            return self.stdin(lhs)
        return NotImplemented

    def __rshift__(self, rhs: STDOUT_TYPES_T) -> "Pipeline[_RT]":
        if isinstance(rhs, STDOUT_TYPES):
            return self.stdout(rhs, append=True)
        if isinstance(rhs, (str, bytes)):
            raise TypeError(
                f"{type(rhs)!r} unsupported for >> output (Use 'pathlib.Path')"
            )
        return NotImplemented

    @property
    def writable(self):
        "Set writable=True option on last command of pipeline."
        return self._set(_writable=True)

    @property
    def result(self) -> "Pipeline[shellous.Result]":
        "Set `_return_result` and `exit_codes`."
        return self._set(_return_result=True, exit_codes=range(-255, 256))

    def __await__(self):
        return self.coro().__await__()

    async def __aenter__(self):
        "Enter the async context manager."
        return await context_aenter(id(self), self._run_())

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ):
        "Exit the async context manager."
        return await context_aexit(id(self), exc_type, exc_value, exc_tb)

    def __aiter__(self):
        "Return async iterator to iterate over output lines."
        return self._readlines()

    async def _readlines(self):
        "Async generator to iterate over lines."
        cmd = self
        if cmd.options.output == Redirect.DEFAULT:
            cmd = cmd.stdout(Redirect.CAPTURE)

        async with cmd._run_() as run:
            if run.stdout is not None and run.stderr is not None:
                raise RuntimeError("multiple capture not supported in iterator")
            async for line in run:
                yield line
