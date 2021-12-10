"""
.. include:: ../README.md
"""
__docformat__ = "restructuredtext"
__version__ = "0.12.0"

import sys

# pylint: disable=cyclic-import
from .command import CmdContext, Command, Options  # noqa: F401
from .pipeline import Pipeline  # noqa: F401
from .pty_util import cbreak, cooked, raw  # noqa: F401
from .redirect import Redirect
from .result import PipeResult, Result, ResultError  # noqa: F401
from .runner import AUDIT_EVENT_SUBPROCESS_SPAWN  # noqa: F401

if sys.platform != "win32":
    from .watcher import DefaultChildWatcher  # noqa: F401

STDOUT = Redirect.STDOUT
DEVNULL = Redirect.DEVNULL
CAPTURE = Redirect.CAPTURE
INHERIT = Redirect.INHERIT


def context() -> CmdContext:
    "Construct a new execution context."
    return CmdContext()
