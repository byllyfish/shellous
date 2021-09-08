Async Processes and Pipelines
=============================

[![PyPI](https://img.shields.io/pypi/v/shellous)](https://pypi.org/project/shellous/) [![CI](https://github.com/byllyfish/shellous/actions/workflows/ci.yml/badge.svg)](https://github.com/byllyfish/shellous/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/byllyfish/shellous/branch/main/graph/badge.svg?token=W44NZE89AW)](https://codecov.io/gh/byllyfish/shellous)

shellous provides a concise API for running subprocesses using asyncio. It is 
similar to and inspired by [sh](https://pypi.org/project/sh/).

```python
import asyncio
import shellous

sh = shellous.context()

async def main():
    result = await (sh("ls") | sh("grep", "README"))
    print(result)

asyncio.run(main())
```

[More Documentation...](https://byllyfish.github.io/shellous/shellous.html)

Benefits
--------

- Run programs asychronously in a single line.
- Easily capture output or redirect stdin, stdout and stderr to files.
- Easily construct [pipelines](https://en.wikipedia.org/wiki/Pipeline_(Unix)) and use [process substitution](https://en.wikipedia.org/wiki/Process_substitution).
- Runs on Linux, MacOS and Windows.

Requirements
------------

- Requires Python 3.9 or later.
- Requires an asyncio event loop.
- Process substitution requires a Unix system with /dev/fd support.

Basic Usage
-----------

Start the asyncio REPL by typing `python3 -m asyncio`, and import the **shellous** module:

```python-repl
>>> import shellous
```

Before we can do anything else, we need to create a **context**. Store the context in a 
short variable name like `sh` because we'll be typing it a lot.

```python-repl
>>> sh = shellous.context()
```

Now, we're ready to run our first command. Here's one that runs `echo "hello, world"`.

```python-repl
>>> await sh("echo", "hello, world")
'hello, world\n'
```

The first argument is the program name. It is followed by zero or more separate arguments.

A command does not run until you `await` it. Here, we create our own echo command with "-n"
to omit the newline. Note, `echo("abc")` is the same as `echo -n "abc"`.

```python-repl
>>> echo = sh("echo", "-n")
>>> await echo("abc")
'abc'
```

[More on commands...](docs/commands.md)

Results and Exit Codes
----------------------

When you `await` a command, it captures the standard output and returns it. You can optionally have the
command return a `Result` object. The `Result` object will contain more information about the command 
execution including the `exit_code`. To return a result object, set `return_result` option to `True`.

```python-repl
>>> await echo("abc").set(return_result=True)
Result(output_bytes=b'abc', exit_code=0, cancelled=False, encoding='utf-8', extra=None)
```

The above command had an exit_code of 0.

If a command exits with a non-zero exit code, it raises a `ResultError` exception that contains
the `Result` object.

```python-repl
>>> await sh("cat", "does_not_exist")
Traceback (most recent call last):
  ...
shellous.result.ResultError: Result(output_bytes=b'', exit_code=1, cancelled=False, encoding='utf-8', extra=None)
```

[More on results...](docs/results.md)

Redirecting Standard Input
--------------------------

You can change the standard input of a command by using the `|` operator.

```python-repl
>>> cmd = "abc" | sh("wc", "-c")
>>> await cmd
'       3\n'
```

To redirect stdin using a file's contents, use a `Path` object from `pathlib`.

```python-repl
>>> from pathlib import Path
>>> cmd = Path("README.md") | sh("wc", "-l")
>>> await cmd
'     255\n'
```

[More on redirection...](docs/redirection.md)

Redirecting Standard Output
---------------------------

To redirect standard output, use the `|` operator.

```python-repl
>>> output_file = Path("/tmp/output_file")
>>> cmd = sh("echo", "abc") | output_file
>>> await cmd
''
>>> output_file.read_bytes()
b'abc\n'
```

To redirect standard output with append, use the `>>` operator.

```python-repl
>>> cmd = sh("echo", "def") >> output_file
>>> await cmd
''
>>> output_file.read_bytes()
b'abc\ndef\n'
```

[More on redirection...](docs/redirection.md)

Redirecting Standard Error
--------------------------

By default, standard error is not captured. To redirect standard error, use the `stderr`
method.

```python-repl
>>> cmd = sh("cat", "does_not_exist").stderr(shellous.STDOUT)
>>> await cmd.set(exit_codes={0,1})
'cat: does_not_exist: No such file or directory\n'
```

You can redirect standard error to a file or path. 

To redirect standard error to the hosting program's `sys.stderr`, use the INHERIT redirect
option.

```python-repl
>>> cmd = sh("cat", "does_not_exist").stderr(shellous.INHERIT)
>>> await cmd
cat: does_not_exist: No such file or directory
Traceback (most recent call last):
  ...
shellous.result.ResultError: Result(output_bytes=b'', exit_code=1, cancelled=False, encoding='utf-8', extra=None)
```

[More on redirection...](docs/redirection.md)

Pipelines
---------

You can create a pipeline by combining commands using the `|` operator.

```python-repl
>>> pipe = sh("ls") | sh("grep", "README")
>>> await pipe
'README.md\n'
```

Process Substitution (Unix Only)
--------------------------------

You can pass a shell command as an argument to another.

```python-repl
>>> cmd = sh("grep", "README", sh("ls"))
>>> await cmd
'README.md\n'
```

Use ~ to write to a command instead.

```python-repl
>>> buf = bytearray()
>>> cmd = sh("ls") | sh("tee", ~sh("grep", "README") | buf) | shellous.DEVNULL
>>> await cmd
''
>>> buf
bytearray(b'README.md\n')
```

Async With & For
----------------

You can use `async with` to interact with the process streams directly. You have to be careful; you
are responsible for correctly reading and writing multiple streams at the same time.

```python-repl
>>> async with pipe.run() as run:
...   data = await run.stdout.readline()
...   print(data)
... 
b'README.md\n'
```

You can loop over a command's output by using the context manager as an iterator.

```python-repl
>>> async with pipe.run() as run:
...   async for line in run:
...     print(line.rstrip())
... 
README.md
```


Incomplete Results
------------------

When a command is cancelled, shellous normally cleans up after itself and re-raises a `CancelledError`.

You can retrieve the partial result by setting `incomplete_result` to True. Shellous will return a
`ResultError` when the specified command is cancelled.

```python-repl
>>> sleep = sh("sleep", 60).set(incomplete_result=True)
>>> t = asyncio.create_task(sleep.coro())
>>> t.cancel()
True
>>> await t
Traceback (most recent call last):
  ...
shellous.result.ResultError: Result(output_bytes=None, exit_code=-15, cancelled=True, encoding='utf-8', extra=None)
```

When you use `incomplete_result`, your code should respect the `cancelled` attribute in the Result object. 
Otherwise, your code may swallow the CancelledError.
