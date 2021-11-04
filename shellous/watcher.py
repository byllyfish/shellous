"Implements DefaultChildWatcher."

import asyncio
import queue
import select
import signal
import socket
import sys
import threading

from shellous.log import LOGGER
from shellous.util import wait_pid

assert sys.platform != "win32"

if sys.platform == "darwin":
    _KQ_EV_RECEIPT = 0x0040
else:
    _KQ_EV_RECEIPT = 0


class DefaultChildWatcher(asyncio.AbstractChildWatcher):
    """Uses kqueue to monitor for exiting child processes.

    Design Goals:
      1. Independent of any running event loop.
      2. Zero-cost until used.

    Cost: 3 file descriptors and 1 thread.

    A DefaultChildWatcher must be created on the main thread.
    """

    def __init__(self):
        self._lock = threading.Lock()  # guard `self`
        self._worker = None

        # Install dummy SIGCHLD signal handler to make EV_FILTER_SIGNAL work.
        signal.signal(signal.SIGCHLD, lambda *args: None)

    def add_child_handler(self, pid, callback, *args):
        """Register a new child handler.

        Arrange for callback(pid, returncode, *args) to be called when
        process 'pid' terminates. Specifying another callback for the same
        process replaces the previous handler.

        Note: callback() must be thread-safe.
        """
        assert self._worker, "Must use context manager API"
        self._worker.watch_pid(pid, callback, args)

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

    def close(self):
        """Close the watcher.

        This must be called to make sure that any underlying resource is freed.
        """
        with self._lock:
            worker = self._worker
            self._worker = None

        if worker:
            worker.close()

    def is_active(self):
        """Return ``True`` if the watcher is active and is used by the event loop.

        Return True if the watcher is installed and ready to handle process exit
        notifications.
        """
        return True

    def __enter__(self):
        """Enter the watcher's context and allow starting new processes."""
        with self._lock:
            if not self._worker:
                self._worker = KQueueWorker()

        return self

    def __exit__(self, a, b, c):
        """Exit the watcher's context"""


class KQueueWorker(threading.Thread):
    """A Thread that uses kqueue to monitor for child processes exiting.

    We use a fallback mechanism on macOS because sometimes, for unknown reasons,
    macOS refuses to register a KQ_FILTER_PROC/KQ_NOTE_EXIT event for a known
    child pid even though the child process is *still running*.

    Our fallback strategy relies on polling the "fallback_pids" in a kqueue
    handler that catches SIGCHLD.

    References:
      - https://developer.apple.com/library/archive/technotes/tn2050/_index.html

    """

    # pylint: disable=no-member

    def __init__(self):
        "Initialize worker variables and start thread."
        super().__init__(daemon=True)
        self._work_queue = queue.Queue()
        self._client_sock, self._server_sock = self._make_self_pipe()
        self._server_pids = {}
        self._fallback_pids = set()
        self._kqueue = None
        self._running = True
        self.start()

    def watch_pid(self, pid, callback, args):
        "Add pid exit callback information to queue and wake up thread."
        LOGGER.debug("watch_pid %r", pid)
        self._work_queue.put_nowait((pid, callback, args))
        self._client_sock.send(b"0")

    def close(self):
        "Tell worker thread to stop."
        self.watch_pid(None, None, None)
        self.join()

    def run(self):
        "Override Thread.run()."
        LOGGER.debug("KQWorker starting %r", self)
        self._kqueue = select.kqueue()
        try:
            self._event_loop()
        except BaseException as ex:  # pylint: disable=broad-except
            LOGGER.error("KQWorker failed %s ex=%r", self, ex, exc_info=True)
            raise
        finally:
            self._kqueue.close()
            self._server_sock.close()
            self._client_sock.close()
            LOGGER.debug("KQWorker stopping %r", self)

    def _event_loop(self):
        "Event loop that handles kqueue events."
        self._add_read_event(self._server_sock.fileno())
        self._add_signal_event(signal.SIGCHLD)

        event_handlers = {
            select.KQ_FILTER_PROC: self._handle_proc,
            select.KQ_FILTER_READ: self._handle_read,
            select.KQ_FILTER_SIGNAL: self._handle_signal,
        }

        while self._running:
            pending = self._kqueue.control(None, 10, 10)
            for event in pending:
                handler = event_handlers.get(event.filter)
                if handler:
                    handler(event)
                else:
                    LOGGER.debug("_event_loop: unknown kevent: %r", event)

    def _handle_proc(self, event):
        "Handle KQ_FILTER_PROC event."
        self._check_pid(event.ident)

    def _handle_read(self, _event):
        "Handle KQ_FILTER_READ event."
        self._drain_server_sock()
        self._check_queue()

    def _handle_signal(self, event):
        "Handle KQ_FILTER_SIGNAL event."
        for pid in list(self._fallback_pids):
            if self._check_pid(pid):
                self._fallback_pids.remove(pid)

    def _check_pid(self, pid):
        """Called when a process exits."""
        info = self._server_pids.get(pid, None)
        if info:
            callback, args = info
            status = wait_pid(pid)
            if status is None:
                # Process is still running.
                LOGGER.debug("_check_pid: process still running pid=%r", pid)
            else:
                # Invoke callback function here.
                callback(pid, status, *args)
                self._server_pids.pop(pid)
                return True
        else:
            LOGGER.error("_check_pid: unregistered pid: %r", pid)

        return False

    def _drain_server_sock(self):
        "Ref: asyncio/selector_events.py#L117."
        while True:
            try:
                if not self._server_sock.recv(4096):
                    break
            except InterruptedError:
                continue
            except BlockingIOError:
                break

    def _check_queue(self):
        "Check the work queue for new work requests."
        try:
            while True:
                pid, callback, args = self._work_queue.get_nowait()
                if pid is None:
                    self._running = False
                    break

                self._monitor_pid(pid, callback, args)

        except queue.Empty:
            pass

    def _monitor_pid(self, pid, callback, args):
        """Add pid and info to our list of pid's to be monitored.

        Return true if we successfully added the pid to kqueue.
        """
        if pid in self._server_pids:
            LOGGER.warning("_monitor_pid: already monitored? pid=%r", pid)

        self._server_pids[pid] = (callback, args)
        if self._add_proc_event(pid):
            return True

        return self._monitor_pid_fallback(pid)

    def _monitor_pid_fallback(self, pid):
        "Called when _monitor_pid fails to add a kevent."
        if self._check_pid(pid):
            return True

        LOGGER.warning("_monitor_pid_fallback: adding fallback pid=%r", pid)
        self._fallback_pids.add(pid)
        return False

    def _add_proc_event(self, pid):
        """Add kevent that monitors for process exit.

        Return true if successful.

        kevent can return ESRCH (ProcessLookupError) if it doesn't find the
        process ID. If this happens, return false.
        """
        try:
            event = select.kevent(
                ident=pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD
                | select.KQ_EV_ONESHOT
                | _KQ_EV_RECEIPT
                | select.KQ_EV_CLEAR,
                fflags=select.KQ_NOTE_EXIT,
            )
            result = self._kqueue.control([event], 0)
            assert result == []
            return True

        except ProcessLookupError as ex:
            # Process already exited, or kqueue random failure.
            return False

        except Exception as ex:  # pylint: disable=broad-except
            LOGGER.error("_add_proc_event: ex=%r", ex)
            raise

    def _add_read_event(self, fdesc):
        "Add kevent that monitors for file descriptor wakeup."
        try:
            event = select.kevent(
                ident=fdesc,
                filter=select.KQ_FILTER_READ,
                flags=select.KQ_EV_ADD,
            )
            result = self._kqueue.control([event], 0)
            assert result == []

        except Exception as ex:  # pylint: disable=broad-except
            LOGGER.error("_add_read_event: ex=%r", ex)

    def _add_signal_event(self, signo):
        "Add kevent that monitors for signals."
        try:
            event = select.kevent(
                ident=int(signo),
                filter=select.KQ_FILTER_SIGNAL,
                flags=select.KQ_EV_ADD,
            )
            result = self._kqueue.control([event], 0)
            assert result == []

        except Exception as ex:  # pylint: disable=broad-except
            LOGGER.error("_add_signal_event: ex=%r", ex)

    @staticmethod
    def _make_self_pipe():
        "Return (client, server) socket pair."
        client, server = socket.socketpair()
        client.setblocking(False)
        server.setblocking(False)
        return (client, server)
