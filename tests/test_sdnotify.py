import os
import socket
import tempfile
import unittest
from unittest import mock

from snapshot_kwin import sdnotify


class SdNotifyTest(unittest.TestCase):
    def test_no_socket_is_a_noop(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(sdnotify.notify("READY=1"))
            self.assertIsNone(sdnotify.watchdog_interval_s())

    def test_sends_datagram(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notify")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as srv:
                srv.bind(path)
                with mock.patch.dict(os.environ, {"NOTIFY_SOCKET": path}):
                    self.assertTrue(sdnotify.notify("WATCHDOG=1"))
                self.assertEqual(srv.recv(64), b"WATCHDOG=1")

    def test_watchdog_interval_is_a_third(self):
        env = {"WATCHDOG_USEC": "30000000", "WATCHDOG_PID": str(os.getpid())}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(sdnotify.watchdog_interval_s(), 10)
        with mock.patch.dict(os.environ, dict(env, WATCHDOG_PID="1"), clear=True):
            self.assertIsNone(sdnotify.watchdog_interval_s())


if __name__ == "__main__":
    unittest.main()
