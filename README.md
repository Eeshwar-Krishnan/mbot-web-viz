# MBot Realtime Graph (Raspberry Pi friendly)

A tiny Python server that serves a lightweight webpage to visualize streamed timeseries data over WebSockets. Designed to run on a near-capacity Raspberry Pi 4.

- Server: `aiohttp` HTTP + WebSocket in a background thread
- Client: Single-file HTML with embedded minimal uPlot-like renderer (no CDN)
- Protocol: stateless broadcast of headers and data; no storage on server

## Dependencies

Listed in `requirements.txt`:

- `aiohttp==3.9.*`

Install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python server/graph_server.py
```

Then open in a browser on your network:

- `http://<raspberrypi-ip>:8765/`

The page auto-connects to `ws://<host>:8765/ws`.

## Integrate from another Python process

Create and hold a server instance in your main program. The server does not store data; it only broadcasts to connected clients.

```python
from pathlib import Path
from server.graph_server import RealtimeGraphServer

srv = RealtimeGraphServer(host="0.0.0.0", port=8765, web_dir=Path("web"))
srv.start()

srv.appendHeader("time ch1 ch2 ch3")

# later, repeatedly push rows
srv.appendData([0.00, 1.23, 2.34, 3.45])
srv.appendData([0.01, 1.24, 2.20, 3.40])
# ...

# on shutdown
srv.stop()
```

### Contracts

- `appendHeader(headers_line: str)`
  - Space-separated list of column names
  - First token must be `time`
- `appendData(values: list[float])`
  - List of numbers aligned with headers
  - First value must be time in seconds since program start

Notes:
- The server broadcasts messages as compact JSON:
  - Header: `{ "type":"header", "headers":["time", "ch1", ...] }`
  - Data: `{ "type":"data", "values":[t, v1, v2, ...] }`
- The browser buffers and renders data; you can pause/clear/save CSV client-side.

## UI features

- Time window control (seconds)
- Per-series visibility toggles
- Per-series manual min/max and autoscale toggle
- Pause, Clear, Save CSV (client-side only)
- Hover crosshair with values tooltip

## Performance tips

- Keep message rate modest if Wi‑Fi is weak. The client throttles redraws (~100ms).
- Send only the channels you need. Each series gets its own Y scale in the client.

## License

MIT for our code. uPlot license is MIT.


