"Implements the Redirect enum and various redirection utilities."

import asyncio
import enum
import io
from logging import Logger
from pathlib import Path
from typing import Any, Optional, Union

from shellous.log import LOG_DETAIL, log_method
from shellous.pty_util import PtyAdapterOrBool
from shellous.util import decode

_CHUNK_SIZE = 8192
_STDIN = 0
_STDOUT = 1
_STDERR = 2


class Redirect(enum.IntEnum):
    "Redirection constants."

    STDOUT = asyncio.subprocess.STDOUT  # -2
    DEVNULL = asyncio.subprocess.DEVNULL  # -3
    CAPTURE = -10
    INHERIT = -11
    BUFFER = -12
    DEFAULT = -20

    def is_custom(self):
        "Return true if this redirect option is not built into asyncio."
        return self in {
            Redirect.CAPTURE,
            Redirect.INHERIT,
            Redirect.BUFFER,
            Redirect.DEFAULT,
        }

    @staticmethod
    def from_default(obj: Any, fdesc: int, pty: PtyAdapterOrBool):
        "Return object with Redirect.DEFAULT replaced by actual value."
        if not isinstance(obj, Redirect) or obj != Redirect.DEFAULT:
            return obj

        assert obj == Redirect.DEFAULT
        return _DEFAULT_REDIRECTION[(fdesc, bool(pty))]


# This table has the default redirections for (src, pty).
# Sources are stdin, stdout, stderr. Used by Redirect.from_default().
_DEFAULT_REDIRECTION: dict[tuple[int, bool], Union[bytes, Redirect]] = {
    # (FD, PTY)
    (_STDIN, False): b"",
    (_STDIN, True): Redirect.CAPTURE,
    (_STDOUT, False): Redirect.BUFFER,
    (_STDOUT, True): Redirect.BUFFER,
    (_STDERR, False): Redirect.BUFFER,
    (_STDERR, True): Redirect.STDOUT,
}

# Used in Command and Pipeline to implement operator overloading.
STDIN_TYPES = (
    str,
    bytes,
    Path,
    bytearray,
    io.IOBase,
    int,
    Redirect,
    asyncio.StreamReader,
)

STDIN_TYPES_T = Union[
    str,
    bytes,
    Path,
    bytearray,
    io.IOBase,
    int,
    Redirect,
    asyncio.StreamReader,
]

STDOUT_TYPES = (
    Path,
    bytearray,
    io.IOBase,
    int,
    Redirect,
    Logger,
    asyncio.StreamWriter,
)

STDOUT_TYPES_T = Union[
    Path,
    bytearray,
    io.IOBase,
    int,
    Redirect,
    Logger,
    asyncio.StreamWriter,
]


async def _drain(stream: asyncio.StreamWriter):
    "Safe drain method."
    try:
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        # Catch these errors and quietly stop.
        # See "_feed_stdin" in /3.9/Lib/asyncio/subprocess.py
        pass


@log_method(LOG_DETAIL)
async def write_stream(
    input_bytes: bytes,
    stream: asyncio.StreamWriter,
    eof: Optional[bytes] = None,
):
    "Write input_bytes to stream."
    if input_bytes:
        # Check for stream that is already closing before we've written
        # anything. Proactively raise a BrokenPipeError here. (issue #45)
        if stream.is_closing():
            raise BrokenPipeError()

        stream.write(input_bytes)
        await _drain(stream)

    # If `stream.drain()`` is cancelled, we do NOT close the stream here.
    # See https://bugs.python.org/issue45074

    if eof is None:
        # Close the stream when we are done.
        stream.close()

    elif eof:
        # When using a pty in canonical mode, send the EOF character instead
        # of closing the pty. At the beginning of a line, we only need to
        # send one EOF. Otherwise, we need to send one EOF to end the
        # partial line and then another EOF to signal we are done.
        if not input_bytes.endswith(b"\n"):
            stream.write(eof + eof)
        else:
            stream.write(eof)
        await _drain(stream)


@log_method(LOG_DETAIL)
async def write_reader(
    reader: asyncio.StreamReader,
    stream: asyncio.StreamWriter,
    eof: Optional[bytes] = None,
):
    "Copy from reader to writer."
    ends_with_newline = True
    try:
        while True:
            data = await reader.read(_CHUNK_SIZE)
            if not data:
                break
            ends_with_newline = data.endswith(b"\n")
            stream.write(data)
            await stream.drain()

    except (BrokenPipeError, ConnectionResetError):
        pass

    if eof is None:
        stream.close()
    elif eof:
        if not ends_with_newline:
            stream.write(eof + eof)
        else:
            stream.write(eof)
        await _drain(stream)


@log_method(LOG_DETAIL)
async def copy_stringio(
    source: asyncio.StreamReader,
    dest: io.StringIO,
    encoding: str,
):
    "Copy bytes from source stream to dest StringIO."
    # Collect partial reads into a BytesIO.
    buf = io.BytesIO()
    try:
        while True:
            data = await source.read(_CHUNK_SIZE)
            if not data:
                break
            buf.write(data)
    finally:
        # Only convert to string once all output is collected.
        # (What if utf-8 codepoint is split between reads?)
        dest.write(decode(buf.getvalue(), encoding))


@log_method(LOG_DETAIL)
async def copy_logger(
    source: asyncio.StreamReader,
    dest: Logger,
    encoding: str,
):
    "Copy lines from source stream to dest Logger."
    async for line in source:
        data = decode(line, encoding)
        dest.error(data.rstrip())


@log_method(LOG_DETAIL)
async def copy_bytesio(source: asyncio.StreamReader, dest: io.BytesIO):
    "Copy bytes from source stream to dest BytesIO."
    # Collect partial reads into a BytesIO.
    while True:
        data = await source.read(_CHUNK_SIZE)
        if not data:
            break
        dest.write(data)


@log_method(LOG_DETAIL)
async def copy_bytearray(source: asyncio.StreamReader, dest: bytearray):
    "Copy bytes from source stream to dest bytearray."
    # Collect partial reads into a bytearray.
    while True:
        data = await source.read(_CHUNK_SIZE)
        if not data:
            break
        dest.extend(data)


@log_method(LOG_DETAIL)
async def copy_bytearray_limit(
    source: asyncio.StreamReader,
    dest: bytearray,
    limit: int,
):
    "Copy limited number of bytes from source stream to dest bytearray."
    while True:
        data = await source.read(_CHUNK_SIZE)
        if not data:
            break
        dest.extend(data)
        if len(dest) > limit:
            del dest[limit:]
            break

    if data:
        # After reaching the limit, continue to read and discard bytes from
        # source to avoid possible blocking/deadlock inside the source program.
        while await source.read(_CHUNK_SIZE):
            pass


@log_method(LOG_DETAIL)
async def copy_streamwriter(source: asyncio.StreamReader, dest: asyncio.StreamWriter):
    "Copy bytes from source stream to dest StreamWriter."
    while True:
        data = await source.read(_CHUNK_SIZE)
        if not data:
            break
        dest.write(data)
        await dest.drain()

    dest.close()
    await dest.wait_closed()


@log_method(LOG_DETAIL)
async def read_lines(source: asyncio.StreamReader, encoding: str):
    "Async iterator over lines in stream."
    async for line in source:
        yield decode(line, encoding)
