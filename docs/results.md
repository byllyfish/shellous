Results
=======

When a command completes with `return_result=True`, it will return a `Result` object.

The `Result` object has the following properties:

- output - the captured stdout of the command as a string (or '' if not captured).
- output_bytes - the captured stdout of the command as bytes (or None if not captured).
- exit_code - the exit code of the process (or None if it didn't start or the process was abandoned).
- cancelled - true if command was cancelled.
- encoding - the string encoding used to interpret the byte output.
- pipe_results - results for each command of a pipeline (list of Result, ResultError, or Exception).


ResultError
----------

When a command fails, it raises a `ResultError` exception. The `ResultError` object has one property:

- result - the result of the command

Normally, a command will fail as a result of a non-zero exit code. You can modify this behavior by 
using the `exit_codes` setting.

To return a `Result` object instead of raising an exception, set the `exit_codes` option
together with the `return_result` option.

```python-repl
>>> await sh("cat", "does_not_exist").set(return_result=True, exit_codes={0,1})
Result(output_bytes=b'', exit_code=1, cancelled=False, encoding='utf-8', extra=None)
```

When a command fails to start, it may raise another `Exception` subclass, say `FileNotFoundError` 
or `PermissionError`.


Cancellation
------------

You can cancel a command by cancelling its running Task using `Task.cancel()`.

When a command is cancelled, it will clean up after itself and raise a `ResultErrror`. This "clean up"
involves sending a `cancel_signal` to the process and waiting for it to exit. A `ResultError`
then reports the exit code of the process, along with any partial output.

Clean up is subject to a timeout, since a stubborn process may refuse to exit. By default, shellous
waits `3` seconds for a process to terminate. You can modify timeout this using the `cancel_timeout` setting.
You can specify the signal used to terminate the cancelled command using the `cancel_signal` setting.

### Abandonment

When a cancelled process refuses to exit in a timely manner, it is abandoned. `shellous` will log a critical log 
message and raise an `AbandonError` exception with the process ID and partial result. The final `exit_code` will be
set to `None` even though the process started normally. 

Command Timeouts
----------------

To run a command with a timeout, use `asyncio.wait_for()`. This will raise a `ResultError` if the command
times out.

Because of the `cancel_timeout` behavior, `wait_for` may take longer than the timeout to finish.
