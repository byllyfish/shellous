"""
.. include:: ../README.md
"""

# pylint: disable=cyclic-import
# pyright: reportUnusedImport=false

__version__ = "0.24.0"

import sys
import warnings

from .command import AuditEventInfo, CmdContext, Command, Options
from .pipeline import Pipeline
from .pty_util import cbreak, cooked, raw
from .result import PipeResult, Result, ResultError  # noqa: F401
from .runner import AUDIT_EVENT_SUBPROCESS_SPAWN, PipeRunner, Runner

if sys.platform != "win32":
    from .watcher import DefaultChildWatcher


if sys.version_info[:3] in [(3, 10, 9), (3, 11, 1)]:
    # Warn about these specific Python releases: 3.10.9 and 3.11.1
    # These releases have a known race condition.
    warnings.warn(
        "Python 3.10.9 and Python 3.11.1 are unreliable with respect to "
        "asyncio subprocesses. Consider a newer Python release: 3.10.10+ "
        "or 3.11.2+. (https://github.com/python/cpython/issues/100133)",
        RuntimeWarning,
    )


sh = CmdContext[str]()
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
    "Options",
    "Pipeline",
    "cbreak",
    "cooked",
    "raw",
    "Result",
    "ResultError",
    "Runner",
    "PipeRunner",
    "DefaultChildWatcher",
    "AUDIT_EVENT_SUBPROCESS_SPAWN",
    "AuditEventInfo",
]
