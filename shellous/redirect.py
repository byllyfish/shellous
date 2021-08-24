"Implements the Redirect enum and various redirection utilities."

import asyncio
import enum
import functools
import io

from shellous.log import LOGGER
from shellous.util import decode


class Redirect(enum.IntEnum):
    "Redirection constants."

    STDOUT = asyncio.subprocess.STDOUT  # -2
    DEVNULL = asyncio.subprocess.DEVNULL  # -3
    CAPTURE = -10
    INHERIT = -11

    def is_custom(self):
        "Return true if this redirect option is not built into asyncio."
        return self in {Redirect.CAPTURE, Redirect.INHERIT}


def _log_exception(func):
    @functools.wraps(func)
    async def _wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            LOGGER.info("Task %r cancelled!", func)
            raise
        except Exception as ex:
            LOGGER.warning("Task %r ex=%r", func, ex)
            raise

    return _wrapper


@_log_exception
async def write_stream(input_bytes, stream):
    if input_bytes:
        try:
            stream.write(input_bytes)
            await stream.drain()
        except asyncio.CancelledError:
            LOGGER.info("_feed_writer cancelled!")
            pass
        except (BrokenPipeError, ConnectionResetError) as ex:
            LOGGER.info("_feed_writer ex=%r", ex)
            pass
    stream.close()


@_log_exception
async def copy_stringio(source, dest, encoding):
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


@_log_exception
async def copy_bytesio(source, dest):
    # Collect partial reads into a BytesIO.
    while True:
        data = await source.read(1024)
        if not data:
            break
        dest.write(data)


@_log_exception
async def copy_bytearray(source, dest):
    # Collect partial reads into a bytearray.
    while True:
        data = await source.read(1024)
        if not data:
            break
        dest.extend(data)
