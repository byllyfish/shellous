"""
.. include:: ../README.md
"""
__docformat__ = "restructuredtext"

__version__ = "0.3.0"

from .command import Command, context  # noqa: F401
from .pipeline import Pipeline  # noqa: F401
from .redirect import Redirect
from .result import PipeResult, Result, ResultError  # noqa: F401

STDOUT = Redirect.STDOUT
DEVNULL = Redirect.DEVNULL
CAPTURE = Redirect.CAPTURE
INHERIT = Redirect.INHERIT
