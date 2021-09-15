"Implements support for Pipelines."

import dataclasses
from dataclasses import dataclass

import shellous
from shellous.redirect import STDIN_TYPES, STDOUT_APPEND_TYPES, STDOUT_TYPES
from shellous.runner import PipeRunner


@dataclass(frozen=True)
class Pipeline:
    "A Pipeline is a sequence of commands."

    commands: tuple[shellous.Command, ...] = ()

    @staticmethod
    def create(*commands) -> "Pipeline":
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

    def stdin(self, input_, *, close=False) -> "Pipeline":
        "Set stdin on the first command of the pipeline."
        new_first = self.commands[0].stdin(input_, close=close)
        new_commands = (new_first,) + self.commands[1:]
        return dataclasses.replace(self, commands=new_commands)

    def stdout(self, output, *, append=False, close=False) -> "Pipeline":
        "Set stdout on the last command of the pipeline."
        new_last = self.commands[-1].stdout(output, append=append, close=close)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def stderr(self, error, *, append=False, close=False) -> "Pipeline":
        "Set stderr on the last command of the pipeline."
        new_last = self.commands[-1].stderr(error, append=append, close=close)
        new_commands = self.commands[0:-1] + (new_last,)
        return dataclasses.replace(self, commands=new_commands)

    def _set_write_mode(self):
        "Set write_mode=True on last command of the pipeline."
        new_last = self.commands[-1].set(write_mode=True)
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

    def _add(self, item):
        if isinstance(item, shellous.Command):
            return dataclasses.replace(self, commands=self.commands + (item,))
        if isinstance(item, Pipeline):
            return dataclasses.replace(
                self,
                commands=self.commands + item.commands,
            )
        raise TypeError("unsupported type")

    def __len__(self):
        "Return number of commands in pipe."
        return len(self.commands)

    def __getitem__(self, key):
        "Return specified command by index."
        return self.commands[key]

    def __call__(self, *args):
        if len(args) == 0:
            return self
        # Use context from first command.
        context = self.commands[0].options.context
        return context._pipe_apply(self, args)

    def __or__(self, rhs):
        if isinstance(rhs, (shellous.Command, Pipeline)):
            return self._add(rhs)
        if isinstance(rhs, STDOUT_TYPES):
            return self.stdout(rhs)
        return NotImplemented

    def __ror__(self, lhs):
        if isinstance(lhs, STDIN_TYPES):
            return self.stdin(lhs)
        return NotImplemented

    def __rshift__(self, rhs):
        if isinstance(rhs, STDOUT_APPEND_TYPES):
            return self.stdout(rhs, append=True)
        return NotImplemented

    def __invert__(self):
        "Set write_mode=True option on last command of pipeline."
        return self._set_write_mode()

    def __await__(self):
        return self.coro().__await__()
