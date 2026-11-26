from contextlib import closing
import errno
from http.client import HTTPConnection, HTTPException
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import radar
from test_radar import NOW, consensus, quote


APP = Path(__file__).resolve().parents[1] / "app.py"


def simulated_scan(symbols, *, api_key):
    return [
        radar.merge(symbol, [{
            **quote(currency=radar.CURRENCIES[symbol]),
            **consensus(currency=radar.CURRENCIES[symbol]),
        }], NOW)
        for symbol in symbols
    ]


class DashboardTests(unittest.TestCase):
    def test_import_app(self):
        spec = importlib.util.spec_from_file_location("radar_dashboard", APP)
        module = importlib.util.module_from_spec(spec)
        with patch("radar.scan", side_effect=AssertionError("Import start geen scan")):
            spec.loader.exec_module(module)
        self.assertEqual(module.VERSION, "0.4")
        self.assertEqual(module.ALERT_THRESHOLD, 75)

    def test_streamlit_session_without_configured_key(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(APP), default_timeout=30)
        with patch.dict(os.environ, {"FINNHUB_API_KEY": " "}), \
                patch("streamlit.secrets", {}), \
                patch("radar.scan", side_effect=simulated_scan) as scan:
            app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Yahoo Finance is actief" in item.value for item in app.info))
        scan.assert_called_once_with(tuple(radar.SYMBOLS), api_key="")

    def test_streamlit_session_renders_data_and_filters(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(APP), default_timeout=30)
        app.secrets["FINNHUB_API_KEY"] = "TEST-STREAMLIT-SECRET"
        with patch.dict(os.environ, {"FINNHUB_API_KEY": ""}), \
                patch("radar.scan", side_effect=simulated_scan) as scan, \
                patch("radar.requests.post") as post:
            app.run()
            self.assertFalse(app.exception)
            self.assertIn("AI Stock Radar V0.4", app.title[0].value)
            self.assertEqual(len(app.dataframe[0].value), 16)
            self.assertEqual(set(app.dataframe[0].value["quality"]), {59})
            self.assertTrue(app.warning)
            self.assertIn("Diagnostiek databronnen", [item.label for item in app.expander])
            self.assertIn("Finnhub-configuratie · Streamlit Secrets", [item.value for item in app.caption])
            panels = (*app.caption, *app.info, *app.warning, *app.markdown, *app.dataframe)
            for item in panels:
                self.assertNotIn("TEST-STREAMLIT-SECRET", str(item.value))
            scan.assert_called_once_with(tuple(radar.SYMBOLS), api_key="TEST-STREAMLIT-SECRET")
            app.sidebar.slider[1].set_value(60).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[0].value), 16)
            self.assertTrue(any("Geen kansen" in item.value for item in app.info))
            post.assert_not_called()

    def test_streamlit_server_starts(self):
        for _ in range(3):
            operation = "IPv4-socket aanmaken"
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as address:
                    operation = "loopback-socket binden aan 127.0.0.1"
                    address.bind(("127.0.0.1", 0))
                    port = address.getsockname()[1]
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EPERM):
                    raise
                reason = f"{operation} geweigerd: {exc}"
                if os.environ.get("REQUIRE_STREAMLIT_HTTP") == "1":
                    self.fail(reason)
                self.skipTest(reason)

            if self._start_streamlit(port):
                return
        self.fail("Streamlit kon na drie pogingen geen vrije loopback-poort binden.")

    def _start_streamlit(self, port):
        # A different Streamlit instance on this port cannot pass our healthcheck.
        base_path = uuid4().hex
        health_path = f"/{base_path}/_stcore/health"
        env = {**os.environ, "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false", "FINNHUB_API_KEY": ""}
        with tempfile.TemporaryFile() as log, subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(APP),
             "--server.headless=true", "--server.fileWatcherType=none",
             "--server.address=127.0.0.1", f"--server.port={port}",
             f"--server.baseUrlPath={base_path}"],
            cwd=APP.parent, env=env, stdout=log, stderr=subprocess.STDOUT,
        ) as server:
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and server.poll() is None:
                    try:
                        with closing(HTTPConnection("127.0.0.1", port, timeout=1)) as client:
                            client.request("GET", health_path)
                            with client.getresponse() as response:
                                if response.status == 200 and response.read(3) == b"ok":
                                    return True
                    except (ConnectionError, TimeoutError, HTTPException):
                        pass
                    time.sleep(0.1)
                log.seek(0)
                output = log.read().decode(errors="replace")
                if server.poll() == 1 and port_is_occupied(port, output):
                    return False
                self.fail(f"Streamlit healthcheck niet gereed:\n{output}")
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()


def port_is_occupied(port, output):
    last_line = output.strip().splitlines()[-1] if output.strip() else ""
    if last_line.endswith(f"Port {port} is already in use"):
        return True
    if last_line.endswith(f"Port {port} is not available"):
        # Recent Streamlit versions use the same message for EADDRINUSE and EACCES.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as address:
            try:
                address.bind(("127.0.0.1", port))
            except OSError as exc:
                return exc.errno == errno.EADDRINUSE
    return False


class StreamlitSmokeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.smoke = DashboardTests("test_streamlit_server_starts")

    def test_loopback_permission_denied_skips_locally_but_fails_in_ci(self):
        for operation, error in (("aanmaken", errno.EPERM), ("binden", errno.EACCES)):
            for required, outcome in (("0", unittest.SkipTest), ("1", AssertionError)):
                with self.subTest(operation=operation, required=required), \
                        patch.dict(os.environ, {"REQUIRE_STREAMLIT_HTTP": required}), \
                        patch("socket.socket") as create, patch("subprocess.Popen") as start:
                    target = create if operation == "aanmaken" else create.return_value.__enter__.return_value.bind
                    target.side_effect = PermissionError(error, os.strerror(error))
                    with self.assertRaisesRegex(outcome, rf"{operation}.*Errno {error}"):
                        self.smoke.test_streamlit_server_starts()
                    start.assert_not_called()

    def test_unrelated_socket_error_is_not_skipped(self):
        with patch("socket.socket", side_effect=OSError(errno.EMFILE, os.strerror(errno.EMFILE))):
            with self.assertRaises(OSError) as error:
                self.smoke.test_streamlit_server_starts()
        self.assertEqual(error.exception.errno, errno.EMFILE)

    def test_port_conflict_retries_with_fresh_port_and_has_attempt_limit(self):
        for outcomes, expected_ports in (([False, True], [41001, 41002]),
                                         ([False] * 3, [41001, 41002, 41003])):
            with self.subTest(outcomes=outcomes), patch("socket.socket") as create, \
                    patch.object(self.smoke, "_start_streamlit", side_effect=outcomes) as start:
                address = create.return_value.__enter__.return_value
                address.getsockname.side_effect = [("127.0.0.1", port) for port in expected_ports]
                if outcomes[-1]:
                    self.smoke.test_streamlit_server_starts()
                else:
                    with self.assertRaisesRegex(AssertionError, "drie pogingen"):
                        self.smoke.test_streamlit_server_starts()
                self.assertEqual([call.args[0] for call in start.call_args_list], expected_ports)

    def test_only_confirmed_bind_collision_allows_retry(self):
        port = 41001
        for output, bind_error, collision in (
            (f"Port {port} is already in use", None, True),
            (f"Port {port} is not available", errno.EADDRINUSE, True),
            (f"Port {port} is not available", errno.EACCES, False),
            ("ImportError: cannot import name ALERT_THRESHOLD", None, False),
        ):
            with self.subTest(output=output, bind_error=bind_error), \
                    patch("socket.socket") as create, patch("subprocess.Popen") as spawn:
                address = create.return_value.__enter__.return_value
                if bind_error:
                    address.bind.side_effect = OSError(bind_error, os.strerror(bind_error))
                server = MagicMock()
                server.poll.return_value = 1

                def start(*args, stdout, **kwargs):
                    stdout.write(output.encode())
                    return server

                spawn.side_effect = start
                server.__enter__.return_value = server
                if collision:
                    self.assertFalse(self.smoke._start_streamlit(port))
                else:
                    with self.assertRaisesRegex(AssertionError, output):
                        self.smoke._start_streamlit(port)
                spawn.assert_called_once()
                server.__exit__.assert_called_once()
