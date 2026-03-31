"""
Data Logger for AKP2 Project
Daniel Gonzalez
dgonz@mit.edu

Extended with a real time graph server.

Notes:
- The realtime graph server prepends a `__time_start` column (seconds since server start).
- `index.html` is served from disk (not embedded) for easy distribution.

WebSocket Protocol:
- Header: {"type":"header","headers":["__time_start","ch1",...]}
- Meta (optional): {"type":"meta","roles":{"gyroX":"gyro_x","gyroY":"gyro_y","pid":"pid","setpoint":"setpoint"}}
- Data: {"type":"data","values":[t,v1,v2,...]}
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

EMBEDDED_INDEX_HTML_GZ_B64 = "__EMBEDDED_INDEX_HTML_GZ_B64__"


def _get_embedded_index_html() -> Optional[str]:
    """Return embedded index.html content when built as a standalone file."""

    if EMBEDDED_INDEX_HTML_GZ_B64 == "__EMBEDDED_INDEX_HTML_GZ_B64__":
        return None
    try:
        raw = gzip.decompress(base64.b64decode(EMBEDDED_INDEX_HTML_GZ_B64))
        return raw.decode("utf-8")
    except Exception:
        return None


class dataLogger:
    def __init__(self, name: str, host: str = "0.0.0.0", port: int = 8765, web_dir: Optional[Path] = None):
        self.name = name
        self.myData: List[List[Union[float, str]]] = []
        self.header: Optional[str] = None

        self._pendingRoles: Dict[str, str] = {}

        self.graphServer = RealtimeGraphServer(host=host, port=port, web_dir=web_dir)
        self.graphServer.start()

        # Touch the file so it exists.
        Path(name).write_text("")

    def writeOut(self) -> None:
        print("Storing data...\n")
        outTxt: List[str] = []
        for data in self.myData:
            outTxt.append(" ".join(str(e) for e in data))
            outTxt.append("\n")

        with open(self.name, "a", encoding="utf-8") as f:
            f.write("".join(outTxt))
            f.write("\n")

        self.myData = []

    def stop(self) -> None:
        self.graphServer.stop()

    # ---- Convenience configuration (optional) ----
    def addGyroXY(self, gyroXHeader: str = "gyro_x", gyroYHeader: str = "gyro_y") -> None:
        """Mark which headers represent gyro X/Y for the web UI.

        Call this before or after `appendHeader()`.
        """

        self._pendingRoles["gyroX"] = gyroXHeader
        self._pendingRoles["gyroY"] = gyroYHeader
        self.graphServer.appendMeta({"roles": dict(self._pendingRoles)})

    def addPidTuning(self, pidHeader: str = "pid", setpointHeader: str = "setpoint") -> None:
        """Mark which headers represent PID value + setpoint/target."""

        self._pendingRoles["pid"] = pidHeader
        self._pendingRoles["setpoint"] = setpointHeader
        self.graphServer.appendMeta({"roles": dict(self._pendingRoles)})

    # ---- Data publishing ----
    def appendHeader(self, headers: Union[str, List[str]]) -> None:
        """Publish the header list (excluding time).

        Accepts either a whitespace-separated string or a list of strings.
        """

        if isinstance(headers, str):
            headerList = [h for h in headers.strip().split() if h]
        else:
            headerList = [h for h in headers if h]

        self.header = " ".join(headerList)
        self.graphServer.appendHeader(self.header)

        if self._pendingRoles:
            self.graphServer.appendMeta({"roles": dict(self._pendingRoles)})

    def appendData(self, val: Union[str, List[Any]]) -> None:
        """Append either a header (first call) or a numeric row.

        Backwards-compatible behavior:
        - If header is not set and `val` is str or list[str], treat it as headers.
        - Otherwise treat `val` as numeric row (list[float]).

        Graceful degradation:
        - If headers are set and `val` is a string, ignore it (no-op).
        - If data conversion fails, log raw data to file but skip WebSocket.
        """

        if self.header is None:
            if isinstance(val, str):
                self.appendHeader(val)
                return
            if isinstance(val, list) and val and isinstance(val[0], str):
                self.appendHeader([str(v) for v in val])
                return
            raise ValueError("First appendData() call must provide headers")

        # Silently ignore string data when headers are already set
        if isinstance(val, str):
            return

        if not isinstance(val, list):
            raise TypeError("appendData() row must be a list of numeric values")

        # Try to convert to numeric values for WebSocket, but log raw data regardless
        try:
            values: List[float] = [float(v) for v in val]
            self.graphServer.appendData(values)
        except (ValueError, TypeError):
            # Conversion failed - log raw data to file but skip WebSocket/graph
            pass

        self.myData.append([str(v) for v in val])


class WebSocketHandler:
    """Simple WebSocket handler using standard library."""

    MAGIC_STRING = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.closed = False

    @classmethod
    def create_accept_key(cls, key: str) -> str:
        """Generate WebSocket accept key from client key."""

        combined = key + cls.MAGIC_STRING
        sha1 = hashlib.sha1(combined.encode()).digest()
        return base64.b64encode(sha1).decode()

    async def send_text(self, text: str) -> None:
        """Send a text frame."""

        if self.closed:
            return
        try:
            data = text.encode("utf-8")
            length = len(data)

            # Frame format: FIN(1) + RSV(3) + opcode(4) = 1 byte
            # Then mask(1) + payload_len(7) = 1 byte (for lengths < 126)
            if length < 126:
                header = struct.pack(">BB", 0x81, length)  # 0x81 = FIN + TEXT opcode
            elif length < 65536:
                header = struct.pack(">BBH", 0x81, 126, length)
            else:
                header = struct.pack(">BBQ", 0x81, 127, length)

            self.writer.write(header + data)
            await self.writer.drain()
        except Exception:
            self.closed = True
            raise

    async def send_ping(self) -> None:
        """Send a ping frame (keepalive)."""

        if self.closed:
            return
        try:
            # 0x89 = FIN + PING opcode
            ping_header = struct.pack(">BB", 0x89, 0)
            self.writer.write(ping_header)
            await self.writer.drain()
        except Exception:
            self.closed = True
            raise

    async def close(self) -> None:
        """Close the WebSocket connection."""

        if self.closed:
            return
        self.closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass

    async def read_message(self) -> Optional[str]:
        """Read a WebSocket message. Returns None on close/error."""

        try:
            while True:
                header = await self.reader.readexactly(2)
                if len(header) < 2:
                    return None

                fin_opcode = header[0]
                mask_length = header[1]

                opcode = fin_opcode & 0x0F
                if opcode == 0x8:  # Close frame
                    return None

                length = mask_length & 0x7F
                mask = (mask_length & 0x80) != 0

                if length == 126:
                    length_bytes = await self.reader.readexactly(2)
                    length = struct.unpack(">H", length_bytes)[0]
                elif length == 127:
                    length_bytes = await self.reader.readexactly(8)
                    length = struct.unpack(">Q", length_bytes)[0]

                if mask:
                    masking_key = await self.reader.readexactly(4)
                else:
                    masking_key = None

                payload = await self.reader.readexactly(length)

                if mask and masking_key:
                    payload = bytes(payload[i] ^ masking_key[i % 4] for i in range(len(payload)))

                if opcode == 0x1:  # Text frame
                    return payload.decode("utf-8")
                if opcode == 0x9:  # Ping
                    pong_header = struct.pack(">BB", 0x8A, 0)  # 0x8A = FIN + PONG
                    self.writer.write(pong_header)
                    await self.writer.drain()
                    continue
                if opcode == 0xA:  # Pong
                    continue
                # Ignore unsupported control/data frames and keep reading.
                continue
        except Exception:
            return None


class RealtimeGraphServer:
    """Lightweight HTTP + WebSocket server for streaming timeseries.

    Uses only standard library - no external dependencies.

    - Serves a static `index.html` client.
    - Broadcasts header, meta, and data JSON messages over WebSocket.
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

        self._index_html_path = self._web_dir / "index.html"

        self.__start = time.time()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._start_exc: Optional[BaseException] = None

        self._server: Optional[asyncio.Server] = None
        self._sockets: Set[WebSocketHandler] = set()
        self._ws_lock: Optional[asyncio.Lock] = None
        self._header_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._last_header: Optional[List[str]] = None
        self._last_meta: Optional[dict] = None

    # ----------------------- Public API -----------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
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
            except Exception as exc:  # pragma: no cover
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
        headers_line = f"__time_start {headers_line}".strip()
        headers = [h for h in headers_line.split(" ") if h]
        if not headers:
            raise ValueError("appendHeader requires at least one header value")
        self._last_header = headers[:]
        msg = {"type": "header", "headers": headers}
        self._submit_broadcast(msg)

    def appendMeta(self, meta: dict) -> None:
        self._last_meta = dict(meta)
        msg = {"type": "meta", **meta}
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
            pass

    async def _start_async(self) -> None:
        self._ws_lock = asyncio.Lock()

        self._server = await asyncio.start_server(self._handle_connection, self._host, self._port)

        if self._header_interval > 0:
            self._header_task = asyncio.create_task(self._header_keepalive())

        self._ping_task = asyncio.create_task(self._ping_keepalive())

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not line:
                writer.close()
                await writer.wait_closed()
                return

            request_line = line.decode("utf-8", errors="ignore").strip()
            if not request_line.startswith(("GET ", "POST ", "HEAD ")):
                writer.close()
                await writer.wait_closed()
                return

            headers: Dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if not line:
                    break
                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str:
                    break
                if ":" in line_str:
                    key, value = line_str.split(":", 1)
                    headers[key.lower().strip()] = value.strip()

            path = request_line.split()[1] if len(request_line.split()) > 1 else "/"

            if (
                path == "/ws"
                and headers.get("upgrade", "").lower() == "websocket"
                and "sec-websocket-key" in headers
            ):
                await self._handle_ws_upgrade(reader, writer, headers)
            elif path == "/":
                await self._handle_http_index(writer)
            else:
                await self._send_http_response(writer, 404, "Not Found", "text/plain", b"Not Found")
        except Exception:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_http_index(self, writer: asyncio.StreamWriter) -> None:
        try:
            content = self._index_html_path.read_text(encoding="utf-8")
        except Exception:
            content = _get_embedded_index_html()
            if content is None:
                await self._send_http_response(
                    writer,
                    500,
                    "Internal Server Error",
                    "text/plain",
                    b"Missing or unreadable index.html",
                )
                return

        await self._send_http_response(writer, 200, "OK", "text/html", content.encode("utf-8"))

    async def _send_http_response(
        self,
        writer: asyncio.StreamWriter,
        status_code: int,
        status_text: str,
        content_type: str,
        body: bytes,
    ) -> None:
        response = (
            f"HTTP/1.1 {status_code} {status_text}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode() + body
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _handle_ws_upgrade(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, headers: dict) -> None:
        key = headers.get("sec-websocket-key", "")
        if not key:
            await self._send_http_response(writer, 400, "Bad Request", "text/plain", b"Missing Sec-WebSocket-Key")
            return

        accept_key = WebSocketHandler.create_accept_key(key)

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_key}\r\n"
            "\r\n"
        )

        writer.write(response.encode())
        await writer.drain()

        ws = WebSocketHandler(reader, writer)

        async with self._ws_lock:
            self._sockets.add(ws)

        # Send last header/meta if available
        try:
            if self._last_header is not None:
                await ws.send_text(json.dumps({"type": "header", "headers": self._last_header}, separators=(",", ":")))
            if self._last_meta is not None:
                await ws.send_text(json.dumps({"type": "meta", **self._last_meta}, separators=(",", ":")))
        except Exception:
            async with self._ws_lock:
                self._sockets.discard(ws)
            await ws.close()
            return

        try:
            while not ws.closed:
                try:
                    msg = await asyncio.wait_for(ws.read_message(), timeout=30.0)
                    if msg is None:
                        break
                    if msg == "ping":
                        await ws.send_text("pong")
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass
        finally:
            async with self._ws_lock:
                self._sockets.discard(ws)
            await ws.close()

    async def _stop_async(self) -> None:
        if self._header_task is not None:
            self._header_task.cancel()
            try:
                await self._header_task
            except asyncio.CancelledError:
                pass
            self._header_task = None

        if self._ping_task is not None:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

        if self._ws_lock is not None:
            async with self._ws_lock:
                for ws in list(self._sockets):
                    await ws.close()
                self._sockets.clear()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _broadcast(self, msg: dict) -> None:
        text = json.dumps(msg, separators=(",", ":"))
        if self._ws_lock is None:
            return
        async with self._ws_lock:
            dead: List[WebSocketHandler] = []
            for ws in self._sockets:
                try:
                    await ws.send_text(text)
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

    async def _ping_keepalive(self) -> None:
        """Send ping frames to all WebSocket connections every 15 seconds."""

        try:
            while True:
                await asyncio.sleep(15.0)
                if self._ws_lock is None:
                    continue
                async with self._ws_lock:
                    dead: List[WebSocketHandler] = []
                    for ws in self._sockets:
                        try:
                            await ws.send_ping()
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self._sockets.discard(ws)
        except asyncio.CancelledError:
            raise
