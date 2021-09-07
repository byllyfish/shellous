"""
.. include:: ../README.md
"""
__docformat__ = "restructuredtext"

__version__ = "0.3.0"

# pylint: disable=cyclic-import
from .command import Command, Context, Options  # noqa: F401
from .pipeline import Pipeline  # noqa: F401
from .redirect import Redirect
from .result import PipeResult, Result, ResultError  # noqa: F401

STDOUT = Redirect.STDOUT
DEVNULL = Redirect.DEVNULL
CAPTURE = Redirect.CAPTURE
INHERIT = Redirect.INHERIT


def context() -> Context:
    "Construct a new execution context."
    return Context()
