Shellous Change Log
===================

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
