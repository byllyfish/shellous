import asyncio
import errno
import os
import struct
from typing import Any, NamedTuple

# The following modules are not supported on Windows.
try:
    import fcntl
    import pty
    import termios
    import tty
except ImportError:
    pass

from .log import LOGGER

_STDIN_FILENO = 0
_STDOUT_FILENO = 1
_LFLAG = 3
_CC = 6


class PtyFds(NamedTuple):
    "Track parent/child fd's for pty."
    parent_fd: int
    child_fd: int
    eof: bytes
    reader: Any = None
    writer: Any = None

    async def open_streams(self):
        reader, writer = await _open_pty_streams(self.parent_fd)
        os.close(self.child_fd)
        return PtyFds(
            self.parent_fd,
            -1,
            self.eof,
            reader,
            writer,
        )

    def close(self):
        if self.child_fd >= 0:
            os.close(self.child_fd)
        if self.writer:
            self.writer.close()


def open_pty():
    "Open pseudo-terminal (pty) descriptors. Returns (parent_fd, child_fd)."
    return pty.openpty()


def set_ctty_preexec_fn():
    "Explicitly open the tty to make it become a controlling tty."
    # See https://github.com/python/cpython/blob/3.9/Lib/pty.py
    tmpfd = os.open(os.ttyname(_STDOUT_FILENO), os.O_RDWR)
    os.close(tmpfd)


class PtyStreamReaderProtocol(asyncio.StreamReaderProtocol):
    "Custom subclass of StreamReaderProtocol for pty's."

    def connection_lost(self, exc):
        "Intercept EIO error and treat it as EOF."
        if isinstance(exc, OSError) and exc.errno == errno.EIO:
            exc = None
            LOGGER.info("connection_lost EIO -> EOF")
        super().connection_lost(exc)


async def _open_pty_streams(file_desc):
    "Open reader, writer streams for pty file descriptor."
    fcntl.fcntl(file_desc, fcntl.F_SETFL, os.O_NONBLOCK)

    reader_pipe = os.fdopen(file_desc, "rb", 0, closefd=False)
    writer_pipe = os.fdopen(file_desc, "wb", 0, closefd=True)

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(loop=loop)
    reader_protocol = PtyStreamReaderProtocol(reader, loop=loop)
    reader_transport, _ = await loop.connect_read_pipe(
        lambda: reader_protocol,
        reader_pipe,
    )

    writer_protocol = asyncio.streams.FlowControlMixin()
    writer_transport, writer_protocol = await loop.connect_write_pipe(
        lambda: writer_protocol,
        writer_pipe,
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

    # Patch writer_transport.close so it also closes the reader_transport.
    def _close():
        _orig_close()
        reader_transport.close()

    writer_transport.close, _orig_close = _close, writer_transport.close

    return reader, writer


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
