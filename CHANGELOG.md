Shellous Change Log
===================

0.16.0
------

- [API] Add `sh` global execution context and update documentation to use it instead of `context()`.
- [LATERAL] Update `immutables` dependency version.
 -[LATERAL] Add continuous integration support for pypy-3.9.
 
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
- [BUGFIX] Manually reap process when childwatcher fails to detect child exit. (#178)
- [LATERAL] Add tests to make sure that Command, Pipeline and Result objects can be pickled.
- [LATERAL] Remove context reference from command Options class.

0.10.0
------

- [FEATURE] Experimental child watcher now supports linux using pidfd.
- [BUGFIX] Fix process still running error (#172).
- [BUGFIX] Improve exit status reporting after a SIGTERM signal with experimental child watcher.
- [LATERAL] Add continuous integration support for Alpine linux.

0.9.3
-----

- [FEATURE] Add % operator to concatenate commands, i.e. sh("nohup") % sh("echo") == sh("nohup", "echo")
- [API] Replace command and pipe's ~ operator with .writable property.
- [BUGFIX] Fix bug where GeneratorExit was not being re-raised.
- [BUGFIX] Clean capfd output in tests (#151).
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
- [BUGFIX] Handle ECHILD properly in pty waitpid code.
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
- [FEATURE] Support for uvloop.
- [FEATURE] Support cancel_timeout and cancel_signal options.
- [FEATURE] Support redirection to BytesIO and bytearray.
- [BUGFIX] Fix support for string encoding errors.
- [BUGFIX] Many improvements to internal logging.

0.1.0 
-----

- Initial release to PyPI.
