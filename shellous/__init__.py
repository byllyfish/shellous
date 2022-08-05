"""
.. include:: ../README.md
"""

__version__ = "0.19.0"

import sys

# pylint: disable=cyclic-import
from .command import CmdContext, Command, Options  # noqa: F401
from .pipeline import Pipeline  # noqa: F401
from .pty_util import cbreak, cooked, raw  # noqa: F401
from .result import PipeResult, Result, ResultError  # noqa: F401
from .runner import AUDIT_EVENT_SUBPROCESS_SPAWN  # noqa: F401

if sys.platform != "win32":
    from .watcher import DefaultChildWatcher  # noqa: F401


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
    "cbreak",
    "cooked",
    "raw",
    "Result",
    "ResultError",
    "AUDIT_EVENT_SUBPROCESS_SPAWN",
]
