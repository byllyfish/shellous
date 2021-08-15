"Implements support for Pipelines."

import asyncio
import dataclasses
import os
from dataclasses import dataclass
from typing import Any

from shellous.command import Command
from shellous.runner import PipeRunner, run_pipe, run_pipe_iter
from shellous.util import Redirect


@dataclass(frozen=True)
class Pipeline:
    "A Pipeline is a sequence of commands."

    commands: Any = ()

    def __post_init__(self):
        "Validate the pipeline."
        if len(self.commands) == 0:
            raise ValueError("Pipeline must include at least one command")

    @property
    def name(self):
        "Return the name of the pipeline."
        return "|".join(cmd.name for cmd in self.commands)

    @property
    def captured(self):
        "Return true if stdin or stderr streams are captured."
        return (
            self.commands[0].options.input == Redirect.CAPTURE
            or self.commands[-1].options.error == Redirect.CAPTURE
        )

    @property
    def options(self):
        "Return last command's options."
        return self.commands[-1].options

    def stdin(self, input_):
        "Set stdin on the first command of the pipeline."
        if not self.commands:
            raise ValueError("invalid pipeline")
        new_first = self.commands[0].stdin(input_)
        new_commands = (new_first,) + self.commands[1:]
        return dataclasses.replace(self, commands=new_commands)

    def stdout(self, output, *, append=False):
        "Set stdout on the last command of the pipeline."
        if not self.commands:
            raise ValueError("invalid pipeline")
        new_last = self.commands[-1].stdout(output, append=append)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def task(self):
        "Wrap the command in a new asyncio task."
        return asyncio.create_task(
            run_pipe(self),
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
        return PipeRunner(self, capturing=True)

    def _add(self, item):
        if isinstance(item, Command):
            return dataclasses.replace(self, commands=self.commands + (item,))
        if isinstance(item, Pipeline):
            return dataclasses.replace(
                self,
                commands=self.commands + item.commands,
            )
        raise TypeError("unsupported type")

    def __call__(self, *args):
        if len(args) > 0:
            raise TypeError("Calling pipeline with 1 or more arguments.")
        return self

    def __or__(self, rhs):
        if isinstance(rhs, (Command, Pipeline)):
            return self._add(rhs)
        if isinstance(rhs, (str, bytes, os.PathLike)):
            return self.stdout(rhs)
        return NotImplemented

    def __ror__(self, lhs):
        if isinstance(lhs, (str, bytes, os.PathLike)):
            return self.stdin(lhs)
        return NotImplemented

    def __rshift__(self, rhs):
        if isinstance(rhs, (str, bytes, os.PathLike)):
            return self.stdout(rhs, append=True)
        return NotImplemented

    def __await__(self):
        return run_pipe(self).__await__()

    def __aiter__(self):
        "Return an asynchronous iterator over the standard output."
        return run_pipe_iter(self)
