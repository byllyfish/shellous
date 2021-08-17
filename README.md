shellous: Run Processes and Pipelines
=====================================

[![CI](https://github.com/byllyfish/shellous/actions/workflows/ci.yml/badge.svg)](https://github.com/byllyfish/shellous/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/byllyfish/shellous/branch/main/graph/badge.svg?token=W44NZE89AW)](https://codecov.io/gh/byllyfish/shellous)

shellous is an asyncio library that provides a concise API for running subprocesses. It is 
similar to and inspired by `sh`.

```python
import asyncio
import shellous

sh = shellous.context()

async def main():
    result = await sh("echo", "hello, world")
    print(result)

asyncio.run(main())
```

Benefits
--------

- Run programs asychronously in a single line.
- Easily capture output or redirect stdin, stdout and stderr to files.
- Easily construct pipelines.

Requirements
------------

- Requires Python 3.9 or later.
- Runs on Linux, MacOS and Windows.

Basic Usage
-----------

Start the asyncio REPL by typing `python3 -m asyncio`, and import the **shellous** module:

```python-repl
>>> import shellous
```

Before we can do anything else, we need to create a **context**. This is our chance to customize
settings like environment variables or text encoding. Store the context in a short variable
name like `sh` because we'll be typing it a lot.

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
shellous.result.ResultError: (Result(output_bytes=b'', exit_code=1, cancelled=False, ...
```

To return a `Result` object instead of raising an exception, set the `allowed_exit_codes` option
together with the `return_result` option.

```python-repl
>>> await sh("cat", "does_not_exist").set(return_result=True, allowed_exit_codes={0,1})
Result(output_bytes=b'', exit_code=1, cancelled=False, encoding='utf-8', extra=None)
```

You can wire this into a `cat` command to do it automatically.

```python-repl
>>> cat = sh("cat").set(return_result=True, allowed_exit_codes={0,1})
>>> await cat("does_not_exist")
Result(output_bytes=b'', exit_code=1, cancelled=False, encoding='utf-8', extra=None)
```

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
>>> cmd = Path("/usr/bin/wc") | sh("wc", "-c")
>>> await cmd
'  137968\n'
```

[More on redirection...](docs/redirection.md)

Redirecting Standard Output
---------------------------

To redirect standard output, use the `|` operator.

```python-repl
>>> output_file = Path("/tmp/output_file")
>>> cmd = sh("echo", "abc") | output_file
>>> await cmd
>>> output_file.read_bytes()
b'abc\n'
```

To redirect standard output with append, use the `>>` operator.

```python-repl
>>> cmd = sh("echo", "def") >> output_file
>>> await cmd
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
>>> await cmd.set(allowed_exit_codes={0,1})
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
shellous.result.ResultError: (Result(output_bytes=b'', exit_code=1, cancelled=False, ...
```

[More on redirection...](docs/redirection.md)

Pipelines
---------

You can create a pipeline by combining commands using the `|` operator.

```python-repl
>>> pipe = sh("echo", "abc") | sh("wc", "-c")
>>> await pipe
'       4\n'
```

