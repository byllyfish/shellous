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


@pytest.mark.xfail(sys.platform == "win32", reason="latent bug")
async def test_bug():
    # t=0: Start the process and begin writing PIPE_MAX_SIZE bytes.
    # t=1: Cancel drain() and close stdin.
    # t=2: Kill the process. Causes "Fatal write error on pipe transport"
    # t=3: Call stdin.wait_closed(). This hangs on Windows.

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdin=asyncio.subprocess.PIPE,
    )

    # Start task to kill process in 2 seconds.
    task = asyncio.create_task(_kill(proc.pid, 2))

    data = b"a" * PIPE_MAX_SIZE
    proc.stdin.write(data)
    try:
        await asyncio.wait_for(proc.stdin.drain(), 1.0)
    except asyncio.TimeoutError:
        pass
    finally:
        proc.stdin.close()

    await asyncio.sleep(2)

    try:
        # wait_closed() hangs here on Windows... and triggers a TimeoutError.
        await asyncio.wait_for(proc.stdin.wait_closed(), 5)
    except BrokenPipeError:
        print("BrokenPipe")

    await task
