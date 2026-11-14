from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Route:
    prefix: str
    target: str
    strip_prefix: bool = False


@dataclass(frozen=True)
class RouterConfig:
    host: str
    port: int
    log_file: Path
    routes: tuple[Route, ...]
    timeout_seconds: float = 30.0


def load_config(path: str | Path) -> RouterConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    routes_raw = raw.get("routes", [])
    if not isinstance(routes_raw, list):
        raise ValueError("config routes must be a list")

    routes = tuple(_parse_route(item) for item in routes_raw)
    if not routes:
        raise ValueError("config must define at least one route")

    port = int(raw.get("port", 8080))
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be between 1 and 65535: {port}")

    timeout_seconds = float(raw.get("timeout_seconds", 30))
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")

    return RouterConfig(
        host=str(raw.get("host", "127.0.0.1")),
        port=port,
        log_file=(config_path.parent / raw.get("log_file", "traffic.log.jsonl")).resolve(),
        timeout_seconds=timeout_seconds,
        routes=routes,
    )


def _parse_route(raw: dict[str, Any]) -> Route:
    if not isinstance(raw, dict):
        raise ValueError("route entries must be objects")

    prefix = str(raw["prefix"])
    target_raw = str(raw["target"])
    target_parts = urlsplit(target_raw)
    if not prefix.startswith("/"):
        raise ValueError(f"route prefix must start with '/': {prefix}")
    if target_parts.scheme not in {"http", "https"}:
        raise ValueError(f"route target must be an absolute HTTP URL: {target_raw}")
    if not target_parts.hostname:
        raise ValueError(f"route target must include a host: {target_raw}")
    return Route(
        prefix=prefix.rstrip("/") or "/",
        target=target_raw.rstrip("/"),
        strip_prefix=bool(raw.get("strip_prefix", False)),
    )

