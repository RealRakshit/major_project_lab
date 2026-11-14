import unittest
import json
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from traffic_router.config import Route, RouterConfig, load_config
from traffic_router.logger import JsonlTrafficLogger
from traffic_router.monitor import parse_netstat, parse_tasklist, split_endpoint
from traffic_router.router import build_upstream_path, create_handler, find_route


class UpstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"upstream:{self.path}".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        request_body = self.rfile.read(length)
        body = (
            f"path:{self.path}\n"
            f"host:{self.headers.get('Host')}\n"
            f"xff:{self.headers.get('X-Forwarded-For')}\n"
            f"xfh:{self.headers.get('X-Forwarded-Host')}\n"
            f"body:{request_body.decode('utf-8')}"
        ).encode("utf-8")
        self.send_response(201)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class RouterTests(unittest.TestCase):
    def test_find_route_uses_longest_matching_prefix(self):
        routes = (
            Route(prefix="/api", target="http://localhost:3000"),
            Route(prefix="/api/admin", target="http://localhost:4000"),
        )

        self.assertEqual(find_route("/api/admin/users", routes), routes[1])

    def test_find_route_does_not_match_partial_segment(self):
        routes = (Route(prefix="/api", target="http://localhost:3000"),)

        self.assertIsNone(find_route("/apix", routes))

    def test_build_upstream_path_keeps_prefix_by_default(self):
        route = Route(prefix="/api", target="http://localhost:3000/v1")

        self.assertEqual(build_upstream_path(route, "/api/users"), "/v1/api/users")

    def test_build_upstream_path_can_strip_prefix(self):
        route = Route(
            prefix="/api",
            target="http://localhost:3000/v1",
            strip_prefix=True,
        )

        self.assertEqual(build_upstream_path(route, "/api/users"), "/v1/users")

    def test_load_config_rejects_invalid_port(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "router.json"
            config_file.write_text(
                json.dumps(
                    {
                        "port": 70000,
                        "routes": [{"prefix": "/api", "target": "http://localhost:3000"}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "port"):
                load_config(config_file)

    def test_load_config_rejects_target_without_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "router.json"
            config_file.write_text(
                json.dumps({"routes": [{"prefix": "/api", "target": "http://"}]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "host"):
                load_config(config_file)

    def test_split_endpoint_handles_ipv4_ipv6_and_wildcards(self):
        self.assertEqual(split_endpoint("127.0.0.1:443"), ("127.0.0.1", "443"))
        self.assertEqual(split_endpoint("[::1]:5353"), ("::1", "5353"))
        self.assertEqual(split_endpoint("*:*"), ("*", "*"))

    def test_parse_netstat_reads_tcp_and_udp_rows(self):
        output = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    192.168.1.10:51522     142.250.183.14:443    ESTABLISHED     1234
  UDP    0.0.0.0:5353           *:*                                   5678
"""

        connections = parse_netstat(output)

        self.assertEqual(len(connections), 2)
        self.assertEqual(connections[0].protocol, "tcp")
        self.assertEqual(connections[0].remote_port, "443")
        self.assertEqual(connections[0].state, "ESTABLISHED")
        self.assertEqual(connections[1].protocol, "udp")
        self.assertEqual(connections[1].pid, "5678")

    def test_parse_tasklist_maps_pid_to_process_name(self):
        output = (
            '"chrome.exe","1234","Console","1","250,000 K"\n'
            '"python.exe","5678","Console","1","50,000 K"\n'
        )

        self.assertEqual(
            parse_tasklist(output),
            {"1234": "chrome.exe", "5678": "python.exe"},
        )

    def test_proxy_forwards_request_and_writes_log(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()

        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "traffic.log.jsonl"
            target = f"http://127.0.0.1:{upstream.server_port}"
            config = RouterConfig(
                host="127.0.0.1",
                port=0,
                log_file=log_file,
                routes=(Route(prefix="/api", target=target, strip_prefix=True),),
            )
            proxy = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(config, JsonlTrafficLogger(log_file)),
            )
            proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            proxy_thread.start()

            try:
                url = f"http://127.0.0.1:{proxy.server_port}/api/users?active=1"
                with urllib.request.urlopen(url, timeout=5) as response:
                    self.assertEqual(response.read(), b"upstream:/users?active=1")

                deadline = time.monotonic() + 2
                while not log_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)

                log_entry = json.loads(log_file.read_text(encoding="utf-8").strip())
                self.assertEqual(log_entry["method"], "GET")
                self.assertEqual(log_entry["path"], "/api/users?active=1")
                self.assertEqual(log_entry["status"], 200)
                self.assertGreater(log_entry["response_bytes"], 0)
            finally:
                proxy.shutdown()
                proxy.server_close()
                upstream.shutdown()
                upstream.server_close()

    def test_proxy_forwards_post_body_and_forwarded_headers(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()

        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "traffic.log.jsonl"
            target = f"http://127.0.0.1:{upstream.server_port}"
            config = RouterConfig(
                host="127.0.0.1",
                port=0,
                log_file=log_file,
                routes=(Route(prefix="/api", target=target, strip_prefix=True),),
            )
            proxy = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(config, JsonlTrafficLogger(log_file)),
            )
            proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            proxy_thread.start()

            try:
                body = b'{"name":"plab"}'
                request = urllib.request.Request(
                    f"http://127.0.0.1:{proxy.server_port}/api/items",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Forwarded-For": "10.0.0.1",
                    },
                )

                with urllib.request.urlopen(request, timeout=5) as response:
                    response_body = response.read().decode("utf-8")
                    self.assertEqual(response.status, 201)

                self.assertIn("path:/items", response_body)
                self.assertIn(f"host:127.0.0.1:{upstream.server_port}", response_body)
                self.assertIn("xff:10.0.0.1, 127.0.0.1", response_body)
                self.assertIn(f"xfh:127.0.0.1:{proxy.server_port}", response_body)
                self.assertIn('body:{"name":"plab"}', response_body)
            finally:
                proxy.shutdown()
                proxy.server_close()
                upstream.shutdown()
                upstream.server_close()


if __name__ == "__main__":
    unittest.main()
