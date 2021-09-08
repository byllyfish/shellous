Commands
========

Commands are immutable objects that represent a program invocation: program, arguments, environment
variables, redirection operators and other settings.  Commands are reusable templates for running
a program. They don't run the program until you `await` them.

When you use a method to modify a `Command`, you are creating a new `Command` object. The original
command object is not modified.

In this example, we create an echo command `cmd1`, then use it to create a second echo command `cmd2` with
the environment variable 'DEBUG' set to '1'.

```python
cmd1 = sh("echo")
cmd2 = cmd1.env(DEBUG=1)
```

CmdContext
----------

Commands are created by a `CmdContext`. The `CmdContext` object manages how arguments are converted to strings
and how arguments are applied to existing commands.

You can also specify defaults in the context using the same methods as `Command`. `CmdContext` objects are
immutable.
