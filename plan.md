# Lightweight realtime graph: Python server + embedded uPlot webpage

## Files to add

- `server/graph_server.py`
- `web/index.html`
- `README.md`
- `requirements.txt`

## Dependencies

- Python: `aiohttp` (serves HTTP and WebSocket in one lightweight event loop)

`requirements.txt`:

```
aiohttp==3.9.*
```

## Server (`server/graph_server.py`)

- A small class `RealtimeGraphServer` that:
  - Starts an `asyncio` loop and aiohttp app in a background thread.
  - Serves `GET /` → `web/index.html`.
  - Serves `GET /ws` → WebSocket endpoint.
  - Exposes thread-safe methods:
    - `appendHeader(headers_line: str)` → splits by spaces, broadcasts `{type:"header", headers:[...]}`.
    - `appendData(values: list[float])` → broadcasts `{type:"data", values:[...]}`.
- No data storage on server; it only broadcasts.
- Thread-safety: use `asyncio.run_coroutine_threadsafe` to schedule broadcasts on the loop; maintain a `set` of active sockets guarded by an `asyncio.Lock` in the loop thread.
- Startup:
  - Constructor accepts `host`, `port`, and `web_dir` path; `start()` spins the thread; `stop()` shuts down.
- Minimal example usage:
```python
srv = RealtimeGraphServer(host="0.0.0.0", port=8765, web_dir=Path(__file__).parent.parent/"web")
srv.start()
srv.appendHeader("time ch1 ch2")
# in your other code loop:
srv.appendData([0.0, 1.23, 4.56])
```


## Web client (`web/index.html`)

- Single offline HTML file with inline CSS and inline JS (no CDN).
- Embed uPlot (minified JS and CSS) directly in the file.
- WebSocket connects to `ws://<host>:<port>/ws`.
- Maintains buffers:
  - `times: number[]`
  - `series: number[][]` (one per header excluding `time`)
  - A copy of current headers.
- Rendering with uPlot:
  - One x-axis for time (seconds since start).
  - One y-scale per series (per-series autoscale), each with its own axis.
  - Per-series visibility toggle via checkboxes.
  - Per-series min/max inputs; when set, override autoscale for that series; include an "Autoscale" checkbox to revert.
- Controls:
  - Time window selector (e.g., 5/10/30/60 seconds or numeric input).
  - Buttons: Pause/Resume (toggle receiving into buffers), Clear (clear buffers + re-render), Save CSV (download current buffer as CSV; headers first row).
- Performance:
  - Batch redraws: accumulate incoming points, trigger at most every ~100ms.
  - Prune old points beyond selected window during redraw.
- Hover/tooltip:
  - Custom tooltip showing x (time) and each visible series value at current cursor index.
  - Crosshair via uPlot hooks/styles.

## Message protocol

- Header message:
```json
{"type":"header","headers":["time","ch1","ch2", ...]}
```

- Data message:
```json
{"type":"data","values":[t, v1, v2, ...]}
```


## README.md

- Instructions to install deps, run the server, and integrate from another Python process.
- Document `appendHeader` and `appendData` contract:
  - Headers: space-separated; first token and first value are always `time` (seconds since program start).
  - Data: list of numbers aligned with headers.
- Note: Server is stateless; all data handling is client-side.

## Key implementation snippets

- aiohttp WebSocket broadcast skeleton:
```python
async def _broadcast(self, msg: dict):
    text = json.dumps(msg, separators=(",", ":"))
    async with self._ws_lock:
        dead = []
        for ws in self._sockets:
            try:
                await ws.send_str(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._sockets.discard(ws)
```

- Thread-safe call from `appendData`:
```python
def appendData(self, values: list[float]):
    fut = asyncio.run_coroutine_threadsafe(
        self._broadcast({"type": "data", "values": values}), self._loop
    )
    fut.result(timeout=1)  # or ignore result for fire-and-forget
```


## Testing

- Manual: run server, open browser to `http://<pi>:8765`, simulate data appending in a Python REPL.
- Validate: toggles, time window, min/max, autoscale, hover tooltip, pause/clear/save CSV.