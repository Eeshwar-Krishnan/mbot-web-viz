import asyncio
import json
import threading
from pathlib import Path
from typing import List, Optional, Set
import time

from aiohttp import web, WSMsgType


class RealtimeGraphServer:
    """Lightweight HTTP + WebSocket server for streaming timeseries.

    - Serves a static `index.html` client.
    - Broadcasts header and data JSON messages over WebSocket to all clients.
    - Does NOT persist data; acts as a relay only.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        web_dir: Optional[Path] = None,
        header_interval: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._web_dir = Path(web_dir) if web_dir is not None else Path(__file__).parent
        self._header_interval = max(0.0, float(header_interval))

        self.__start = time.time()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._start_exc: Optional[BaseException] = None

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self._sockets: Set[web.WebSocketResponse] = set()
        self._ws_lock: Optional[asyncio.Lock] = None
        self._header_task: Optional[asyncio.Task] = None
        self._last_header: Optional[List[str]] = None

    # ----------------------- Public API -----------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            # Already running; make sure startup completed before returning.
            self._ready_event.wait(timeout=5)
            return

        self._ready_event.clear()
        self._start_exc = None

        def _run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            started = False
            try:
                loop.run_until_complete(self._start_async())
                started = True
            except Exception as exc:  # pragma: no cover - setup failures are fatal
                self._start_exc = exc
            else:
                self._ready_event.set()
                try:
                    loop.run_forever()
                finally:
                    loop.run_until_complete(self._stop_async())
            finally:
                if not started:
                    self._ready_event.set()
                loop.close()
                self._loop = None

        self._thread = threading.Thread(target=_run, name="RealtimeGraphServer", daemon=True)
        self._thread.start()

        if not self._ready_event.wait(timeout=5):
            raise RuntimeError("RealtimeGraphServer failed to start within 5 seconds")
        if self._start_exc is not None:
            if self._thread is not None:
                self._thread.join(timeout=2)
                self._thread = None
            err = self._start_exc
            self._start_exc = None
            raise RuntimeError("RealtimeGraphServer failed to start") from err

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._loop = None
        self._ready_event.clear()

    def appendHeader(self, headers_line: str) -> None:
        headers_line = f"__time_start {headers_line}"
        headers = [h for h in headers_line.strip().split(" ") if h]
        if not headers:
            raise ValueError("appendHeader requires at least one header value")
        self._last_header = headers[:]
        msg = {"type": "header", "headers": headers}
        self._submit_broadcast(msg)

    def appendData(self, values: List[float]) -> None:
        msg = {"type": "data", "values": [time.time() - self.__start] + values}
        self._submit_broadcast(msg)

    # ----------------------- Internals -----------------------
    def _submit_broadcast(self, msg: dict) -> None:
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
        except RuntimeError:
            # Loop might be shutting down; ignore.
            pass

    async def _start_async(self) -> None:
        self._app = web.Application()
        self._ws_lock = asyncio.Lock()

        self._app.add_routes([
            web.get("/", self._handle_index),
            web.get("/ws", self._handle_ws),
        ])

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._host, port=self._port)
        await self._site.start()
        if self._header_interval > 0:
            self._header_task = asyncio.create_task(self._header_keepalive())

    async def _stop_async(self) -> None:
        if self._header_task is not None:
            self._header_task.cancel()
            try:
                await self._header_task
            except asyncio.CancelledError:
                pass
            self._header_task = None
        # Close sockets
        if self._ws_lock is not None:
            async with self._ws_lock:
                for ws in list(self._sockets):
                    await ws.close()
                self._sockets.clear()

        # Stop web server
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._site = None
        self._app = None

    async def _handle_index(self, request: web.Request) -> web.StreamResponse:
        index_path = self._web_dir / "index.html"
        if not index_path.exists():
            return web.Response(status=404, text="index.html not found")
        return web.FileResponse(path=str(index_path))

    async def _handle_ws(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=15)
        await ws.prepare(request)

        assert self._ws_lock is not None
        async with self._ws_lock:
            self._sockets.add(ws)

        if self._last_header is not None:
            try:
                await ws.send_str(json.dumps({"type": "header", "headers": self._last_header}, separators=(",", ":")))
            except Exception:
                # If the client disconnects immediately, fall back to normal cleanup path
                await ws.close()
                async with self._ws_lock:
                    self._sockets.discard(ws)
                return ws

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # This server is broadcast-only; ignore input.
                    if msg.data == "ping":
                        await ws.send_str("pong")
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            async with self._ws_lock:
                self._sockets.discard(ws)

        return ws

    async def _broadcast(self, msg: dict) -> None:
        text = json.dumps(msg, separators=(",", ":"))
        if self._ws_lock is None:
            return
        async with self._ws_lock:
            dead: List[web.WebSocketResponse] = []
            for ws in self._sockets:
                try:
                    await ws.send_str(text)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._sockets.discard(ws)

    async def _header_keepalive(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._header_interval)
                if self._last_header is None:
                    continue
                await self._broadcast({"type": "header", "headers": self._last_header})
        except asyncio.CancelledError:
            raise


if __name__ == "__main__":
    # Manual run helper
    srv = RealtimeGraphServer()
    srv.start()
    print(f"Serving on http://{srv._host}:{srv._port}")
    try:
        import time
        while True:
            # Keep process alive; data should be appended by other code.
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
