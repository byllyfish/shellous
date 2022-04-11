Async Processes and Pipelines
=============================

[![docs](https://img.shields.io/badge/-documentation-informational)](https://byllyfish.github.io/shellous/shellous.html) [![PyPI](https://img.shields.io/pypi/v/shellous)](https://pypi.org/project/shellous/) [![CI](https://github.com/byllyfish/shellous/actions/workflows/ci.yml/badge.svg)](https://github.com/byllyfish/shellous/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/byllyfish/shellous/branch/main/graph/badge.svg?token=W44NZE89AW)](https://codecov.io/gh/byllyfish/shellous) [![Downloads](https://pepy.tech/badge/shellous)](https://pepy.tech/project/shellous)

shellous provides a concise API for running subprocesses using asyncio. It is 
similar to and inspired by [sh](https://pypi.org/project/sh/).

```python
import asyncio
from shellous import sh

async def main():
    result = await sh("echo", "hello")
    print(result)

asyncio.run(main())
```

Benefits
--------

- Run programs asychronously in a single line.
- Easily capture output or redirect stdin, stdout and stderr to files, memory buffers or loggers.
- Easily construct [pipelines](https://en.wikipedia.org/wiki/Pipeline_(Unix)) and use [process substitution](https://en.wikipedia.org/wiki/Process_substitution).
- Easily set timeouts and reliably cancel running processes.
- Run a program with a pseudo-terminal (pty).
- Runs on Linux, MacOS, FreeBSD and Windows.
- Monitor processes being started and stopped with `audit_callback` API.

Requirements
------------

- Requires Python 3.9 or later.
- Requires an asyncio event loop.
- Process substitution requires a Unix system with /dev/fd support.
- Pseudo-terminals require a Unix system.

Basic Usage
-----------

Start the asyncio REPL by typing `python3 -m asyncio`, and import **sh** from the **shellous** module:

```pycon
>>> from shellous import sh
```

Here's a command that runs `echo "hello, world"`.

```pycon
>>> await sh("echo", "hello, world")
'hello, world\n'
```

The first argument is the program name. It is followed by zero or more separate arguments.

A command does not run until you `await` it. Here, we create our own echo command with "-n"
to omit the newline. Note, `echo("abc")` is the same as `echo -n "abc"`.

```pycon
>>> echo = sh("echo", "-n")
>>> await echo("abc")
'abc'
```

[More on commands...](docs/commands.md) <!-- __REL_LINK__ -->

Results and Exit Codes
----------------------

If a command exits with a non-zero exit code, it raises a `ResultError` exception that contains
the `Result` object. The `Result` object contains the exit code for the command among other details.

```pycon
>>> await sh("cat", "does_not_exist")
Traceback (most recent call last):
  ...
shellous.result.ResultError: Result(output_bytes=b'', exit_code=1, cancelled=False, encoding='utf-8', extra=None)
```

To always return a `Result` object (and not raise an error for a non-zero exit status), add the `.result` modifier.

```pycon
>>> await echo("abc").result
Result(output_bytes=b'abc', exit_code=0, cancelled=False, encoding='utf-8', extra=None)
```

[More on results...](docs/results.md) <!-- __REL_LINK__ -->

Redirecting Standard Input
--------------------------

You can change the standard input of a command by using the `|` operator.

```pycon
>>> cmd = "abc" | sh("wc", "-c")
>>> await cmd
'       3\n'
```

To redirect stdin using a file's contents, use a `Path` object from `pathlib`.

```pycon
>>> from pathlib import Path
>>> cmd = Path("LICENSE") | sh("wc", "-l")
>>> await cmd
'     201\n'
```

[More on redirection...](docs/redirection.md) <!-- __REL_LINK__ -->

Redirecting Standard Output
---------------------------

To redirect standard output, use the `|` operator.

```pycon
>>> output_file = Path("/tmp/output_file")
>>> cmd = sh("echo", "abc") | output_file
>>> await cmd
''
>>> output_file.read_bytes()
b'abc\n'
```

To redirect standard output with append, use the `>>` operator.

```pycon
>>> cmd = sh("echo", "def") >> output_file
>>> await cmd
''
>>> output_file.read_bytes()
b'abc\ndef\n'
```

[More on redirection...](docs/redirection.md) <!-- __REL_LINK__ -->

Redirecting Standard Error
--------------------------

By default, standard error is not captured. To redirect standard error, use the `stderr`
method.

```pycon
>>> cmd = sh("cat", "does_not_exist").stderr(sh.STDOUT)
>>> await cmd.set(exit_codes={0,1})
'cat: does_not_exist: No such file or directory\n'
```

You can redirect standard error to a file or path. 

To redirect standard error to the hosting program's `sys.stderr`, use the INHERIT redirect
option.

```pycon
>>> cmd = sh("cat", "does_not_exist").stderr(sh.INHERIT)
>>> await cmd
cat: does_not_exist: No such file or directory
Traceback (most recent call last):
  ...
shellous.result.ResultError: Result(output_bytes=b'', exit_code=1, cancelled=False, encoding='utf-8', extra=None)
```

[More on redirection...](docs/redirection.md) <!-- __REL_LINK__ -->

Pipelines
---------

You can create a pipeline by combining commands using the `|` operator.

```pycon
>>> pipe = sh("ls") | sh("grep", "README")
>>> await pipe
'README.md\n'
```

Process Substitution (Unix Only)
--------------------------------

You can pass a shell command as an argument to another.

```pycon
>>> cmd = sh("grep", "README", sh("ls"))
>>> await cmd
'README.md\n'
```

Use `.writable` to write to a command instead.

```pycon
>>> buf = bytearray()
>>> cmd = sh("ls") | sh("tee", sh("grep", "README").writable | buf) | sh.DEVNULL
>>> await cmd
''
>>> buf
bytearray(b'README.md\n')
```

Async With & For
----------------

You can loop over a command's output by using the context manager as an iterator.

```pycon
>>> async with pipe as run:
...   async for line in run:
...     print(line.rstrip())
... 
README.md
```

> <span style="font-size:1.5em;">⚠️ </span> You can also acquire an async iterator directly from
> the command or pipeline object. This is discouraged because you will have less control over the final
> clean up of the command invocation than with a context manager.

```pycon
>>> async for line in pipe:   # Use caution!
...   print(line.rstrip())
... 
README.md
```

You can use `async with` to interact with the process streams directly. You have to be careful; you
are responsible for correctly reading and writing multiple streams at the same time.

```pycon
>>> async with pipe as run:
...   data = await run.stdout.readline()
...   print(data)
... 
b'README.md\n'
```

Timeouts
--------

You can specify a timeout using the `timeout` option. If the timeout expires, shellous will raise
a `TimeoutError`.

```pycon
>>> await sh("sleep", 60).set(timeout=0.1)
Traceback (most recent call last):
  ...
TimeoutError
```

Timeouts are just a special case of cancellation.

Cancellation
------------

When a command is cancelled, shellous terminates the process and raises a `CancelledError`.

You can retrieve the partial result by setting `incomplete_result` to True. Shellous will return a
`ResultError` when the specified command is cancelled (or timed out).

```pycon
>>> sleep = sh("sleep", 60).set(incomplete_result=True)
>>> t = asyncio.create_task(sleep.coro())
>>> t.cancel()
True
>>> await t
Traceback (most recent call last):
  ...
shellous.result.ResultError: Result(output_bytes=b'', exit_code=-15, cancelled=True, encoding='utf-8', extra=None)
```

When you use `incomplete_result`, your code should respect the `cancelled` attribute in the Result object. 
Otherwise, your code may swallow the CancelledError.

Pseudo-Terminal Support (Unix Only)
-----------------------------------

To run a command through a pseudo-terminal, set the `pty` option to True. Alternatively, you can pass
a function to configure the tty mode and size.

```pycon
>>> ls = sh("ls").set(pty=shellous.cooked(cols=40, rows=10, echo=False))
>>> await ls("README.md", "CHANGELOG.md")
'CHANGELOG.md\tREADME.md\r\n'
```

Context Objects
---------------

You can store shared command settings in an immutable context object. To create a new context 
object, specify your changes to the default context **sh**:

```pycon
>>> auditor = lambda phase, info: print(phase, info["runner"].name)
>>> sh_audit = sh.set(audit_callback=auditor)
```

Now all commands created with `sh_audit` will log their progress using the audit callback.

```pycon
>>> await sh_audit("echo", "goodbye")
start echo
stop echo
'goodbye\n'
```
