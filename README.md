# Async Processes and Pipelines

[![PyPI](https://img.shields.io/pypi/v/shellous)](https://pypi.org/project/shellous/) [![docs](https://img.shields.io/badge/-documentation-informational)](https://byllyfish.github.io/shellous/shellous.html) [![CI](https://github.com/byllyfish/shellous/actions/workflows/ci.yml/badge.svg)](https://github.com/byllyfish/shellous/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/byllyfish/shellous/branch/main/graph/badge.svg?token=W44NZE89AW)](https://codecov.io/gh/byllyfish/shellous) [![Downloads](https://static.pepy.tech/badge/shellous)](https://pepy.tech/project/shellous)

**shellous** provides a concise API for running subprocesses using [asyncio](https://docs.python.org/3/library/asyncio.html). It is 
similar to and inspired by [sh](https://pypi.org/project/sh/).

```python
import asyncio
from shellous import sh

async def main():
    result = await sh("echo", "hello")
    print(result)

asyncio.run(main())
```

## Benefits

- Run programs asynchronously in a single line.
- Redirect stdin, stdout and stderr to files, memory buffers, async streams or loggers.
- Iterate asynchronously over subprocess output.
- Set timeouts and reliably cancel running processes.
- Run a program with a pseudo-terminal (pty).
- Use send() and expect() to manually control a subprocess.
- Construct [pipelines](https://en.wikipedia.org/wiki/Pipeline_(Unix)) and use [process substitution](https://en.wikipedia.org/wiki/Process_substitution) directly from Python (no shell required).
- Runs on Linux, MacOS, FreeBSD and Windows.
- Monitor processes being started and stopped with `audit_callback` API.

## Requirements

- Requires Python 3.9 or later.
- Requires an asyncio event loop.
- Pseudo-terminals require a Unix system.
- Process substitution requires a Unix system with /dev/fd support.

## Running a Command

The tutorial in this README uses the asyncio REPL built into Python. In these examples, `>>>`
is the REPL prompt.

Start the asyncio REPL by typing `python3 -m asyncio`, and import **sh** from the **shellous** module:

```pycon
>>> from shellous import sh
```

Here's a command that runs `echo "hello, world"`.

```pycon
>>> await sh("echo", "hello, world")
'hello, world\n'
```

The first argument to `sh` is the program name. It is followed by zero or more arguments. Each argument will be
converted to a string. If an argument is a list or tuple, it is flattened recursively.

```pycon
>>> await sh("echo", 1, 2, [3, 4, (5, 6)])
'1 2 3 4 5 6\n'
```

A command does not run until you `await` it. When you run a command using `await`, it returns the value of the standard output interpreted as a UTF-8 string.
It is safe to `await` the same command object more than once.

Here, we create our own echo command with "-n" to omit the newline. Note, `echo("abc")` will run the same command as `echo -n "abc"`.

```pycon
>>> echo = sh("echo", "-n")
>>> await echo("abc")
'abc'
```

Commands are **immutable** objects that represent a program invocation: program name, arguments, environment
variables, redirection operators and other settings. When you use a method to modify a `Command`, you are
returning a new `Command` object. The original object is unchanged.

You can wrap your commands in a function to improve type safety:

```pycon
>>> from shellous import Command
>>> def exclaim(word: str) -> Command[str]:
...   return sh("echo", "-n", f"{word}!!")
... 
>>> await exclaim("Oh")
'Oh!!'
```

The type hint `Command[str]` indicates that the command returns a `str`.

### Results

When a command completes successfully, it returns the standard output (or "" if stdout is redirected). For a more detailed response, you can specify that the command should return a `Result` object by using the `.result` modifier:

```pycon
>>> await echo.result("abc")
Result(exit_code=0, output_bytes=b'abc', error_bytes=b'', cancelled=False, encoding='utf-8')
```

A `Result` object contains the command's `exit_code` in addition to its output. A `Result` is True if 
the command's exit_code is zero. You can access the string value of the output using the `.output` property:

```python
if result := await sh.result("cat", "some-file"):
    output = result.output
else:
    print(f"Command failed with exit_code={result.exit_code})
```

You can retrieve the string value of the standard error using the `.error` property. (By default, only the 
first 1024 bytes of standard error is stored.)

If a command was terminated by a signal, the `exit_code` will be the negative *signal* number.

### ResultError

If you are not using the `.result` modifier and a command fails, it raises a `ResultError` exception:

```pycon
>>> await sh("cat", "does_not_exist")
Traceback (most recent call last):
  ...
shellous.result.ResultError: Result(exit_code=1, output_bytes=b'', error_bytes=b'cat: does_not_exist: No such file or directory\n', cancelled=False, encoding='utf-8')
```

The `ResultError` exception contains a `Result` object with the exit_code and the first 1024 bytes of standard error.

In some cases, you want to ignore certain exit code values. That is, you want to treat them as if they are 
normal. To do this, you can set the `exit_codes` option:

```pycon
>>> await sh("cat", "does_not_exist").set(exit_codes={0,1})
''
```

If there is a problem launching a process, shellous can also raise a separate `FileNotFoundError` or  `PermissionError` exception.

### Async For

Using `await` to run a command collects the entire output of the command in memory before returning it. You 
can also iterate over the output lines as they arrive using `async for`.

```pycon
>>> [line async for line in echo("hi\n", "there")]
['hi\n', ' there']
```

Use an `async for` loop when you want to examine the stream of output from a command, line by line. For example, suppose you want to run tail on a log file.

```python
async for line in sh("tail", "-f", "/var/log/syslog"):
    if "ERROR" in line:
        print(line.rstrip())
```

### Async With

You can use a command as an asynchronous context manager. There are two ways to run a program using
a context manager: a low-level API and a high-level API.

#### Byte-by-Byte (Low Level)

Use `async with` directly when you need byte-by-byte
control over the individual streams: stdin, stdout and stderr. To control a standard stream, you
must tell shellous to "capture" it (For more on this, see [Redirection](#redirection).)

```python
cmd = sh("cat").stdin(sh.CAPTURE).stdout(sh.CAPTURE)
async with cmd as run:
    run.stdin.write(b"abc")
    run.stdin.close()
    print(await run.stdout.readline())

result = run.result()
```

The streams `run.stdout` and `run.stderr` are `asyncio.StreamReader` objects. The stream `run.stdin`
is an `asyncio.StreamWriter` object. If we didn't specify that stdin/stdout are `sh.CAPTURE`, the 
streams `run.stdin` and `run.stdout` would be 
`None`. 

The return value of `run.result()` is a `Result` object. Depending on the command settings, this 
function may raise a `ResultError` on a non-zero exit code.

> :warning: When reading or writing individual streams, you are responsible for managing reads and writes so they don't
deadlock. You may use `run.create_task` to schedule a concurrent task.

You can also use `async with` to run a server. When you do so, you must tell the server
to stop using `run.cancel()`. Otherwise, the context manager will wait forever for the process to exit.

```python
async with sh("some-server") as run:
    # Send commands to the server here...
    # Manually signal the server to stop.
    run.cancel()
```

#### Prompt with Send/Expect (High Level API)

Use the `prompt()` method to control a process using `send` and `expect`. The `prompt()`
method returns an asynchronous context manager (the `Prompt` class) that facilitates reading and 
writing strings and matching regular expressions.

```python
cmd = sh("cat").set(pty=True)

async with cmd.prompt() as client:
  await client.send("abc")
  output, _ = await client.expect("\r\n")
  print(output)

assert client.result.exit_code == 0
```

The `prompt()` API automatically captures `stdin` and `stdout`.

## Redirection

shellous supports the redirection operators `|` and `>>`. They work similar to how they work in 
the unix shell. Shellous does not support use of `<` or `>` for redirection. Instead, replace these 
with `|`.

To redirect to or from a file, use a `pathlib.Path` object. Alternatively, you can redirect input/output
to a StringIO object, an open file, a Logger, or use a special redirection constant like `sh.DEVNULL`.

> :warning: When combining the redirect operators with `await`, you must use parentheses; `await` has higher
precedence than `|` and `>>`.

### Redirecting Standard Input

To redirect standard input, use the pipe operator `|` with the argument on the **left-side**.
Here is an example that passes the string "abc" as standard input.

```pycon
>>> cmd = "abc" | sh("wc", "-c")
>>> await cmd
'       3\n'
```

To read input from a file, use a `Path` object from `pathlib`.

```pycon
>>> from pathlib import Path
>>> cmd = Path("LICENSE") | sh("wc", "-l")
>>> await cmd
'     201\n'
```

Shellous supports different STDIN behavior when using different Python types.

| Python Type | Behavior as STDIN |
| ----------- | --------------- |
| str | Read input from string object. |
| bytes, bytearray | Read input from bytes object. |
| Path | Read input from file specified by `Path`. |
| File, StringIO, ByteIO | Read input from open file object. |
| int | Read input from existing file descriptor. |
| asyncio.StreamReader | Read input from `StreamReader`. |
| sh.DEVNULL | Read input from `/dev/null`. |
| sh.INHERIT  | Read input from existing `sys.stdin`. |
| sh.CAPTURE | You will write to stdin interactively. |

### Redirecting Standard Output

To redirect standard output, use the pipe operator `|` with the argument on the **right-side**. Here is an 
example that writes to a temporary file.

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

Shellous supports different STDOUT behavior when using different Python types.

| Python Type | Behavior as STDOUT/STDERR | 
| ----------- | --------------- | 
| Path | Write output to the file path specified by `Path`.  | 
| bytearray | Write output to a mutable byte array. | 
| File, StringIO, ByteIO | Write output to an open file object. | 
| int | Write output to existing file descriptor at its current position. ◆ | 
| logging.Logger | Log each line of output. ◆ | 
| asyncio.StreamWriter | Write output to `StreamWriter`. ◆ | 
| sh.CAPTURE | Capture output for `async with`. ◆ | 
| sh.DEVNULL | Write output to `/dev/null`. ◆ | 
| sh.INHERIT  | Write output to existing `sys.stdout` or `sys.stderr`. ◆ | 

◆ For these types, there is no difference between using `|` and `>>`.

Shellous does **not** support redirecting standard output/error to a plain `str` or `bytes` object. 
If you intend to redirect output to a file, you must use a `pathlib.Path` object.

### Redirecting Standard Error

By default, the first 1024 bytes of standard error is collected into the Result object.

To redirect standard error, use the `stderr` method. Standard error supports the
same Python types as standard output. To append, set `append=True` in the `stderr` method.

To redirect stderr to the same place as stdout, use the `sh.STDOUT` constant. If you also
redirect stdout to `sh.DEVNULL`, you will only receive the standard error.

```pycon
>>> cmd = sh("cat", "does_not_exist").stderr(sh.STDOUT)
>>> await cmd.set(exit_codes={0,1})
'cat: does_not_exist: No such file or directory\n'
```

To redirect standard error to the hosting program's `sys.stderr`, use the `sh.INHERIT` redirect
option.

```pycon
>>> cmd = sh("cat", "does_not_exist").stderr(sh.INHERIT)
>>> await cmd
cat: does_not_exist: No such file or directory
Traceback (most recent call last):
  ...
shellous.result.ResultError: Result(exit_code=1, output_bytes=b'', error_bytes=b'', cancelled=False, encoding='utf-8')
```

If you redirect stderr, it will no longer be stored in the Result object.

### Default Redirections

For regular commands, the default redirections are:

- Standard input is read from the empty string ("").
- Standard out is buffered and stored in the Result object (BUFFER).
- First 1024 bytes of standard error is buffered and stored in the Result object (BUFFER).

However, the default redirections are adjusted when using a pseudo-terminal (pty):

- Standard input is captured and ignored (CAPTURE).
- Standard out is buffered and stored in the Result object (BUFFER).
- Standard error is redirected to standard output (STDOUT).

## Pipelines

You can create a pipeline by combining commands using the `|` operator. A pipeline feeds the standard out of one process into the next process as standard input. Here is the shellous
equivalent to the bash command: `ls | grep README`

```pycon
>>> pipe = sh("ls") | sh("grep", "README")
>>> await pipe
'README.md\n'
```

A pipeline returns a `Result` if the last command in the pipeline has the `.result` modifier.

```pycon
>>> pipe = sh("ls") | sh("grep", "README").result
>>> await pipe
Result(exit_code=0, output_bytes=b'README.md\n', error_bytes=b'', cancelled=False, encoding='utf-8')
```

Error reporting for a pipeline is implemented similar to using the `-o pipefail` shell option.

## Process Substitution (Unix Only)

You can pass a shell command as an argument to another. Here is the shellous equivalent to the bash
command: `grep README <(ls)`.

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

The above example is equivalent to `ls | tee >(grep README > buf) > /dev/null`.

## Timeouts

You can specify a timeout using the `timeout` option. If the timeout expires, shellous will raise
a `TimeoutError`.

```pycon
>>> await sh("sleep", 60).set(timeout=0.1)
Traceback (most recent call last):
  ...
TimeoutError
```

Timeouts are just a special case of **cancellation**. When a command is cancelled, shellous terminates 
the running process and raises a `CancelledError`.

```pycon
>>> t = asyncio.create_task(sh("sleep", 60).coro())
>>> t.cancel()
True
>>> await t
Traceback (most recent call last):
  ...
CancelledError
```

By default, shellous will send a SIGTERM signal to the process to tell it to exit. If the process does not
exit within 3 seconds, shellous will send a SIGKILL signal. You can change these defaults with the
`cancel_signal` and `cancel_timeout` settings. A command is not considered fully cancelled until the 
process exits.

## Pseudo-Terminal Support (Unix Only)

To run a command through a pseudo-terminal, set the `pty` option to True. 

```pycon
>>> await sh("echo", "in a pty").set(pty=True)
'in a pty\r\n'
```

Alternatively, you can pass a `pty` function to configure the tty mode and size.

```pycon
>>> ls = sh("ls").set(pty=shellous.cooked(cols=40, rows=10, echo=False))
>>> await ls("README.md", "CHANGELOG.md")
'CHANGELOG.md\tREADME.md\r\n'
```

Shellous provides three built-in helper functions: `shellous.cooked()`, `shellous.raw()` and `shellous.cbreak()`.

## Context Objects

You can store shared command settings in an immutable context object (CmdContext). To create a new 
context object, specify your changes to the default context **sh**:

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

You can also create a context object that specifies all return values are `Result` objects.

```pycon
>>> rsh = sh.result
>>> await rsh("echo", "whatever")
Result(exit_code=0, output_bytes=b'whatever\n', error_bytes=b'', cancelled=False, encoding='utf-8')
```

## Options

Both `Command` and `CmdContext` support options to control their runtime behavior. Some of these options (timeout,
pty, audit_callback, and exit_codes) have been described above. See the `shellous.Options` class for
more information.

You can retrieve an option from `cmd` with `cmd.options.<option>`. For example, use `cmd.options.encoding` to obtain the encoding:

```pycon
>>> cmd = sh("echo").set(encoding="latin1")
>>> cmd.options.encoding
'latin1'
```

`Command` and `CmdContext` use the `.set()` method to specify most options:

| Option | Description |
| --- | --- |
| path | Search path to use instead of the `PATH` environment variable. |
| env | Additional environment variables to pass to the command. |
| inherit_env | True if command should inherit the environment variables from the current process. (Default=True) |
| encoding | Text encoding of input/output streams. You can specify an error handling scheme by including it after a space, e.g. "ascii backslashreplace". (Default="utf-8 strict") |
| exit_codes | Set of exit codes that do not raise a `ResultError`. (DEFAULT={0}) |
| timeout | Timeout in seconds to wait before cancelling the process. |
| cancel_timeout | Timeout in seconds to wait for a cancelled process to exit before forcefully terminating it. (Default=3s) |
| cancel_signal | The signal sent to a process when it is cancelled. (Default=SIGTERM) |
| alt_name | Alternate name for the process used for debug logging. |
| pass_fds | Additional file descriptors to pass to the process. |
| pass_fds_close | True if descriptors in `pass_fds` should be closed after the process is launched. |
| pty | Used to allocate a pseudo-terminal (PTY). |
| close_fds | True if process should close all file descriptors when it starts. |
| audit_callback | Provide function to audit stages of process execution. |
| coerce_arg | Provide function to coerce `Command` arguments to strings when `str()` is not sufficient. |

### env

Use the `env()` method to **add** to the list of environment variables. The `env()` method supports keyword parameters.
You can call `env()` more than once and the effect is additive.

```pycon
>>> cmd = sh("echo").env(ENV1="a", ENV2="b").env(ENV2=3)
>>> cmd.options.env
{'ENV1': 'a', 'ENV2': '3'}
```

Use the `env` option with `set()` when you want to **replace all** the environment variables. 

### input, output, error

When you apply a redirection operator to a `Command` or `CmdContext`, the redirection targets
are also stored in the `Options` object. To change these, use the `.stdin()`, `.stdout()`, or `.stderr()` methods or the redirection operator `|`.

| Option | Description |
| --- | --- |
| input | The redirection target for standard input. |
| input_close | True if standard input should be closed after the process is launched. |
| output | The redirection target for standard output. |
| output_append | True if standard output should be open for append. |
| output_close | True if standard output should be closed after the process is launched. |
| error | The redirection target for standard error. |
| error_append | True if standard error should be open for append. |
| error_close | True if standard error should be closed after the process is launched. |

## Type Checking

Shellous fully supports PEP 484 type hints.

### Commands

Commands are generic on the return type, either `str` or `Result`. You will specify the
type of a command object as `Command[str]` or `Command[Result]`.

Use the `result` modifier to obtain a `Command[Result]` from a `Command[str]`.

```python
from shellous import sh, Command, Result

cmd1: Command[str] = sh("echo", "abc")
# When you `await cmd1`, the result is a `str` object.

cmd2: Command[Result] = sh.result("echo", "abc")
# When you `await cmd2`, the result is a `Result` object.
```

### CmdContext

The `CmdContext` class is also generic on either `str` or `Result`.

```python
from shellous import sh, CmdContext, Result

sh1: CmdContext[str] = sh.set(path="/bin:/usr/bin")
# When you use `sh1` to create commands, it produces `Command[str]` object with the given path.

sh2: CmdContext[Result] = sh.result.set(path="/bin:/usr/bin")
# When you use `sh2` to create commands, it produces `Command[Result]` objects with the given path.
```
