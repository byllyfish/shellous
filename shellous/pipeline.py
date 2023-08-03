"Implements support for Pipelines."

import dataclasses
from dataclasses import dataclass
from types import TracebackType
from typing import (
    Any,
    AsyncIterator,
    Coroutine,
    Generator,
    Generic,
    Optional,
    TypeVar,
    Union,
    cast,
    overload,
)

import shellous
from shellous.redirect import (
    STDIN_TYPES,
    STDOUT_TYPES,
    StdinType,
    StdoutType,
    aiter_preflight,
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

    def __post_init__(self) -> None:
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
        return cast(Coroutine[Any, Any, _RT], PipeRunner.run_pipeline(self))

    def _add(self, item: Union["shellous.Command[Any]", "Pipeline[Any]"]):
        if isinstance(item, shellous.Command):
            return dataclasses.replace(self, commands=(*self.commands, item))
        return dataclasses.replace(
            self,
            commands=self.commands + item.commands,
        )

    def __len__(self) -> int:
        "Return number of commands in pipe."
        return len(self.commands)

    def __getitem__(self, key: int) -> shellous.Command[Any]:
        "Return specified command by index."
        return self.commands[key]

    def __call__(self, *args: Any) -> "Pipeline[_RT]":
        if args:
            raise TypeError("Calling pipeline with 1 or more arguments.")
        return self

    @overload
    def __or__(
        self, rhs: "Union[shellous.Command[shellous.Result], Pipeline[shellous.Result]]"
    ) -> "Pipeline[shellous.Result]":
        ...  # pragma: no cover

    @overload
    def __or__(
        self, rhs: "Union[shellous.Command[str], Pipeline[str]]"
    ) -> "Pipeline[str]":
        ...  # pragma: no cover

    @overload
    def __or__(self, rhs: StdoutType) -> "Pipeline[_RT]":
        ...  # pragma: no cover

    def __or__(self, rhs: Any) -> "Pipeline[Any]":
        if isinstance(rhs, (shellous.Command, Pipeline)):
            return self._add(rhs)  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(rhs, STDOUT_TYPES):
            return self.stdout(rhs)
        if isinstance(rhs, (str, bytes)):
            raise TypeError(
                f"{type(rhs)!r} unsupported for | output (Use 'pathlib.Path')"
            )
        return NotImplemented

    def __ror__(self, lhs: StdinType) -> "Pipeline[_RT]":
        if isinstance(lhs, STDIN_TYPES):
            return self.stdin(lhs)
        return NotImplemented

    def __rshift__(self, rhs: StdoutType) -> "Pipeline[_RT]":
        if isinstance(rhs, STDOUT_TYPES):
            return self.stdout(rhs, append=True)
        if isinstance(rhs, (str, bytes)):
            raise TypeError(
                f"{type(rhs)!r} unsupported for >> output (Use 'pathlib.Path')"
            )
        return NotImplemented

    @property
    def writable(self) -> "Pipeline[_RT]":
        "Set writable=True option on last command of pipeline."
        return self._set(_writable=True)

    @property
    def result(self) -> "Pipeline[shellous.Result]":
        "Set `_return_result` and `exit_codes`."
        return cast(
            Pipeline[shellous.Result],
            self._set(_return_result=True, exit_codes=range(-255, 256)),
        )

    def __await__(self) -> "Generator[Any, None, _RT]":
        return self.coro().__await__()  # FP pylint: disable=no-member

    async def __aenter__(self) -> PipeRunner:
        "Enter the async context manager."
        return await context_aenter(self, PipeRunner(self, capturing=True))

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> Optional[bool]:
        "Exit the async context manager."
        return await context_aexit(self, exc_type, exc_value, exc_tb)

    def __aiter__(self) -> AsyncIterator[str]:
        "Return async iterator to iterate over output lines."
        return aiter_preflight(self)._readlines()

    async def _readlines(self):
        "Async generator to iterate over lines."
        async with PipeRunner(self, capturing=True) as run:
            async for line in run:
                yield line
