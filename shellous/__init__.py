"""
.. include:: ../README.md
"""

# pylint: disable=cyclic-import
# pyright: reportUnusedImport=false

__version__ = "0.23.0"

import sys
import warnings

from .command import CmdContext, Command, Options  # noqa: F401
from .pipeline import Pipeline  # noqa: F401
from .pty_util import cbreak, cooked, raw  # noqa: F401
from .result import PipeResult, Result, ResultError  # noqa: F401
from .runner import AUDIT_EVENT_SUBPROCESS_SPAWN, PipeRunner, Runner  # noqa: F401

if sys.platform != "win32":
    from .watcher import DefaultChildWatcher  # noqa: F401


if sys.version_info[:3] in [(3, 10, 9), (3, 11, 1)]:
    # Warn about these specific Python releases: 3.10.9 and 3.11.1
    # These releases have a known race condition.
    warnings.warn(
        "Python 3.10.9 and Python 3.11.1 are unreliable with respect to asyncio subprocesses. Consider a newer Python release: 3.10.10+ or 3.11.2+. (https://github.com/python/cpython/issues/100133)",
        RuntimeWarning,
    )


sh = CmdContext()
"""`sh` is the default command context (`CmdContext`).

Use `sh` to create commands or new command contexts.

```python
from shellous import sh
result = await sh("echo", "hello")
```
"""

__all__ = [
    "sh",
    "CmdContext",
    "Command",
    "Pipeline",
    "cbreak",
    "cooked",
    "raw",
    "Result",
    "ResultError",
    "Runner",
    "PipeRunner",
    "AUDIT_EVENT_SUBPROCESS_SPAWN",
    "DefaultChildWatcher",
]
