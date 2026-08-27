from fastapi import WebSocket
from typing import Dict, Set
import json
import asyncio
import time


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Set[WebSocket]] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    def is_online(self, user_id: int) -> bool:
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0

    def get_online_user_ids(self) -> list[int]:
        return [uid for uid, conns in self.active_connections.items() if conns]

    async def send_to_user(self, user_id: int, data: dict):
        if user_id not in self.active_connections:
            return
        dead = set()
        for ws in self.active_connections[user_id]:
            try:
                await ws.send_text(json.dumps(data, default=str))
            except Exception:
                dead.add(ws)
        self.active_connections[user_id] -= dead
        if not self.active_connections[user_id]:
            del self.active_connections[user_id]

    async def send_to_chat(self, member_ids: list[int], data: dict, exclude_user: int = None):
        for uid in member_ids:
            if uid != exclude_user:
                await self.send_to_user(uid, data)

    async def broadcast(self, data: dict):
        for uid in list(self.active_connections.keys()):
            await self.send_to_user(uid, data)

    async def _cleanup_stale_connections(self):
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for user_id, connections in list(self.active_connections.items()):
                dead = set()
                for ws in connections:
                    try:
                        await ws.send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        dead.add(ws)
                self.active_connections[user_id] -= dead
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]


manager = ConnectionManager()
