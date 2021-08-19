Multiple Capture
================

"Multiple capture" is when you set stdin to `CAPTURE` or more than one of stdin, stdout to `CAPTURE`. It
allows you to interact with the stdin, stdout, and stderr streams interactively.

To use "multiple capture", use `async with` syntax and a `runner`.:

```python
cmd = sh("cat").stdin(CAPTURE)

runner = cmd.runner()
async with runner as (stdin, stdout, _stderr):
    stdin.write(b"abc\n")
    output, _ = await asyncio.gather(stdout.readline(), stdin.drain())

    stdin.close()
result = runner.result(output)
```

You are responsible for managing reads and writes so they don't deadlock.


multiple capture requires 'async with'
--------------------------------------

If you see the error message "multiple capture requires 'async with'", you have attempted
to run a command either with stdin set to `CAPTURE`, or both stdout and stderr set to `CAPTURE`.

You cannot run such a command with `await` or `async for`. You **MUST** use `async with`.
