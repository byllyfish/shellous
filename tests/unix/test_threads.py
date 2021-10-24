"Unit tests that involve daemon threads."

import asyncio
import functools
import sys
import threading
import time

import pytest
import shellous

unix_only = pytest.mark.skipif(sys.platform == "win32", reason="Unix")
pytestmark = [pytest.mark.asyncio, unix_only]


class EventLoopThread(threading.Thread):
    "Thread with its own asyncio event loop."

    def __init__(self):
        super().__init__(daemon=True)
        self._lock = threading.Lock()  # Guards _loop
        self._loop = None
        self._shutdown_fut = None

    @property
    def loop(self):
        result = None
        for _ in range(3):
            with self._lock:
                result = self._loop
            if result:
                break
            time.sleep(0.1)
        return result

    def future(self, coro):
        "Run coroutine in separate thread. Returns concurrent.Future."
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self):
        "Override Thread.run()."
        asyncio.run(self._wait())

    def stop(self):
        "Called from another thread to stop this one."
        self.loop.call_soon_threadsafe(self._stop)
        self.join()

    async def _wait(self):
        "Wait for shutdown future to be triggered."
        loop = asyncio.get_running_loop()
        self._shutdown_fut = loop.create_future()

        with self._lock:
            self._loop = loop

        await self._shutdown_fut

    def _stop(self):
        "Trigger shutdown future."
        if not self._shutdown_fut.done():
            self._shutdown_fut.set_result(1)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_args):
        self.stop()


def run_in_thread(child_watcher_name="ThreadedChildWatcher"):
    "Decorator to run async function in a daemon thread with private event loop."

    def _decorator(coro):
        @functools.wraps(coro)
        def _wrap(*args, **kwargs):
            child_watcher = getattr(asyncio, child_watcher_name)()
            asyncio.set_child_watcher(child_watcher)

            # MultiLoopChildWatcher: call attach_loop to prepare for action.
            if child_watcher_name == "MultiLoopChildWatcher":
                child_watcher.attach_loop(None)

            with EventLoopThread() as thread:
                fut = thread.future(coro(*args, **kwargs))
                fut.result()

        return _wrap

    return _decorator


@run_in_thread("MultiLoopChildWatcher")
async def test_thread_echo():
    "Test echo in another thread."
    sh = shellous.context()
    result = await sh("echo", "abc")
    assert result == "abc\n"


@run_in_thread("MultiLoopChildWatcher")
async def test_thread_pipe():
    "Test pipe in another thread."
    sh = shellous.context()
    result = await (sh("echo", "abc") | sh("cat"))
    assert result == "abc\n"


@run_in_thread("MultiLoopChildWatcher")
async def test_thread_procsub():
    "Test process substituion in another thread."
    sh = shellous.context()
    result = await sh("cat", sh("echo", "abc"), sh("echo", "def"))
    assert result == "abc\ndef\n"


@run_in_thread("MultiLoopChildWatcher")
async def test_thread_pty():
    "Test pty in another thread."
    sh = shellous.context()
    result = await sh("ls", "README.md").set(pty=True)
    assert result == "README.md\r\n"
