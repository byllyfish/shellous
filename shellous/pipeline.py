"Implements support for Pipelines."

import dataclasses
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Optional, Union

import shellous
from shellous.redirect import STDIN_TYPES, STDOUT_APPEND_TYPES, STDOUT_TYPES
from shellous.runner import PipeRunner
from shellous.util import context_aenter, context_aexit


@dataclass(frozen=True)
class Pipeline:
    "A Pipeline is a sequence of commands."

    commands: tuple[shellous.Command, ...] = ()

    @staticmethod
    def create(*commands: shellous.Command) -> "Pipeline":
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

    def stdin(self, input_: Any, *, close: bool = False) -> "Pipeline":
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
    ) -> "Pipeline":
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
    ) -> "Pipeline":
        "Set stderr on the last command of the pipeline."
        new_last = self.commands[-1].stderr(error, append=append, close=close)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def _set(self, **kwds: Any):
        "Set options on last command of the pipeline."
        new_last = self.commands[-1].set(**kwds)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def coro(self):
        "Return coroutine object for pipeline."
        return PipeRunner.run_pipeline(self)

    def run(self) -> "PipeRunner":
        """Return a `Runner` to help run the pipeline incrementally.

        ```
        async with pipe.run() as run:
            # do something with run.stdin, run.stdout, run.stderr...
            # close stdin to signal we're done...
        result = run.result()
        ```
        """
        return PipeRunner(self, capturing=True)

    def _add(self, item: Union["shellous.Command", "Pipeline"]):
        if isinstance(item, shellous.Command):
            return dataclasses.replace(self, commands=self.commands + (item,))
        return dataclasses.replace(
            self,
            commands=self.commands + item.commands,
        )

    def __len__(self):
        "Return number of commands in pipe."
        return len(self.commands)

    def __getitem__(self, key: int):
        "Return specified command by index."
        return self.commands[key]

    def __call__(self, *args: Any):
        if args:
            raise TypeError("Calling pipeline with 1 or more arguments.")
        return self

    def __or__(self, rhs: Any):
        if isinstance(rhs, (shellous.Command, Pipeline)):
            return self._add(rhs)
        if isinstance(rhs, STDOUT_TYPES):
            return self.stdout(rhs)
        if isinstance(rhs, (str, bytes)):
            raise TypeError(
                f"{type(rhs)!r} unsupported for | output (Use 'pathlib.Path')"
            )
        return NotImplemented

    def __ror__(self, lhs: Any):
        if isinstance(lhs, STDIN_TYPES):
            return self.stdin(lhs)
        return NotImplemented

    def __rshift__(self, rhs: Any):
        if isinstance(rhs, STDOUT_APPEND_TYPES):
            return self.stdout(rhs, append=True)
        if isinstance(rhs, (str, bytes)):
            raise TypeError(
                f"{type(rhs)!r} unsupported for >> output (Use 'pathlib.Path')"
            )
        return NotImplemented

    @property
    def writable(self):
        "Set writable=True option on last command of pipeline."
        return self._set(writable=True)

    @property
    def result(self):
        "Set `return_result` and `exit_codes`."
        return self._set(return_result=True, exit_codes=range(-255, 256))

    def __await__(self):
        return self.coro().__await__()

    async def __aenter__(self):
        "Enter the async context manager."
        return await context_aenter(id(self), self.run())

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
        async with self.run() as run:
            async for line in run:
                yield line
