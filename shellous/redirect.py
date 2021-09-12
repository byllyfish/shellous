"Implements the Redirect enum and various redirection utilities."

import asyncio
import enum
import io
import os

from shellous.log import LOGGER, log_method
from shellous.util import decode

_DETAILED_LOGGING = True


class Redirect(enum.IntEnum):
    "Redirection constants."

    STDOUT = asyncio.subprocess.STDOUT  # -2
    DEVNULL = asyncio.subprocess.DEVNULL  # -3
    CAPTURE = -10
    INHERIT = -11

    def is_custom(self):
        "Return true if this redirect option is not built into asyncio."
        return self in {Redirect.CAPTURE, Redirect.INHERIT}


# Used in Command and Pipeline to implement operator overloading.
STDIN_TYPES = (str, bytes, os.PathLike, bytearray, io.IOBase, int, Redirect)
STDOUT_TYPES = (str, bytes, os.PathLike, bytearray, io.IOBase, int, Redirect)
STDOUT_APPEND_TYPES = (str, bytes, os.PathLike)


@log_method(_DETAILED_LOGGING)
async def write_stream(input_bytes, stream, eof=None):
    "Write input_bytes to stream."
    try:
        if input_bytes:
            stream.write(input_bytes)
            await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        # Catch these errors and quietly stop.
        # See "_feed_stdin" in /3.9/Lib/asyncio/subprocess.py
        pass

    # If `stream.drain()`` is cancelled, we do NOT close the stream here.
    # See https://bugs.python.org/issue45074

    if eof is not None:
        if eof:
            # When using a pty in canonical mode, send the EOF character instead
            # of closing the pty. At the beginning of a line, we only need to
            # send one EOF. Otherwise, we need to send one EOF to end the
            # partial line and then another EOF to signal we are done.
            if input_bytes and input_bytes[-1] != "\n":
                stream.write(eof + eof)
            else:
                stream.write(eof)
            await stream.drain()
    else:
        stream.close()


@log_method(_DETAILED_LOGGING)
async def copy_stringio(source, dest, encoding):
    "Copy bytes from source stream to dest StringIO."
    # Collect partial reads into a BytesIO.
    buf = io.BytesIO()
    try:
        while True:
            data = await source.read(1024)
            if not data:
                break
            buf.write(data)
    finally:
        # Only convert to string once all output is collected.
        # (What if utf-8 codepoint is split between reads?)
        dest.write(decode(buf.getvalue(), encoding))


@log_method(_DETAILED_LOGGING)
async def copy_bytesio(source, dest):
    "Copy bytes from source stream to dest BytesIO."
    # Collect partial reads into a BytesIO.
    while True:
        data = await source.read(1024)
        if not data:
            break
        dest.write(data)


@log_method(_DETAILED_LOGGING)
async def copy_bytearray(source, dest):
    "Copy bytes from source stream to dest bytearray."
    # Collect partial reads into a bytearray.
    while True:
        data = await source.read(1024)
        if not data:
            break
        LOGGER.debug("copy_bytearray read=%r", data)
        dest.extend(data)
