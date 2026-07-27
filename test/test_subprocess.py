"""
test_subprocess.py — Tests for subprocess and threading operations.
"""

import queue
import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest
from main import run_subprocess


class TestRunSubprocess:
    """Test subprocess execution and output streaming."""

    def test_run_subprocess_success(self):
        """Should capture stdout from successful subprocess."""
        output_q = queue.Queue()

        # Use a simple command that always succeeds
        cmd = ["python", "-c", "print('hello'); print('world')"]
        thread = threading.Thread(target=run_subprocess, args=(cmd, output_q), daemon=True)
        thread.start()
        thread.join(timeout=5)

        messages = []
        while not output_q.empty():
            messages.append(output_q.get())

        # Should have captured stdout messages + done signal
        stdout_msgs = [m for m in messages if m[0] == "stdout"]
        done_msgs = [m for m in messages if m[0] == "done"]

        assert len(stdout_msgs) >= 1
        assert len(done_msgs) == 1
        assert done_msgs[0][1] == 0  # returncode 0

    def test_run_subprocess_failure(self):
        """Should capture error from failed subprocess."""
        output_q = queue.Queue()

        # Use a command that fails
        cmd = ["python", "-c", "import sys; sys.exit(1); print('should not print')"]
        thread = threading.Thread(target=run_subprocess, args=(cmd, output_q), daemon=True)
        thread.start()
        thread.join(timeout=5)

        messages = []
        while not output_q.empty():
            messages.append(output_q.get())

        error_msgs = [m for m in messages if m[0] == "error"]
        assert len(error_msgs) == 1

    def test_run_subprocess_nonexistent_command(self):
        """Should error if command does not exist."""
        output_q = queue.Queue()

        cmd = ["nonexistent_command_xyz", "arg1"]
        thread = threading.Thread(target=run_subprocess, args=(cmd, output_q), daemon=True)
        thread.start()
        thread.join(timeout=5)

        messages = []
        while not output_q.empty():
            messages.append(output_q.get())

        error_msgs = [m for m in messages if m[0] == "error"]
        # Only assert that an error was reported — the message text itself is the
        # OS's wording, not our logic (Linux says "[Errno 2] No such file or
        # directory", Windows phrases it differently), so asserting on specific
        # substrings is platform-dependent and would break on Linux CI.
        assert len(error_msgs) == 1

    def test_output_queue_thread_safety(self):
        """Output queue should be thread-safe."""
        output_q = queue.Queue()

        # Multiple threads writing to same queue
        def write_messages(msg_list):
            for msg in msg_list:
                output_q.put(msg)

        msgs_a = [("type", "a1"), ("type", "a2"), ("type", "a3")]
        msgs_b = [("type", "b1"), ("type", "b2")]

        t1 = threading.Thread(target=write_messages, args=(msgs_a,), daemon=True)
        t2 = threading.Thread(target=write_messages, args=(msgs_b,), daemon=True)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Should have all messages (order may vary)
        total_msgs = []
        while not output_q.empty():
            total_msgs.append(output_q.get())

        assert len(total_msgs) == 5


class TestSubprocessOutputPolling:
    """Test polling output queue from subprocess."""

    def test_queue_get_nonblocking(self):
        """Should not block when queue is empty."""
        output_q = queue.Queue()

        # This should not block
        try:
            msg_type, msg_content = output_q.get_nowait()
            assert False, "Should raise queue.Empty"
        except queue.Empty:
            pass  # Expected

    def test_queue_get_with_timeout(self):
        """Should timeout if waiting too long."""
        output_q = queue.Queue()

        # Simulate waiting 500ms for message
        messages_received = []
        for _ in range(5):
            try:
                msg = output_q.get(timeout=0.1)
                messages_received.append(msg)
            except queue.Empty:
                pass

        # Should have received no messages
        assert len(messages_received) == 0

    def test_multiple_messages_in_queue(self):
        """Should handle multiple messages queued."""
        output_q = queue.Queue()

        # Queue multiple messages
        messages = [
            ("stdout", "Line 1"),
            ("stdout", "Line 2"),
            ("stdout", "Line 3"),
            ("done", 0),
        ]
        for msg in messages:
            output_q.put(msg)

        # Retrieve all without blocking
        received = []
        while not output_q.empty():
            try:
                received.append(output_q.get_nowait())
            except queue.Empty:
                break

        assert len(received) == 4
        assert received[-1] == ("done", 0)
