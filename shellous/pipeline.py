"Implements support for Pipelines."

import dataclasses
import os
from dataclasses import dataclass
from typing import Any

from shellous.command import Command
from shellous.runner import run_pipe, run_pipe_iter


@dataclass(frozen=True)
class Pipeline:
    "A Pipeline is a sequence of commands."

    commands: Any = ()

    def __post_init__(self):
        "Validate the pipeline."
        if len(self.commands) == 0:
            raise ValueError("Pipeline must include at least one command")

    @property
    def options(self):
        "Return last command's options."
        return self.commands[-1].options

    def stdin(self, input):
        "Set stdin on the first command of the pipeline."
        if not self.commands:
            raise ValueError("invalid pipeline")
        new_first = self.commands[0].stdin(input)
        new_commands = (new_first,) + self.commands[1:]
        return dataclasses.replace(self, commands=new_commands)

    def stdout(self, output, *, append=False):
        "Set stdout on the last command of the pipeline."
        if not self.commands:
            raise ValueError("invalid pipeline")
        new_last = self.commands[-1].stdout(output, append=append)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def _add(self, item):
        if isinstance(item, Command):
            return dataclasses.replace(self, commands=self.commands + (item,))
        if isinstance(item, Pipeline):
            return dataclasses.replace(
                self,
                commands=self.commands + item.commands,
            )
        raise TypeError("unsupported type")

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
