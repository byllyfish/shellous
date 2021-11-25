"""Implements DefaultChildWatcher.

Design Goals:
    1. Independent of any running event loop.
    2. Zero-cost until used.

References:
    - https://developer.apple.com/library/archive/technotes/tn2050/_index.html
    - https://chromium.googlesource.com/chromium/src/base/+/refs/heads/main/process/kill_mac.cc

"""

import asyncio
import os
import select
import signal
import socket
import sys
import threading

from shellous.log import LOGGER
from shellous.util import close_fds, wait_pid

assert sys.platform != "win32"

# Use a single module level lock for multi-threading.
_LOCK = threading.RLock()


def _check_sigchld():
    """Check that SIGCHLD is not set to SIG_IGN.

    kqueue/pidfd do not work if SIGCHLD is set to SIG_IGN in the
    signal table. On unix systems, the default action for SIGCHLD is to
    discard the signal so SIG_DFL is fine.
    """
    if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        raise RuntimeError("SIGCHLD cannot be set to SIG_IGN")


class DefaultChildWatcher(asyncio.AbstractChildWatcher):
    "Use platform-dependent mechanism to monitor for exiting child processes."

    def __init__(self):
        "Initialize child watcher."
        self._agent = None

    def add_child_handler(self, pid, callback, *args):
        """Register a new child handler.

        Arrange for callback(pid, returncode, *args) to be called when
        process 'pid' terminates. Specifying another callback for the same
        process replaces the previous handler.

        Note: callback() must be thread-safe.
        """
        assert self._agent, "Must use context manager API"
        self._agent.watch_pid(pid, callback, args)

    def remove_child_handler(self, pid):
        """Removes the handler for process 'pid'.

        The function returns True if the handler was successfully removed,
        False if there was nothing to remove.
        """
        return False  # not supported

    def attach_loop(self, loop):
        """Attach the watcher to an event loop.

        If the watcher was previously attached to an event loop, then it is
        first detached before attaching to the new loop.

        Note: loop may be None.
        """
        # no op

    def close(self):
        """Close the watcher.

        This must be called to make sure that any underlying resource is freed.
        """
        with _LOCK:
            agent = self._agent
            self._agent = None

        if agent:
            agent.close()

    def is_active(self):
        """Return ``True`` if the watcher is active and is used by the event loop.

        Return True if the watcher is installed and ready to handle process exit
        notifications.
        """
        return True

    def __enter__(self):
        """Enter the watcher's context and allow starting new processes."""
        with _LOCK:
            if not self._agent:
                self._init_agent()

        return self

    def __exit__(self, *_args):
        """Exit the watcher's context"""
        # no op

    def _init_agent(self):
        "Construct child watcher agent."
        if sys.platform == "linux":
            self._agent = EPollAgent()
        else:
            self._agent = KQueueAgent()


class KQueueAgent:
    "Agent that watches for child exit kqueue event."

    # pylint: disable=no-member

    def __init__(self):
        "Initialize agent variables."
        _check_sigchld()
        self._pids = {}  # pid -> (callback, args)
        self._kqueue = select.kqueue()
        self._thread = _start_thread(self._run, name="KQueueAgent")

    def close(self):
        "Tell our thread to exit with a dummy timer event."
        self._add_kevent(
            1,
            select.KQ_FILTER_TIMER,
            select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
        )
        self._thread.join()

    def watch_pid(self, pid, callback, args):
        "Register a PID with kqueue."
        with _LOCK:
            self._pids[pid] = (callback, args)

        try:
            self._add_kevent(
                pid,
                select.KQ_FILTER_PROC,
                select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                select.KQ_NOTE_EXIT,
            )
            LOGGER.debug("_add_kevent pid=%r", pid)
        except ProcessLookupError:
            self._kevent_failed(pid)

    def _kevent_failed(self, pid):
        "Handle case where an exiting process is no longer kqueue-able."
        with _LOCK:
            callback, args = self._pids.pop(pid)

        LOGGER.debug("_kevent_failed pid=%r", pid)

        status = wait_pid(pid)
        if status is not None:
            callback(pid, status, *args)
        else:
            # Process is still dying. Spawn a task to poll it.
            asyncio.create_task(_poll_dead_pid(pid, callback, args))

    def _run(self):
        "Event loop that handles kqueue events."
        try:
            while True:
                pending = self._kqueue.control(None, 10, 10)
                for event in pending:
                    if event.filter == select.KQ_FILTER_TIMER:
                        # Dummy timer event tells thread to exit.
                        return
                    if event.filter == select.KQ_FILTER_PROC:
                        self._reap_pid(event.ident)

        finally:
            self._kqueue.close()

    def _reap_pid(self, pid):
        """Called by event loop when a process exits."""
        with _LOCK:
            callback, args = self._pids.pop(pid)

        LOGGER.debug("_reap_pid pid=%r", pid)

        status = wait_pid(pid)
        if status is not None:
            # Invoke callback function.
            callback(pid, status, *args)
        else:
            # Process is still running.
            LOGGER.critical("_reap_pid: process still running pid=%r", pid)

    def _add_kevent(self, ident, filter, flags, fflags=0):
        "Add specified kevent to kqueue."
        event = select.kevent(ident, filter, flags, fflags)
        self._kqueue.control([event], 0)


class EPollAgent:
    "Agent that watches for child exit epoll event."

    # pylint: disable=no-member

    def __init__(self):
        "Initialize agent variables."
        _check_sigchld()
        self._pids = {}  # pid -> (callback, args)
        self._pidfds = {}  # pidfd -> pid
        self._epoll = select.epoll()
        self._selfpipe = os.pipe()
        self._epoll.register(self._selfpipe[0], select.EPOLLIN)
        self._thread = _start_thread(self._run, name="EPollAgent")

    def watch_pid(self, pid, callback, args):
        "Register a PID with epoll."
        with _LOCK:
            self._pids[pid] = (callback, args)

        try:
            self._add_pidfd(pid)
        except ProcessLookupError:
            self._pidfd_failed(pid)

    def _pidfd_failed(self, pid):
        "Handle case where pidfd_open fails."
        with _LOCK:
            callback, args = self._pids.pop(pid)

        LOGGER.debug("_pidfd_failed pid=%r", pid)

        status = wait_pid(pid)
        if status is not None:
            callback(pid, status, *args)
        else:
            # Process is still dying. Spawn a task to poll it.
            asyncio.create_task(_poll_dead_pid(pid, callback, args))

    def close(self):
        "Tell the epoll thread to exit."
        if self._selfpipe is not None:
            os.write(self._selfpipe[1], b"\x00")
        self._thread.join()

    def _run(self):
        "Event loop that handles epoll events."
        try:
            pipe_fd = self._selfpipe[0]

            while True:
                pending = self._epoll.poll()
                for pidfd, _events in pending:
                    if pidfd == pipe_fd:
                        return  # all done!
                    self._reap_pidfd(pidfd)
                    self._remove_pidfd(pidfd)

        finally:
            self._epoll.close()
            with _LOCK:
                pidfds = list(self._pidfds.keys())
            close_fds(pidfds)
            close_fds(self._selfpipe)
            self._selfpipe = None

    def _reap_pidfd(self, pidfd):
        "Handle epoll pidfd event."
        with _LOCK:
            pid = self._pidfds.pop(pidfd)
            callback, args = self._pids.pop(pid)

        LOGGER.debug("_reap_pidfd pidfd=%r pid=%r", pidfd, pid)

        status = wait_pid(pid)
        if status is not None:
            callback(pid, status, *args)
        else:
            LOGGER.critical("_reap_pid: process still running pid=%r", pid)

    def _add_pidfd(self, pid):
        "Add epoll that monitors for process exit."
        pidfd = os.pidfd_open(pid, 0)
        with _LOCK:
            self._pidfds[pidfd] = pid
        self._epoll.register(pidfd, select.EPOLLIN)
        LOGGER.debug("_add_pidfd registered pidfd=%r pid=%r", pidfd, pid)

    def _remove_pidfd(self, pidfd):
        "Remove epoll that monitors for process exit."
        self._epoll.unregister(pidfd)
        os.close(pidfd)


async def _poll_dead_pid(pid, callback, args):
    """Poll a pid that we expect to exit and be reap-able very soon."""
    for timeout in (0.001, 0.01, 0.1, 1.0, 2.0):
        await asyncio.sleep(timeout)
        status = wait_pid(pid)
        if status is not None:
            callback(pid, status, *args)
            break
    else:
        # Handle case where process is *still* running after 3.111 seconds.
        LOGGER.critical("Pid %r is not exiting after several seconds.", pid)


def _start_thread(target, *, name):
    "Create a new thread and start it."

    def _runner():
        try:
            LOGGER.debug("Thread %r starting", name)
            target()
        except BaseException as ex:  # pylint: disable=broad-except
            LOGGER.error("Thread %r failed ex=%r", name, ex, exc_info=True)
            raise
        finally:
            LOGGER.debug("Thread %r stopping", name)

    thread = threading.Thread(target=_runner, name=name, daemon=True)
    thread.start()
    return thread
