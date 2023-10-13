"Implements support for pseudo-terminals."

import asyncio
import contextlib
import contextvars
import errno
import os
import struct
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple, Optional, Union

# The following modules are not supported on Windows.
try:
    import fcntl
    import pty
    import termios
    import tty
except ImportError:  # pragma: no cover
    pass

from .log import LOG_DETAIL, LOGGER, log_method
from .util import BSD_DERIVED, BSD_FREEBSD, close_fds

_STDOUT_FILENO = 1
_LFLAG = 3
_CC = 6

PtyAdapter = Callable[[int], None]
PtyAdapterOrBool = Union[PtyAdapter, bool]


@dataclass
class ChildFd:
    "Make sure child fd is only closed once."
    child_fd: int

    def __int__(self) -> int:
        return self.child_fd

    def close(self) -> None:
        "Close the child file descriptor exactly once."
        if self.child_fd >= 0:
            fdesc = self.child_fd
            self.child_fd = -1
            close_fds([fdesc])


class PtyFds(NamedTuple):
    "Track parent fd for pty."
    parent_fd: int
    child_fd: ChildFd
    eof: bytes
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None

    @log_method(LOG_DETAIL)
    async def open_streams(self) -> "PtyFds":
        "Open pty reader/writer streams."
        reader, writer = await _open_pty_streams(self.parent_fd, self.child_fd)
        return PtyFds(
            self.parent_fd,
            self.child_fd,
            self.eof,
            reader,
            writer,
        )

    def close(self) -> None:
        "Close pty file descriptors."
        if LOG_DETAIL:
            LOGGER.debug("PtyFds.close")
        self.child_fd.close()
        if self.writer:
            self.writer.close()
        else:
            close_fds([self.parent_fd])


def open_pty(pty_func: PtyAdapterOrBool) -> PtyFds:
    "Open pseudo-terminal (pty) descriptors. Returns (PtyFds, child_fd)."
    parent_fd, child_fd = pty.openpty()

    # If pty_func is a callable, call it here with `child_fd` as argument. This
    # gives the client an opportunity to configure the tty.
    if callable(pty_func):
        pty_func(child_fd)

    return PtyFds(
        parent_fd,
        ChildFd(child_fd),
        _get_eof(child_fd),
    )


def set_ctty(ttypath: str) -> None:
    "Explicitly open the tty to make it become a controlling tty."
    # See https://github.com/python/cpython/blob/3.9/Lib/pty.py

    # Don't use ioctl TIOCSTTY; it doesn't appear to work on FreeBSD.
    tmpfd = os.open(ttypath, os.O_RDWR)
    os.close(tmpfd)


class PtyStreamReaderProtocol(asyncio.StreamReaderProtocol):
    "Custom subclass of StreamReaderProtocol for pty's."

    pty__child_fd: Optional[ChildFd] = None
    pty__timer: Optional[asyncio.TimerHandle] = None  # Issue #378

    def connection_lost(self, exc: Optional[Exception]) -> None:
        "Intercept EIO error and treat it as EOF."
        if LOG_DETAIL:
            LOGGER.debug("PtyStreamReaderProtocol.connection_lost ex=%r", exc)
        if BSD_DERIVED and self.pty__timer is not None:
            self.pty__timer.cancel()
        if isinstance(exc, OSError) and exc.errno == errno.EIO:
            exc = None
        super().connection_lost(exc)

    if BSD_DERIVED:
        # BSD only: On Linux, use the default behavior.

        def _close_child_fd(self):
            if self.pty__child_fd:
                self.pty__child_fd.close()
                self.pty__child_fd = None

        if BSD_FREEBSD:
            # Timer is only necessary on FreeBSD, not MacOS.
            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                "Set up timer to close child fd when no data is received."
                super().connection_made(transport)
                if LOG_DETAIL:
                    LOGGER.debug("PtyStreamReaderProtocol.connection_made")
                self.pty__timer = self._loop.call_later(2.0, self._close_child_fd)  # type: ignore

        def data_received(self, data: bytes) -> None:
            "Close child_fd when first data received."
            self._close_child_fd()
            return super().data_received(data)

    def eof_received(self) -> Optional[bool]:
        "Log when EOF received."
        if LOG_DETAIL:
            LOGGER.debug("PtyStreamReaderProtocol.eof_received")
        return super().eof_received()


async def _open_pty_streams(parent_fd: int, child_fd: ChildFd):
    "Open reader, writer streams for pty file descriptor."
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(loop=loop)
    reader_protocol = PtyStreamReaderProtocol(reader, loop=loop)

    # Stick reference to child_fd into protocol so we can close it after the
    # first data is received on BSD systems.
    reader_protocol.pty__child_fd = child_fd

    reader_transport, _ = await loop.connect_read_pipe(
        lambda: reader_protocol,
        os.fdopen(parent_fd, "rb", 0, closefd=False),
    )

    writer_transport, writer_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin,
        os.fdopen(parent_fd, "wb", 0, closefd=True),
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

    # Patch writer_transport.close so it also closes the reader_transport.
    def _close():
        if LOG_DETAIL:
            LOGGER.debug("writer_transport.close()")
        _orig_close()
        reader_transport.close()

    writer_transport.close, _orig_close = _close, writer_transport.close

    return reader, writer


IntOrEllipsis = Union[int, type(...)]


def raw(rows: IntOrEllipsis = 0, cols: IntOrEllipsis = 0) -> PtyAdapter:
    "Return a function that sets PtyOptions.child_fd to raw mode."
    rows_int, cols_int = _inherit_term_size(rows, cols)

    def _pty_set_raw(fdesc: int):
        tty.setraw(fdesc)
        if rows_int or cols_int:
            _set_term_size(fdesc, rows_int, cols_int)
        assert _get_eof(fdesc) == b""

    return _pty_set_raw


def cbreak(rows: IntOrEllipsis = 0, cols: IntOrEllipsis = 0) -> PtyAdapter:
    "Return a function that sets PtyOptions.child_fd to cbreak mode."
    rows_int, cols_int = _inherit_term_size(rows, cols)

    def _pty_set_cbreak(fdesc: int):
        tty.setcbreak(fdesc)
        if rows_int or cols_int:
            _set_term_size(fdesc, rows_int, cols_int)
        assert _get_eof(fdesc) == b""

    return _pty_set_cbreak


def cooked(
    rows: IntOrEllipsis = 0, cols: IntOrEllipsis = 0, echo: bool = True
) -> PtyAdapter:
    "Return a function that leaves PtyOptions.child_fd in cooked mode."
    rows_int, cols_int = _inherit_term_size(rows, cols)

    def _pty_set_canonical(fdesc: int):
        if rows_int or cols_int:
            _set_term_size(fdesc, rows_int, cols_int)
        if not echo:
            set_term_echo(fdesc, False)
        assert _get_eof(fdesc) == b"\x04"

    return _pty_set_canonical


def get_term_echo(fdesc: int) -> bool:
    "Return true if terminal driver is in echo mode."
    attrs = termios.tcgetattr(fdesc)
    return (attrs[_LFLAG] & termios.ECHO) != 0


def set_term_echo(fdesc: int, echo: bool) -> None:
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


def _set_term_size(fdesc: int, rows: int, cols: int):
    "Set pseudo-terminal size."
    try:
        winsz = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fdesc, tty.TIOCSWINSZ, winsz)  # pyright: ignore
    except OSError as ex:
        LOGGER.warning("_set_term_size ex=%r", ex)


def _inherit_term_size(rows: IntOrEllipsis, cols: IntOrEllipsis) -> tuple[int, int]:
    "Override ... with terminal setting from current stdin."
    if Ellipsis not in (rows, cols):
        return rows, cols  # type: ignore

    try:
        zeros = struct.pack("HHHH", 0, 0, 0, 0)
        winsz = fcntl.ioctl(_STDOUT_FILENO, tty.TIOCGWINSZ, zeros)  # pyright: ignore
        winsz = struct.unpack("HHHH", winsz)
    except (OSError, struct.error) as ex:
        winsz = (0, 0, 0, 0)
        LOGGER.warning("_inherit_term_size ex=%r", ex)

    if rows is Ellipsis:
        rows = winsz[0]
    if cols is Ellipsis:
        cols = winsz[1]

    return rows, cols  # type: ignore


def _get_eof(fdesc: int):
    "Return the End-of-file character (EOF) if tty is in canonical mode only."
    eof = b""
    attrs = termios.tcgetattr(fdesc)
    if attrs[_LFLAG] & termios.ICANON:
        eof = attrs[_CC][termios.VEOF]

    return eof


# This ContextVar indicates that the child watcher should ignore the next
# call to `add_child_handler``.
_IGNORE_CHILD_PROCESS = contextvars.ContextVar("ignore_child_process", default=False)


def _patch_child_watcher():
    "Patch the current child watcher for `add_child_handler`."
    watcher = asyncio.get_child_watcher()

    # Check flag to see if patch already exists.
    if hasattr(watcher, "_shellous_patched"):
        return
    setattr(watcher, "_shellous_patched", True)

    saved_add_handler = watcher.add_child_handler

    def _add_child_handler(pid: int, callback: Callable[..., object], *args: Any):
        if not _IGNORE_CHILD_PROCESS.get():
            saved_add_handler(pid, callback, *args)

    watcher.add_child_handler = _add_child_handler


@contextlib.contextmanager
def set_ignore_child_watcher(ignore: bool):
    "Tell the current child watcher to ignore the next `add_child_handler`."
    if ignore:
        _patch_child_watcher()

    _IGNORE_CHILD_PROCESS.set(ignore)
    try:
        yield
    finally:
        _IGNORE_CHILD_PROCESS.set(False)
