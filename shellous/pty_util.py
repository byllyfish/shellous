"Implements support for pseudo-terminals."

import asyncio
import errno
import os
import struct
from typing import Any, NamedTuple, Optional

# The following modules are not supported on Windows.
try:
    import fcntl
    import pty
    import termios
    import tty
except ImportError:
    pass

from .log import LOGGER
from .util import close_fds

_STDIN_FILENO = 0
_STDOUT_FILENO = 1
_LFLAG = 3
_CC = 6


class PtyFds(NamedTuple):
    "Track parent fd for pty."
    parent_fd: int
    eof: bytes
    reader: Any = None
    writer: Any = None

    async def open_streams(self):
        "Open pty reader/writer streams."
        reader, writer = await _open_pty_streams(self.parent_fd)
        return PtyFds(
            self.parent_fd,
            self.eof,
            reader,
            writer,
        )

    def close(self):
        "Close pty file descriptors."
        LOGGER.info("PtyFds.close")
        if self.writer:
            self.writer.close()
        else:
            os.close(self.parent_fd)


def open_pty(pty_func):
    "Open pseudo-terminal (pty) descriptors. Returns (PtyFds, child_fd)."
    parent_fd, child_fd = pty.openpty()

    # If pty_func is a callable, call it here with `child_fd` as argument. This
    # gives the client an opportunity to configure the tty.
    if callable(pty_func):
        pty_func(child_fd)

    return (
        PtyFds(
            parent_fd,
            _get_eof(child_fd),
        ),
        child_fd,
    )


def set_ctty(child_fd):
    "Explicitly open the tty to make it become a controlling tty."
    # See https://github.com/python/cpython/blob/3.9/Lib/pty.py

    # Don't use ioctl TIOCSTTY; it doesn't appear to work on FreeBSD.
    tmpfd = os.open(os.ttyname(child_fd), os.O_RDWR)
    os.close(tmpfd)


class PtyStreamReaderProtocol(asyncio.StreamReaderProtocol):
    "Custom subclass of StreamReaderProtocol for pty's."

    def connection_lost(self, exc):
        "Intercept EIO error and treat it as EOF."
        LOGGER.info("PtyStreamReaderProtocol.connection_lost ex=%r", exc)
        if isinstance(exc, OSError) and exc.errno == errno.EIO:
            exc = None
            LOGGER.info("connection_lost EIO -> EOF")
        super().connection_lost(exc)

    def eof_received(self):
        "Log when EOF received."
        LOGGER.info("PtyStreamReaderProtocol.eof_received")
        return super().eof_received()


async def _open_pty_streams(file_desc):
    "Open reader, writer streams for pty file descriptor."

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(loop=loop)
    reader_protocol = PtyStreamReaderProtocol(reader, loop=loop)
    reader_transport, _ = await loop.connect_read_pipe(
        lambda: reader_protocol,
        os.fdopen(file_desc, "rb", 0, closefd=False),
    )

    writer_protocol = asyncio.streams.FlowControlMixin()
    writer_transport, writer_protocol = await loop.connect_write_pipe(
        lambda: writer_protocol,
        os.fdopen(file_desc, "wb", 0, closefd=True),
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

    # Patch writer_transport.close so it also closes the reader_transport.
    def _close():
        LOGGER.info("writer_transport.close()")
        _orig_close()
        reader_transport.close()

    writer_transport.close, _orig_close = _close, writer_transport.close

    return reader, writer


def raw(rows=0, cols=0, xpixel=0, ypixel=0):
    "Return a function that sets PtyOptions.child_fd to raw mode."

    if Ellipsis in (rows, cols, xpixel, ypixel):
        rows, cols, xpixel, ypixel = _inherit_term_size(rows, cols, xpixel, ypixel)

    def _pty_set_raw(fdesc):
        tty.setraw(fdesc)
        if rows or cols or xpixel or ypixel:
            _set_term_size(fdesc, rows, cols, xpixel, ypixel)
        assert _get_eof(fdesc) == b""

    return _pty_set_raw


def cbreak(rows=0, cols=0, xpixel=0, ypixel=0):
    "Return a function that sets PtyOptions.child_fd to cbreak mode."

    if Ellipsis in (rows, cols, xpixel, ypixel):
        rows, cols, xpixel, ypixel = _inherit_term_size(rows, cols, xpixel, ypixel)

    def _pty_set_cbreak(fdesc):
        tty.setcbreak(fdesc)
        if rows or cols or xpixel or ypixel:
            _set_term_size(fdesc, rows, cols, xpixel, ypixel)
        assert _get_eof(fdesc) == b""

    return _pty_set_cbreak


def canonical(rows=0, cols=0, xpixel=0, ypixel=0, echo=True):
    "Return a function that leaves PtyOptions.child_fd in canonical mode."

    if Ellipsis in (rows, cols, xpixel, ypixel):
        rows, cols, xpixel, ypixel = _inherit_term_size(rows, cols, xpixel, ypixel)

    def _pty_set_canonical(fdesc):
        if rows or cols or xpixel or ypixel:
            _set_term_size(fdesc, rows, cols, xpixel, ypixel)
        if not echo:
            _set_term_echo(fdesc, False)
        assert _get_eof(fdesc) == b"\x04"

    return _pty_set_canonical


def _set_term_echo(fdesc, echo):
    "Set pseudo-terminal echo."
    attrs = termios.tcgetattr(fdesc)
    curr_echo = (attrs[_LFLAG] & termios.ECHO) != 0
    if echo != curr_echo:
        if echo:
            # Set the ECHO bit.
            attrs[_LFLAG] = attrs[_LFLAG] | termios.ECHO
        else:
            # Clear the echo bit.
            attrs[_LFLAG] = attrs[_LFLAG] & ~termios.ECHO
        termios.tcsetattr(fdesc, termios.TCSADRAIN, attrs)


def _set_term_size(fdesc, rows, cols, xpixel, ypixel):
    "Set pseudo-terminal size."
    try:
        winsz = struct.pack("HHHH", rows, cols, xpixel, ypixel)
        fcntl.ioctl(fdesc, tty.TIOCSWINSZ, winsz)
    except OSError as ex:
        LOGGER.warning("_set_term_size ex=%r", ex)


def _inherit_term_size(rows, cols, xpixel, ypixel):
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
    if xpixel is Ellipsis:
        xpixel = winsz[2]
    if ypixel is Ellipsis:
        ypixel = winsz[3]

    return rows, cols, xpixel, ypixel


def _get_eof(fdesc):
    "Return the End-of-file character (EOF) if tty is in canonical mode only."

    eof = b""
    attrs = termios.tcgetattr(fdesc)
    if attrs[_LFLAG] & termios.ICANON:
        eof = attrs[_CC][termios.VEOF]

    return eof


def patch_child_watcher():
    "Patch the current child watcher to ignore the next `add_child_handler`."
    watcher = asyncio.get_child_watcher()
    saved_add_handler = watcher.add_child_handler

    def _add_child_handler(*_args):
        watcher.add_child_handler = saved_add_handler

    watcher.add_child_handler = _add_child_handler
