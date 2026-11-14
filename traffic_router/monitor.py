from __future__ import annotations

import argparse
import csv
import subprocess
import time
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path

from .logger import JsonlTrafficLogger


@dataclass(frozen=True)
class Connection:
    protocol: str
    local_address: str
    local_port: str
    remote_address: str
    remote_port: str
    state: str
    pid: str


def parse_netstat(output: str) -> tuple[Connection, ...]:
    connections: list[Connection] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith(("TCP", "UDP")):
            continue

        parts = line.split()
        protocol = parts[0].lower()
        if protocol == "tcp" and len(parts) >= 5:
            local_host, local_port = split_endpoint(parts[1])
            remote_host, remote_port = split_endpoint(parts[2])
            connections.append(
                Connection(
                    protocol=protocol,
                    local_address=local_host,
                    local_port=local_port,
                    remote_address=remote_host,
                    remote_port=remote_port,
                    state=parts[3],
                    pid=parts[4],
                )
            )
        elif protocol == "udp" and len(parts) >= 4:
            local_host, local_port = split_endpoint(parts[1])
            remote_host, remote_port = split_endpoint(parts[2])
            connections.append(
                Connection(
                    protocol=protocol,
                    local_address=local_host,
                    local_port=local_port,
                    remote_address=remote_host,
                    remote_port=remote_port,
                    state="",
                    pid=parts[3],
                )
            )
    return tuple(connections)


def split_endpoint(endpoint: str) -> tuple[str, str]:
    if endpoint in {"*", "*:*"}:
        return "*", "*"
    if endpoint.startswith("["):
        host, _, port = endpoint.rpartition("]:")
        return host.removeprefix("["), port or "*"
    host, separator, port = endpoint.rpartition(":")
    if not separator:
        return endpoint, "*"
    return host, port


def read_connections() -> tuple[Connection, ...]:
    result = subprocess.run(
        ["netstat", "-ano"],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_netstat(result.stdout)


def parse_tasklist(output: str) -> dict[str, str]:
    processes: dict[str, str] = {}
    for row in csv.reader(StringIO(output)):
        if len(row) < 2 or row[0].lower() == "image name":
            continue
        processes[row[1]] = row[0]
    return processes


def read_process_names() -> dict[str, str]:
    result = subprocess.run(
        ["tasklist", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_tasklist(result.stdout)


def monitor(log_file: Path, interval_seconds: float) -> None:
    logger = JsonlTrafficLogger(log_file)
    seen: set[Connection] = set()
    print(f"Logging new connections to {log_file}")
    print("Press Ctrl+C to stop.")

    while True:
        process_names = read_process_names()
        for connection in read_connections():
            if connection in seen:
                continue
            seen.add(connection)
            logger.write(
                {
                    "event": "connection_seen",
                    **asdict(connection),
                    "process_name": process_names.get(connection.pid, "unknown"),
                }
            )
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log active laptop TCP/UDP connections as JSON lines."
    )
    parser.add_argument(
        "--log-file",
        default="logs/connections.log.jsonl",
        help="Path to write connection logs. Defaults to logs/connections.log.jsonl.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds. Defaults to 1.",
    )
    args = parser.parse_args()

    if args.interval <= 0:
        raise ValueError("--interval must be greater than 0")
    monitor(Path(args.log_file), args.interval)


if __name__ == "__main__":
    main()
