"""
.. include:: ../README.md
"""
__docformat__ = "restructuredtext"

__version__ = "0.5.1"

# pylint: disable=cyclic-import
from .command import CmdContext, Command, Options  # noqa: F401
from .pipeline import Pipeline  # noqa: F401
from .pty_util import canonical, cbreak, raw  # noqa: F401
from .redirect import Redirect
from .result import PipeResult, Result, ResultError  # noqa: F401

STDOUT = Redirect.STDOUT
DEVNULL = Redirect.DEVNULL
CAPTURE = Redirect.CAPTURE
INHERIT = Redirect.INHERIT


def context() -> CmdContext:
    "Construct a new execution context."
    return CmdContext()
