Shellous Change Log
===================

0.32.0
------

- [FEATURE] Add the `Command.prompt()` method to facilitate using the `Prompt` API.
- [FEATURE] Refactor the `Prompt` helper class to support regular expression matching.
- [FEATURE] The `Prompt.expect()` method can match on a regular expression in the output stream. 
- [API] The `Prompt.send()` method no longer returns a response. If you want to send a string to a process and wait for the response, use the `Prompt.command()` method instead.
- [API] The `receive()` method has been removed. Use `expect()` instead.
- [BUGFIX] Fix a potential deadlock issue in `Prompt.send()` by continuing to read pending input from the subprocess concurrently.

0.31.1
------

- [LATERAL] Fix issue with CI action that publishes the shellous package to PyPI.

0.31.0
------

- [API] Several API changes to the experimental `Prompt` class.
- [API] The `Result` class now has an `exit_signal` property that converts the `exit_code` value into a `Signals` enum.
- [LATERAL] Update CI settings for Python 3.12 release. Add pip --require-hashes to Github actions.
- [LATERAL] Apply security best practices to Github actions.

0.30.0
------

- [LATERAL] Improve documentation and add security policy to the GitHub repository.
- [LATERAL] Fix two more log messages so they only appear when the SHELLOUS_DEBUG environment variable is set.
- [LATERAL] Improve reliability of CI testing on Windows.

0.29.0
------

- [BUGFIX] Report standard error when a pipeline sub-command fails. (#459)
- [LATERAL] Reduce debug logging so it only occurs when SHELLOUS_DEBUG environment variable is set.
- [LATERAL] Fix Windows CI tests for Python 3.11.5.

0.28.0
------

- [FEATURE] Add the `coerce_arg` option in `Command` and `CmdContext`. (#397)
- [BUGFIX] Improve support for built-in sequence types like `range/zip/reversed` when used as command arguments.
- [BUGFIX] Add support for using `StringIO` and `BytesIO` objects as command arguments.
- [BUGFIX] `bytearray` is now coerced to bytes when used as an argument in a Command.
- [BUGFIX] Fix subtle bug in `context_aenter/context_aexit` helpers when a re-entrant context manager overlaps in a parent/child task pair.

0.27.0
------

- [FEATURE] Add experimental `Prompt` class for working with interactive prompts in a co-process.
- [API] The `DefaultChildWatcher` class has been downgraded to an experimental-only feature.
- [API] The `AUDIT_EVENT_SUBPROCESS_SPAWN` and `UNLAUNCHED_EXIT_CODE` constants are no longer part of the public/documented API. Internally, they were renamed.
- [LATERAL] The methods of the internal `Options` class are now deliberately marked private, as they are not part of the public/documented API.
- [LATERAL] Removed support for using Ellipsis as a special value when setting environment variables.

0.26.0
------

- [FEATURE] Add `path` option to customize the search path for a command.
- [FEATURE] Add `find_command()` method to the `sh` CmdContext for use in locating a command in the `path`. (#424)
- [API] Remove support for concatenating commands using the `%` operator. The same result can be achieved using `cmd.args`. (#420)
- [LATERAL] Add CI testing for `eager_task_factory` in Python 3.12. (#437)

0.25.0
------

- [API] Removed the `extra` property from the `Result` object. `PipeResult` is no longer defined.
- [API] The `catch_cancelled_error` option has been renamed to `_catch_cancelled_err` and is now private.
- [FEATURE] The external API is now fully typed. Added a `py.typed` file.
- [BUGFIX] Added support for combining stdout(DEVNULL) and stderr(STDOUT) to produce only standard error, as if it was normal output (#381).
- [BUGFIX] Reduced verbosity of logging under the `info` level.
- [LATERAL] Refactored the `_run_` helper method used in Runner, PipeRunner.

0.24.0
------

- [API] The `Runner.run()` method is now private. The correct API is `await`, `async for` or `async with`.
- [API] The `return_result` and `writable` options in the `Options` class are now private.
- [FEATURE] Improve typing of Command, CmdContext and Pipeline to differentiate between "string" mode and "Result" mode (#375).
- [FEATURE] Improve typing of the audit_callback info; add `AuditEventInfo` as a `TypedDict`.
- [FEATURE] Allow redirection with append to more types than `Path` (#359).
- [BUGFIX] Fix bug in redirecting via `|` to a `bytearray`, `StringIO` or `BytesIO` where the original content was not replaced/truncated.
- [BUGFIX] Fix unintended dependency on typing_extensions module; shellous has no required dependencies.
- [BUGFIX] Fix edge case on FreeBSD where running command in a pty that produces no output causes a shellous command to hang (#378).
- [LATERAL] Add CI testing for Python 3.12-dev.
- [LATERAL] Fix CI code coverage due to codecov API changes.
- [LATERAL] Replace flake8 with ruff in CI.

0.23.0
------

- [API] When using `async with`, you MUST tag the desired input/output streams with `sh.CAPTURE`. The new default is to capture nothing; the previous default was `stdout(sh.CAPTURE)`.
- [API] In Runner/PipeRunner, the `result()` method will always return a `Result` object. Previously, it could return `str`.
- [API] Setting the encoding to `None` is no longer allowed. If you want `bytes` output, use the `Result` object. This was previously a DeprecationWarning.
- [API] The `sh.RESULT` constant has been renamed to `sh.BUFFER` to avoid confusion with the `sh.result` modifier.
- [FEATURE] Added the `.result` modifier to CmdContext. You can now request a `Result` using the syntax `await sh.result("echo")`.

0.22.0
------

- [FEATURE] The default for standard error is to write to `error_bytes` in the Result object (saving the first 1024 bytes only). If a command fails, this makes it easier to see what went wrong.
- [FEATURE] Issue a warning when using Python '3.10.9' and '3.11.1'. These specific releases have a known race condition bug that affects asyncio subprocess output.
- [API] Setting the output encoding to `None` issues a DeprecationWarning. If you want the bytes output, use the `Result` object.
- [LATERAL] Reorder the fields in the `Result` object to improve readability.

0.21.0
------

- [BUGFIX] Fix an issue with pty support on Linux. The bug led to closing a file descriptor that was already closed.
- [LATERAL] Add stronger typing support internally.
- [LATERAL] Refactor _RunOptions internal class.
- [LATERAL] Remove dependency on immutables module.

0.20.0
------

- [API] Add `cancel` and `send_signal` methods to `Runner` class.
- [LATERAL] Add some type annotations internally.

0.19.0
------

- [API] Remove DeprecationWarnings. Literal constants are no longer supported in redirections.
- [API] Redirecting stdout/stderr to a string/bytes file name is no longer supported. (Use `pathlib.Path`)
- [API] Remove the deprecated `context()` function.
- [LATERAL] Improve test reliability by increasing some timeouts slightly.

0.18.0
------

- [LATERAL] Update `immutables` dependency version.
- [LATERAL] Include ThreadStrategy in code coverage tests.

0.17.0
------

- [API] In the future, refer to redirection constants using `sh`: `sh.DEVNULL`, `sh.STDOUT`, `sh.INHERIT`.
- [API] Redirecting output to a str or bytes object raises a DeprecationWarning; use a Path object instead.
- [API] The `context()` function now raises a DeprecationWarning.
- [API] Using literal constants None, ..., () and 1 in redirection operators now raises a DeprecationWarning.
- [API] Rename `incomplete_result` setting to `catch_cancelled_error`.
- [LATERAL] Work-around test that is failing on Alpine linux (#241).

0.16.0
------

- [API] Add `sh` global execution context and update documentation to use it instead of `context()`.
- [LATERAL] Update `immutables` dependency version.
- [LATERAL] Add continuous integration support for pypy-3.9.
 
0.15.0
------

- [LATERAL] Clean up DefaultChildWatcher implementation. Keep references to created tasks.
- [LATERAL] Use a contextvar to control whether to deactivate child watcher for certain processes.
- [BUGFIX] Use `.name` to convert signal enums to strings rather than `str()`.

0.14.0
------

- [API] In audit_callback, signal names no longer have prefix "Signals."; just the signal name, e.g. "SIGTERM".
- [LATERAL] Run CI tests on Python 3.11-dev.
- [LATERAL] Update copyright year.

0.13.0
------

- [FEATURE] Add the `.result` modifier to Command and Pipeline.
- [BUGFIX] Fix failing test on Python 3.10.1 due to change in behavior of `asyncio.wait_for`.
- [LATERAL] Add more timeout-cancellation tests.

0.12.0
------

- [BUGFIX] Fix race condition in process substitution where process is left running due to exception.
- [BUGFIX] Handle pidfd_open case where we run out of file descriptors.

0.11.0
------

- [FEATURE] Custom child watcher class, DefaultChildWatcher, automatically picks between pidfd, kqueue and thread implementation.
- [BUGFIX] Manually reap process when child watcher fails to detect child exit. (#178)
- [LATERAL] Add tests to make sure that Command, Pipeline and Result objects can be pickled.
- [LATERAL] Remove context reference from command Options class.

0.10.0
------

- [FEATURE] Experimental child watcher now supports linux using pidfd.
- [BUGFIX] Fix process still running error (#172).
- [BUGFIX] Improve exit status reporting after a `SIGTERM` signal with experimental child watcher.
- [LATERAL] Add continuous integration support for Alpine linux.

0.9.3
-----

- [FEATURE] Add % operator to concatenate commands, i.e. sh("nohup") % sh("echo") == sh("nohup", "echo")
- [API] Replace command and pipe's ~ operator with .writable property.
- [BUGFIX] Fix bug where GeneratorExit was not being re-raised.
- [BUGFIX] Clean captured output in tests (#151).
- [LATERAL] Add tests to check for open/inherited file descriptors in subprocess using lsof.

0.9.2
-----

- [BUGFIX] Fix bug in `context_aexit` utility function.
- [LATERAL] Fix mypy/pylint issues.
- [LATERAL] More async iterator tests.
- [LATERAL] Add tests for experimental kqueue child watcher.

0.9.1
-----

- [FEATURE] Add `timeout` option as an alterative to using `asyncio.wait_for` (#132).
- [LATERAL] Fix bug in test: test_audit_block_pipe_specific_cmd (#122)
- [LATERAL] Add multi-threaded tests and test improvements to MultiLoopChildWatcher.
- [LATERAL] Run tests on FreeBSD 12.2.

0.9.0
-----

- [API] Rename shellous.canonical() helper function to cooked().
- [FEATURE] Add SHELLOUS_DEBUG environment variable to enable detailed logging.
- [FEATURE] It's possible to redirect stderr to a pipe even with a pseudo-terminal (#99).
- [LATERAL] Remove Redirect.IGNORE option and just use CAPTURE for stdin under pty.
- [LATERAL] Fix mypy/typing issues.
- [LATERAL] Change development status to Beta.

0.8.0
-----

- [API] Rename preexec_fn and start_new_session options, and reserve them for testing.
- [FEATURE] Add support for redirecting to and from asyncio StreamWriter/StreamReader.
- [FEATURE] Add `close_fds` option and align defaults with posix_spawn (#78).
- [FEATURE] Add support for redirection constant literals: ..., (), and 1.
- [FEATURE] Add support for async context manager API directly to Command and Pipeline.
- [FEATURE] Add support for direct iteration (using an implicit context manager.)
- [FEATURE] Add the `audit_callback` option.
- [BUGFIX] Fix stdin redirects from BytesIO and StringIO.
- [BUGFIX] Do not allow create_subprocess_exec to be interrupted when cancelled.
- [LATERAL] Refactor and improve test coverage.

0.7.0
-----

- [FEATURE] Add support for redirecting stdout/stderr to a logging.Logger.
- [FEATURE] Add PEP 578 audit support: AUDIT_EVENT_SUBPROCESS_SPAWN (#83).
- [FEATURE] Add IGNORE redirect option for stdin (#77).
- [API] Change defaults for PTY redirections (stdin -> IGNORE, stderr -> STDOUT).
- [BUGFIX] Fix character encoding issue with input (#88).
- [BUGFIX] Fix clean up of subcommands when there is an exception (#82).
- [BUGFIX] Return correct exit status from failed/cancelled pty commands.
- [BUGFIX] Handle `ECHILD` properly in pty `waitpid` code.
- [BUGFIX] Improve reliability of pty mode on FreeBSD and MacOS (#76, #84).
- [LATERAL] Test readme REPL commands on all platforms except Windows.

0.6.0
-----

- [FEATURE] Add support for FreeBSD.
- [BUGFIX] Improve pty support on MacOS.

0.5.1
-----

- [BUGFIX] Fix sed script in 'publish' github action.

0.5.0
-----

- [FEATURE] Add support for pseudo-terminals (pty).
- [BUGFIX] Case where process exits before we can write to stdin... (#45)
- [BUGFIX] Sleep command interrupted at start (#62)
- [BUGFIX] Add documentation badge; remove non-working relative links from pypi readme.

0.4.0
-----

- [FEATURE] Add support for process substitution on Unix.
- [API] Rename Context to CmdContext.
- [API] Remove task() method on Command/Pipeline.
- [BUGFIX] Always finish stdin.wait_closed(), even if cancelled.
- [BUGFIX] Add pypi classifiers to project.

0.3.0
-----

- [API] Change API for `async with` and `async for`.
- [API] Change the way cancellation works; add the `incomplete_result` option. (#42)
- [BUGFIX] Replace gather_collect with separate harvest functions.
- [BUGFIX] Clean up logging and make sure that cancelled flag is set.
- [BUGFIX] Improve reliability of `test_broken_pipe_in_failed_pipeline` test. (#32)
- [BUGFIX] Improve testing of process child watchers.
- [BUGFIX] Fix pipeline operator overloads to match stdin/stdout methods.

0.2.0 
-----

- [FEATURE] Use ... in env() to inherit specific environment variables.
- [FEATURE] Support for `uvloop`.
- [FEATURE] Support cancel_timeout and cancel_signal options.
- [FEATURE] Support redirection to BytesIO and bytearray.
- [BUGFIX] Fix support for string encoding errors.
- [BUGFIX] Many improvements to internal logging.

0.1.0 
-----

- Initial release to PyPI.
