# Traffic Router

A tiny HTTP reverse proxy that routes requests by path prefix and logs traffic as JSON lines.

It is useful when you want a lightweight "router kind of thing" in front of local services and need to inspect traffic without adding app-level logging.

Important: the reverse proxy only logs requests sent through the proxy URL. It does not automatically see every website or app your laptop uses.

## Log Laptop Connections

To log the TCP/UDP connections your Windows laptop opens, run:

```powershell
python -m traffic_router.monitor
```

This writes new connection metadata to:

```text
logs/connections.log.jsonl
```

Each line includes protocol, local address/port, remote address/port, connection state, process ID, and process name. This is useful for seeing which app connects to which remote server, but it is not full packet capture and it cannot show encrypted HTTPS page contents.

Example:

```json
{"event":"connection_seen","local_address":"10.228.64.159","local_port":"53600","pid":"22688","process_name":"chrome.exe","protocol":"tcp","remote_address":"104.46.162.231","remote_port":"443","state":"ESTABLISHED","timestamp":"2026-10-09T08:14:53.452242+00:00"}
```

If you open YouTube in a browser, the app name will usually be `chrome.exe` or `msedge.exe`, not `youtube`. The logger can identify the application process, but exact website names require DNS or browser/proxy logging.

To see which program owns a PID:

```powershell
Get-Process -Id <PID>
```

## Make The Laptop Act Like A Router

If you want other devices to connect through your laptop, use Windows Mobile Hotspot or Internet Connection Sharing, then run a traffic logger on the laptop.

For full packet logs, use an admin-level capture tool such as Wireshark/Npcap or Windows `pktmon`. A normal Python process cannot capture all packets on Windows without that kind of privileged capture support.

## Run

Copy the example config, edit the routes, then start the router:

```powershell
Copy-Item router-config.example.json router-config.json
python -m traffic_router.router --config router-config.json
```

The example starts on `http://127.0.0.1:8080`.

## Config

```json
{
  "host": "127.0.0.1",
  "port": 8080,
  "log_file": "logs/traffic.log.jsonl",
  "timeout_seconds": 30,
  "routes": [
    {
      "prefix": "/api",
      "target": "http://127.0.0.1:3000",
      "strip_prefix": false
    }
  ]
}
```

Route fields:

- `prefix`: request path prefix to match.
- `target`: upstream service URL.
- `strip_prefix`: when `true`, removes `prefix` before forwarding.

## Log Format

Each request writes one JSON object to the configured `.jsonl` file:

```json
{"client":"127.0.0.1","duration_ms":12.4,"method":"GET","path":"/api/users","request_bytes":0,"response_bytes":128,"status":200,"timestamp":"2026-10-09T07:58:00.000000+00:00"}
```

## Test

```powershell
python -m unittest discover -s tests
```







{
  "event": "connection_seen",
  "local_address": "10.228.64.159",
  "local_port": "53600",
  "remote_address": "104.46.162.231",
  "remote_port": "443",
  "protocol": "tcp",
  "state": "LAST_ACK",
  "pid": "22688",
  "timestamp": "2026-10-09T08:14:53Z"
}


local_address: IP address of my laptop
local_port: temporary port used by my laptop
remote_address: destination server IP
remote_port: service port, e.g. 443 means HTTPS
protocol: TCP or UDP
state: connection status
pid: process ID of the app that made the connection
timestamp: time of logging




python -m traffic_router.monitor
Get-Content logs\connections.log.jsonl -Wait
Get-Process -Id 1234
