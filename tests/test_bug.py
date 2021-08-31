import asyncio
import os
import signal
import sys

import pytest

pytestmark = pytest.mark.asyncio

PIPE_MAX_SIZE = 4 * 1024 * 1024 + 1


async def _kill(pid, timeout):
    await asyncio.sleep(timeout)
    os.kill(pid, signal.SIGTERM)


async def test_bug():
    # Write a ton of data to "sleep". Kill the command.

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdin=asyncio.subprocess.PIPE,
    )

    # Create a task to kill process in 2 seconds.
    task = asyncio.create_task(_kill(proc.pid, 2))

    data = b"a" * PIPE_MAX_SIZE
    proc.stdin.write(data)
    try:
        await proc.stdin.drain()
    except BrokenPipeError:
        print("BrokenPipe 1")

    proc.stdin.close()
    try:
        # Check if wait_closed() hangs...
        await proc.stdin.wait_closed()
    except BrokenPipeError:
        print("BrokenPipe 2")

    await task
