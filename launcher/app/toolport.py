"""The single public port that standalone tool UIs are served on.

Declaring one RunPod HTTP port per tool produced ports that were permanently
"Initializing" because nothing ever listened on them. Instead this proxy owns
port 7860 for the whole life of the Pod: it always answers, and it forwards to
whichever tool is currently running. Tools bind to a private loopback port and
still see requests at "/", so Gradio and Streamlit need no root-path setup.

ComfyUI is deliberately not routed through here - it keeps listening directly
on its own public port 8188.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

BODYLESS_METHODS = {"GET", "HEAD", "OPTIONS", "DELETE", "TRACE"}

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

PLACEHOLDER = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>AI Lab — порт инструментов</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#11141a;color:#e6e9ef;margin:0;
      display:flex;min-height:100vh;align-items:center;justify-content:center}}
 main{{max-width:34rem;padding:2rem;text-align:center}}
 h1{{font-size:1.4rem;margin:0 0 .75rem}}
 p{{color:#9aa3b2;line-height:1.6}}
 a{{color:#7aa2f7}}
</style></head>
<body><main>
<h1>Порт свободен</h1>
<p>{message}</p>
<p><a href="{launcher}">Открыть AI Lab Launcher</a></p>
</main></body></html>
"""


class RouteTable:
    def __init__(self, route_file: Path):
        self.route_file = route_file

    def current(self) -> dict[str, object] | None:
        try:
            raw = json.loads(self.route_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw or None


class ToolPortProxy:
    """ASGI app: health endpoint, placeholder page, HTTP and WebSocket proxy."""

    def __init__(self, route_file: Path, launcher_url: str = "/"):
        self.routes = RouteTable(route_file)
        self.launcher_url = launcher_url
        self._client: httpx.AsyncClient | None = None

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(scope, receive, send)
        elif scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
        else:
            await self._http(scope, receive, send)

    async def _lifespan(self, scope, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                self._client = httpx.AsyncClient(timeout=None, follow_redirects=False)
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._client:
                    await self._client.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    # ------------------------------------------------------------------ http

    async def _http(self, scope, receive, send) -> None:
        path = scope.get("path", "/")
        if path == "/__ai_lab_health":
            route = self.routes.current()
            body = json.dumps(
                {"status": "ok", "tool": (route or {}).get("tool_id"), "port": (route or {}).get("port")},
                ensure_ascii=False,
            ).encode()
            await self._respond(send, 200, [(b"content-type", b"application/json")], body)
            return

        route = self.routes.current()
        if not route:
            body = PLACEHOLDER.format(
                message="Ни один standalone-инструмент сейчас не запущен. "
                "Запустите его в Launcher — интерфейс появится на этом же адресе.",
                launcher=self.launcher_url,
            ).encode()
            await self._respond(send, 503, [(b"content-type", b"text/html; charset=utf-8")], body)
            return

        client = self._client or httpx.AsyncClient(timeout=None, follow_redirects=False)
        target = f"http://127.0.0.1:{route['port']}{scope.get('raw_path', path.encode()).decode()}"
        query = scope.get("query_string", b"").decode()
        if query:
            target = f"{target}?{query}"

        method = scope["method"].upper()
        # A GET forwarded with a streaming body goes out chunked, which some
        # upstream servers reject outright.
        body = None if method in BODYLESS_METHODS else self._body_stream(receive)
        headers = [
            (key.decode(), value.decode())
            for key, value in scope.get("headers", [])
            if key.decode().lower() not in HOP_BY_HOP
            and not (body is None and key.decode().lower() == "content-length")
        ]
        headers.append(("x-forwarded-proto", scope.get("scheme", "http")))
        headers.append(("x-ai-lab-tool", str(route.get("tool_id", ""))))

        # Keeping the client's content-length makes httpx reuse the original
        # framing instead of re-encoding the body as chunked, which would
        # otherwise require buffering entire uploads in memory to measure them.
        request = client.build_request(
            method,
            target,
            headers=headers,
            content=body,
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            body = PLACEHOLDER.format(
                message=f"{route.get('name', route.get('tool_id'))} ещё не принимает запросы: {exc}",
                launcher=self.launcher_url,
            ).encode()
            await self._respond(send, 502, [(b"content-type", b"text/html; charset=utf-8")], body)
            return

        # aiter_raw() forwards the body exactly as received, still compressed,
        # so content-length and content-encoding both stay accurate.
        out_headers = [
            (key.encode(), value.encode())
            for key, value in response.headers.multi_items()
            if key.lower() not in HOP_BY_HOP
        ]
        await send({"type": "http.response.start", "status": response.status_code, "headers": out_headers})
        try:
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        finally:
            await response.aclose()
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _body_stream(self, receive):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            if body:
                yield body
            if not message.get("more_body", False):
                return

    async def _respond(self, send, status: int, headers, body: bytes) -> None:
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    # ------------------------------------------------------------- websocket

    async def _websocket(self, scope, receive, send) -> None:
        import websockets

        route = self.routes.current()
        if not route:
            await send({"type": "websocket.close", "code": 1013})
            return

        path = scope.get("raw_path", scope.get("path", "/").encode()).decode()
        query = scope.get("query_string", b"").decode()
        target = f"ws://127.0.0.1:{route['port']}{path}"
        if query:
            target = f"{target}?{query}"

        await receive()  # websocket.connect
        subprotocols = scope.get("subprotocols") or []
        try:
            upstream = await websockets.connect(
                target,
                subprotocols=subprotocols or None,
                open_timeout=20,
                max_size=None,
            )
        except Exception:  # noqa: BLE001 - any failure means "tool not reachable"
            await send({"type": "websocket.close", "code": 1011})
            return

        await send({"type": "websocket.accept"})
        import asyncio

        async def client_to_upstream() -> None:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    await upstream.close()
                    return
                if (text := message.get("text")) is not None:
                    await upstream.send(text)
                elif (data := message.get("bytes")) is not None:
                    await upstream.send(data)

        async def upstream_to_client() -> None:
            async for message in upstream:
                if isinstance(message, str):
                    await send({"type": "websocket.send", "text": message})
                else:
                    await send({"type": "websocket.send", "bytes": message})
            await send({"type": "websocket.close", "code": 1000})

        tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await upstream.close()


def create_tool_port_app(settings=None):
    from .config import Settings

    settings = settings or Settings.from_env()
    # Deliberately no ensure_runtime() here: the proxy only ever reads the route
    # file, and importing this module must not touch the filesystem.
    return ToolPortProxy(
        settings.state_dir / "tool-route.json",
        launcher_url=settings.public_port_url(settings.launcher_port),
    )


app = create_tool_port_app()
