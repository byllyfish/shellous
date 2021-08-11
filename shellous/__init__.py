"shellous provides a concise API for running subprocesses."

__version__ = "0.1.0"

from .command import context, pipeline  # noqa: F401
from .result import PipeResult, Result, ResultError  # noqa: F401
from .util import Redirect

STDOUT = Redirect.STDOUT
DEVNULL = Redirect.DEVNULL
CAPTURE = Redirect.CAPTURE
INHERIT = Redirect.INHERIT
