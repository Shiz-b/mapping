import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from watchfiles import Change, awatch

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
UPDATES_DIR = ROOT / "data" / "updates"


class UpdatesFanout:
    """Single directory watch; broadcast parsed update payloads to all SSE subscribers."""

    def __init__(self, updates_dir: Path) -> None:
        self._updates_dir = updates_dir
        self._queues: List[asyncio.Queue[Optional[Dict[str, Any]]]] = []
        self._watch_task: Optional[asyncio.Task[None]] = None
        self._sent_paths: Set[str] = set()

    def _ensure_watch_task(self) -> None:
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.create_task(self._watch_loop(), name="updates-watch")

    async def _watch_loop(self) -> None:
        self._updates_dir.mkdir(parents=True, exist_ok=True)
        try:
            async for changes in awatch(self._updates_dir, recursive=False):
                paths: List[Path] = []
                for change, raw_path in changes:
                    if change == Change.deleted:
                        continue
                    p = Path(raw_path)
                    if p.suffix.lower() != ".json":
                        continue
                    paths.append(p)

                for path in paths:
                    key = str(path.resolve())
                    payload = await self._try_read_update(path)
                    if payload is None:
                        continue
                    if key in self._sent_paths:
                        continue
                    self._sent_paths.add(key)
                    await self._broadcast({"event": "nodes_added", "data": payload})
                    if len(self._sent_paths) > 5000:
                        self._sent_paths.clear()
        except asyncio.CancelledError:
            logger.info("updates watch cancelled")
            raise

    async def _try_read_update(self, path: Path) -> Optional[List[Any]]:
        await asyncio.sleep(0.05)
        for _ in range(5):
            try:
                text = path.read_text(encoding="utf-8")
                data = json.loads(text)
                if isinstance(data, list):
                    return data
                return None
            except (json.JSONDecodeError, OSError):
                await asyncio.sleep(0.05)
        return None

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        for q in list(self._queues):
            await q.put(message)

    def subscribe(self) -> asyncio.Queue[Optional[Dict[str, Any]]]:
        q: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._queues.append(q)
        self._ensure_watch_task()
        return q

    def unsubscribe(self, q: asyncio.Queue[Optional[Dict[str, Any]]]) -> None:
        if q in self._queues:
            self._queues.remove(q)
        if not self._queues and self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None


fanout = UpdatesFanout(UPDATES_DIR)

app = FastAPI()


def _format_sse(event: str, data: str) -> bytes:
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


@app.get("/stream")
async def stream() -> StreamingResponse:
    async def event_iterator() -> AsyncIterator[bytes]:
        queue = fanout.subscribe()
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield _format_sse("heartbeat", json.dumps({"t": int(time.time())}))
                    continue
                if msg is None:
                    break
                if msg.get("event") == "nodes_added":
                    yield _format_sse("nodes_added", json.dumps(msg["data"]))
        finally:
            fanout.unsubscribe(queue)

    return StreamingResponse(
        event_iterator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(ROOT / "index.html", media_type="text/html")


@app.get("/data/nodes.json")
async def serve_nodes() -> FileResponse:
    path = ROOT / "data" / "nodes.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="nodes.json not found")
    return FileResponse(path, media_type="application/json")


app.mount("/", StaticFiles(directory=str(ROOT), html=False), name="static")
