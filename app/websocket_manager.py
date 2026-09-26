import uuid
import json
import logging
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger("websocket_manager")


class ConnectionManager:
    def __init__(self):
        # Map company_id -> Set of active WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, company_id: str, websocket: WebSocket):
        await websocket.accept()
        if company_id not in self.active_connections:
            self.active_connections[company_id] = set()
        self.active_connections[company_id].add(websocket)
        logger.info(f"Client connected to company room {company_id}. Total connections: {len(self.active_connections[company_id])}")

    def disconnect(self, company_id: str, websocket: WebSocket):
        if company_id in self.active_connections:
            self.active_connections[company_id].discard(websocket)
            if not self.active_connections[company_id]:
                del self.active_connections[company_id]
        logger.info(f"Client disconnected from company room {company_id}.")

    async def broadcast_to_company(self, company_id: str, message: dict):
        """Broadcast a message only to clients subscribed to a specific company_id."""
        if company_id not in self.active_connections:
            logger.debug(f"No active WebSocket connections for company {company_id}.")
            return

        dead_connections = set()
        payload = json.dumps(message)

        for connection in self.active_connections[company_id]:
            try:
                await connection.send_text(payload)
            except Exception as e:
                logger.warning(f"Error sending message to client in company {company_id}: {e}")
                dead_connections.add(connection)

        # Cleanup stale/dropped connections
        for dead in dead_connections:
            self.disconnect(company_id, dead)

    async def broadcast_to_all(self, message: dict):
        """Broadcast a message to all connected clients across all company rooms."""
        for company_id in list(self.active_connections.keys()):
            await self.broadcast_to_company(company_id, message)


# Singleton instance
ws_manager = ConnectionManager()
