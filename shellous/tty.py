import fcntl
import struct
import termios
import tty

from .log import LOGGER

_STDIN_FILENO = 0
_LFLAG = 3
_CC = 6


def raw(rows=0, cols=0, x=0, y=0):
    "Return a function that sets PtyOptions.child_fd to raw mode."

    if Ellipsis in (rows, cols, x, y):
        rows, cols, x, y = _inherit_term_size(rows, cols, x, y)

    def _pty_set_raw(fd):
        tty.setraw(fd)
        if rows or cols or x or y:
            _set_term_size(fd, rows, cols, x, y)
        assert get_eof(fd) == b""

    return _pty_set_raw


def cbreak(rows=0, cols=0, x=0, y=0):
    "Return a function that sets PtyOptions.child_fd to cbreak mode."

    if Ellipsis in (rows, cols, x, y):
        rows, cols, x, y = _inherit_term_size(rows, cols, x, y)

    def _pty_set_cbreak(fd):
        tty.setcbreak(fd)
        if rows or cols or x or y:
            _set_term_size(fd, rows, cols, x, y)
        assert get_eof(fd) == b""

    return _pty_set_cbreak


def canonical(rows=0, cols=0, x=0, y=0, echo=True):
    "Return a function that leaves PtyOptions.child_fd in canonical mode."

    if Ellipsis in (rows, cols, x, y):
        rows, cols, x, y = _inherit_term_size(rows, cols, x, y)

    def _pty_set_canonical(fd):
        if rows or cols or x or y:
            _set_term_size(fd, rows, cols, x, y)
        if not echo:
            _set_term_echo(fd, False)
        assert get_eof(fd) == b"\x04"

    return _pty_set_canonical


def _set_term_echo(fd, echo):
    "Set pseudo-terminal echo."
    attrs = termios.tcgetattr(fd)
    curr_echo = (attrs[_LFLAG] & termios.ECHO) != 0
    if echo != curr_echo:
        if echo:
            # Set the ECHO bit.
            attrs[_LFLAG] = attrs[_LFLAG] | termios.ECHO
        else:
            # Clear the echo bit.
            attrs[_LFLAG] = attrs[_LFLAG] & ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)


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


def get_eof(fd):
    "Return the End-of-file character (EOF) if tty is in canonical mode only."

    eof = b""
    attrs = termios.tcgetattr(fd)
    if attrs[_LFLAG] & termios.ICANON:
        eof = attrs[_CC][termios.VEOF]

    return eof
