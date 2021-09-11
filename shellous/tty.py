import fcntl
import struct
import tty

from .log import LOGGER

_STDIN_FILENO = 0


def raw(rows=0, cols=0, x=0, y=0):
    "Return a function that sets PtyOptions.child_fd to raw mode."

    if Ellipsis in (rows, cols, x, y):
        rows, cols, x, y = _inherit_term_size(rows, cols, x, y)

    def _pty_set_raw(pty_fds):
        tty.setraw(pty_fds.child_fd)
        if rows or cols or x or y:
            _set_term_size(pty_fds.child_fd, rows, cols, x, y)

    return _pty_set_raw


def cbreak(rows=0, cols=0, x=0, y=0):
    "Return a function that sets PtyOptions.child_fd to cbreak mode."

    if Ellipsis in (rows, cols, x, y):
        rows, cols, x, y = _inherit_term_size(rows, cols, x, y)

    def _pty_set_cbreak(pty_fds):
        tty.setcbreak(pty_fds.child_fd)
        if rows or cols or x or y:
            _set_term_size(pty_fds.child_fd, rows, cols, x, y)

    return _pty_set_cbreak


def _set_term_size(fd, rows, cols, x, y):
    "Set pseudo-terminal size."
    try:
        winsz = struct.pack("HHHH", rows, cols, x, y)
        fcntl.ioctl(fd, tty.TIOCSWINSZ, winsz)
    except OSError as ex:
        LOGGER.warning("_set_term_size ex=%r", ex)


def _inherit_term_size(rows, cols, x, y):
    "Override ... with terminal setting from current stdin."
    try:
        zeros = struct.pack("HHHH", 0, 0, 0, 0)
        winsz = fcntl.ioctl(_STDIN_FILENO, tty.TIOCGWINSZ, zeros)
        winsz = struct.unpack("HHHH", winsz)
    except (OSError, struct.error) as ex:
        winsz = (0, 0, 0, 0)
        LOGGER.warning("_inherit_term_size ex=%r", ex)

    if rows is Ellipsis:
        rows = winsz[0]
    if cols is Ellipsis:
        cols = winsz[1]
    if x is Ellipsis:
        x = winsz[2]
    if y is Ellipsis:
        y = winsz[3]

    return rows, cols, x, y
