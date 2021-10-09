Redirection
===========

shellous supports redirecting a command's stdin, stdout, and stderr.


Redirection Constants
---------------------

shellous defines several redirection constants that you can use:

- DEVNULL
- STDOUT
- CAPTURE
- INHERIT

Standard Input
--------------

You can redirect the standard input using the `stdin(arg)` method. The behavior depends on the type of object you 
pass for `arg`.

| Python Type | What it does... |
| ----------- | --------------- |
| pathlib.Path | Read input from file specified by `Path`. |
| str | Read input from string object. |
| bytes, bytearray | Read input from bytes object. |
| *file object*<sup>1</sup> | Read input from open file object. |
| int | Read input from existing file descriptor. |
| asyncio.StreamReader | Read input from `StreamReader`. |
| DEVNULL | Read input from `/dev/null`. |
| INHERIT  | Read input from existing `sys.stdin`. |
| CAPTURE | See *Multiple Capture*. |

### Example

```python
result = await sh("cat").stdin(Path("some_file"))
```

The "left" pipe operator `|` is syntactic sugar for calling `stdin`. The following code is equivalent:

```python
result = await (Path("some_file") | sh("cat"))
```

### Close

The `stdin()` method also takes an optional `close` parameter. The `close` parameter only affects
file descriptors passed as `int`. It closes them immediately after starting the subprocess.

Standard Output & Standard Error
---------------------

You can redirect the standard output and error using the `stdout(arg)` and `stderr(arg)` methods. To append 
instead, set the `append` keyword argument to True. The behavior  depends on the type of object you pass for 
`arg`. 

| Python Type | What it does... | append=True
| ----------- | --------------- | ------
| pathlib.Path | Write output to file path specified by `Path`. | Open file for append
| str | Write output to file path specified by string object. | Open file for append
| bytes | Write output to file path specified by bytes object. | Open file for append
| bytearray | Write output to mutable byte array. | TypeError
| *file object*<sup>1</sup> | Write output to open file object. | TypeError
| int | Write output to existing file descriptor. | TypeError
| logging.Logger | Log each line of output. | TypeError
| CAPTURE | Return standard output or error. See *Multiple Capture*. | TypeError
| DEVNULL | Write output to `/dev/null`. | TypeError
| INHERIT  | Write output to existing `sys.stdout` or `sys.stderr`. | TypeError
| STDOUT | Redirect stderr to same place as stdout. | TypeError

### Example

```python
result = await sh("echo", "abc").stdout(Path("some_file"))
```

The "right" pipe operator `|` is syntactic sugar for calling `stdout`. The following code is equivalent:

```python
result = await (sh("echo", "abc") | Path("some_file"))
```

### Append

The `stdout()` and `stderr()` methods also take an optional `append` parameter. Setting `append=True` forces
shellous to open the specified files in append-only mode.

```python
result = await sh("echo", "abc").stdout(Path("some_file"), append=True)
```

The append operator `>>` is syntactic sugar for calling `stdout` with `append=True`. The following code is 
equivalent:

```python
result = await (sh("echo", "abc") >> Path("some_file"))
```

### Stderr

There is no syntactic sugar for calling the `stderr()` method. 

If you redirect stdout to someplace else. you can set stderr to CAPTURE to return just the standard error.

```python
result = await sh("ls", "non-existant-file").stdout(DEVNULL).stderr(CAPTURE)
```

Consider calling `stderr()` on your **context** object; this will affect all commands created from it.

### Close

The `stdout()` and `stderr()` methods also take an optional `close` parameter. The `close` parameter only affects
file descriptors passed as `int`. It closes them immediately after starting the subprocess.

Default Settings
----------------

- Standard input is read from the empty string.
- Standard out is captured by the program and returned (CAPTURE).
- Standard error is discarded (DEVNULL).

Default Settings for Pseudo-Terminal
------------------------------------

- Standard input is ignored (IGNORE).
- Standard out is captured by the program and returned (CAPTURE).
- Standard error is redirected to standard output (STDOUT).


----

<sup>1</sup> File objects include in-memory file objects like `StringIO` and `BytesIO`.
