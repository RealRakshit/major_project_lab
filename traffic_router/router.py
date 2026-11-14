from __future__ import annotations

import argparse
import http.client
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import urlsplit

from .config import Route, RouterConfig, load_config
from .logger import JsonlTrafficLogger


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def find_route(path: str, routes: Iterable[Route]) -> Route | None:
    matches = [
        route
        for route in routes
        if path == route.prefix or path.startswith(route.prefix.rstrip("/") + "/")
    ]
    return max(matches, key=lambda route: len(route.prefix), default=None)


def build_upstream_path(route: Route, request_path: str) -> str:
    parsed_target = urlsplit(route.target)
    base_path = parsed_target.path.rstrip("/")
    path = request_path

    if route.strip_prefix and route.prefix != "/":
        path = path[len(route.prefix) :] or "/"

    if not path.startswith("/"):
        path = "/" + path
    return base_path + path


def create_handler(config: RouterConfig, logger: JsonlTrafficLogger):
    class TrafficRouterHandler(BaseHTTPRequestHandler):
        server_version = "TrafficRouter/1.0"

        def do_GET(self) -> None:
            self._proxy()

        def do_POST(self) -> None:
            self._proxy()

        def do_PUT(self) -> None:
            self._proxy()

        def do_PATCH(self) -> None:
            self._proxy()

        def do_DELETE(self) -> None:
            self._proxy()

        def do_HEAD(self) -> None:
            self._proxy()

        def do_OPTIONS(self) -> None:
            self._proxy()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _proxy(self) -> None:
            start = time.perf_counter()
            parsed_request = urlsplit(self.path)
            route = find_route(parsed_request.path, config.routes)
            request_body = self._read_body()

            if route is None:
                self.send_error(404, "No route configured for path")
                logger.write(
                    self._event(
                        status=404,
                        duration_ms=start,
                        request_bytes=len(request_body),
                        response_bytes=0,
                        error="route_not_found",
                    )
                )
                return

            upstream = urlsplit(route.target)
            upstream_path = build_upstream_path(route, parsed_request.path)
            if parsed_request.query:
                upstream_path += "?" + parsed_request.query

            status = 502
            response_bytes = 0
            error = None

            try:
                connection_cls = (
                    http.client.HTTPSConnection
                    if upstream.scheme == "https"
                    else http.client.HTTPConnection
                )
                connection = connection_cls(
                    upstream.hostname,
                    upstream.port,
                    timeout=config.timeout_seconds,
                )
                connection.request(
                    self.command,
                    upstream_path,
                    body=request_body,
                    headers=self._forward_headers(upstream.netloc),
                )
                response = connection.getresponse()
                status = response.status
                response_body = b"" if self.command == "HEAD" else response.read()
                response_bytes = len(response_body)

                self.send_response(status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_BY_HOP_HEADERS:
                        self.send_header(key, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response_body)
            except Exception as exc:  # pragma: no cover - exercised by runtime failures.
                error = f"{type(exc).__name__}: {exc}"
                body = b"Bad gateway\n"
                response_bytes = len(body)
                self.send_response(502, "Bad Gateway")
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(response_bytes))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            finally:
                if "connection" in locals():
                    connection.close()
                logger.write(
                    self._event(
                        status=status,
                        duration_ms=start,
                        request_bytes=len(request_body),
                        response_bytes=response_bytes,
                        route=asdict(route),
                        upstream=f"{upstream.scheme}://{upstream.netloc}{upstream_path}"
                        if route
                        else None,
                        error=error,
                    )
                )

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _forward_headers(self, upstream_host: str) -> dict[str, str]:
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
            }
            forwarded_for = self.headers.get("X-Forwarded-For")
            headers["Host"] = upstream_host
            headers["X-Forwarded-For"] = (
                f"{forwarded_for}, {self.client_address[0]}"
                if forwarded_for
                else self.client_address[0]
            )
            headers["X-Forwarded-Host"] = self.headers.get("Host", "")
            headers["X-Forwarded-Proto"] = "http"
            return headers

        def _event(self, **fields: object) -> dict[str, object]:
            duration_ms = fields.pop("duration_ms")
            return {
                "client": self.client_address[0],
                "method": self.command,
                "path": self.path,
                "status": fields.pop("status"),
                "duration_ms": round((time.perf_counter() - float(duration_ms)) * 1000, 2),
                **fields,
            }

    return TrafficRouterHandler


def run(config: RouterConfig) -> None:
    logger = JsonlTrafficLogger(config.log_file)
    server = ThreadingHTTPServer((config.host, config.port), create_handler(config, logger))
    print(f"Traffic router listening on http://{config.host}:{config.port}")
    print(f"Writing traffic logs to {config.log_file}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Log and route HTTP traffic.")
    parser.add_argument(
        "--config",
        default="router-config.json",
        help="Path to router JSON config. Defaults to router-config.json.",
    )
    args = parser.parse_args()
    run(load_config(args.config))


if __name__ == "__main__":
    main()
