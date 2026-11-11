"""Port selection regression tests; no real game directories or fixed ports."""

import errno
import http.client
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock


MANAGER_DIR = Path(__file__).resolve().parents[1] / "local-manager"
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))
import server as manager_server


class ManagerStartupTests(unittest.TestCase):
    def setUp(self):
        self.workshop = mock.Mock()
        self.workshop.token = "temporary-startup-fixture-token"
        self.workshop.get.return_value = {"fixture": True}

    @staticmethod
    def attempted_ports(factory):
        return [call.args[0][1] for call in factory.call_args_list]

    def occupied_listener(self):
        # Reserve an OS-assigned port. Keep it open throughout the startup call.
        for _ in range(10):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(2)
            listener.settimeout(3)
            if listener.getsockname()[1] < 65535:
                self.addCleanup(listener.close)
                return listener
            listener.close()
        self.skipTest("The OS repeatedly assigned the upper boundary port.")

    @staticmethod
    def http_get(server, token=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            headers = {"X-Workshop-Token": token} if token else {}
            connection.request("GET", "/api/status", headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_occupied_port_uses_real_new_listener_without_stopping_old_listener(self):
        old = self.occupied_listener()
        preferred = old.getsockname()[1]
        server = manager_server.create_local_server(self.workshop, preferred_port=preferred,
                                                    port_attempts=20)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        self.assertGreater(server.server_port, preferred)
        self.assertLessEqual(server.server_port, min(preferred + 19, 65535))

        # The existing service still owns its socket and accepts its own traffic.
        with socket.create_connection(("127.0.0.1", preferred), timeout=3) as client:
            client.sendall(b"old listener still alive")
            accepted, _ = old.accept()
            with accepted:
                accepted.settimeout(3)
                self.assertEqual(accepted.recv(100), b"old listener still alive")

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            thread.join(timeout=3)

        self.addCleanup(stop)
        self.assertEqual(self.http_get(server)[0], 401)
        code, result = self.http_get(server, self.workshop.token)
        self.assertEqual(code, 200)
        self.assertEqual(result, {"ok": True, "data": {"fixture": True}})
        self.assertNotIn(self.workshop.token, json.dumps(result))

    def test_zero_port_lets_operating_system_choose_a_real_loopback_port(self):
        server = manager_server.create_local_server(self.workshop, preferred_port=0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        self.assertGreater(server.server_port, 0)
        self.assertLessEqual(server.server_port, 65535)
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=3):
            pass

    def test_unrelated_os_errors_are_raised_immediately_without_port_retry(self):
        errors = [PermissionError(errno.EACCES, "permission denied"),
                  OSError(errno.EADDRNOTAVAIL, "address not available"),
                  OSError("Address already in use but no occupied-port errno")]
        for error in errors:
            with self.subTest(error=repr(error)):
                with mock.patch.object(manager_server, "ThreadingHTTPServer", side_effect=error) as factory:
                    with self.assertRaises(OSError) as rejected:
                        manager_server.create_local_server(self.workshop, preferred_port=32000,
                                                           port_attempts=20)
                self.assertIs(rejected.exception, error)
                self.assertEqual(self.attempted_ports(factory), [32000])

    def test_windows_address_in_use_error_retries_next_port(self):
        busy = OSError("Windows address already in use")
        busy.winerror = 10048
        expected = mock.Mock()
        with mock.patch.object(manager_server, "ThreadingHTTPServer", side_effect=[busy, expected]) as factory:
            server = manager_server.create_local_server(self.workshop, preferred_port=32000,
                                                        port_attempts=20)
        self.assertIs(server, expected)
        self.assertEqual(self.attempted_ports(factory), [32000, 32001])

    def test_busy_ports_stop_at_requested_attempt_limit(self):
        busy = OSError(errno.EADDRINUSE, "fixture port busy")
        with mock.patch.object(manager_server, "ThreadingHTTPServer", side_effect=busy) as factory:
            with self.assertRaises(OSError):
                manager_server.create_local_server(self.workshop, preferred_port=32000,
                                                   port_attempts=3)
        self.assertEqual(self.attempted_ports(factory), [32000, 32001, 32002])

    def test_upper_port_boundary_does_not_wrap_or_try_65536(self):
        busy = OSError(errno.EADDRINUSE, "fixture upper port busy")
        with mock.patch.object(manager_server, "ThreadingHTTPServer", side_effect=busy) as factory:
            with self.assertRaises(OSError):
                manager_server.create_local_server(self.workshop, preferred_port=65535,
                                                   port_attempts=20)
        self.assertEqual(self.attempted_ports(factory), [65535])

    def test_invalid_ports_are_rejected_before_creating_a_listener(self):
        with mock.patch.object(manager_server, "ThreadingHTTPServer") as factory:
            for port in (-1, 65536, "invalid", None, 1.5):
                with self.subTest(port=port), self.assertRaises((ValueError, TypeError)):
                    manager_server.create_local_server(self.workshop, preferred_port=port)
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
