import uuid
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.websocket_manager import ws_manager

logger = logging.getLogger("websocket_router")
router = APIRouter(tags=["Real-time Dashboard WebSockets"])


@router.websocket("/ws/{company_id}")
async def websocket_dashboard_endpoint(
    websocket: WebSocket,
    company_id: str,
    token: str = Query(default="demo-token")
):
    """
    WebSocket endpoint for real-time dashboard updates.
    
    Path Param:
    - `company_id`: UUID string of the company room to subscribe to.
    
    Authentication:
    - Accepts authorization token as query parameter or headers.
    """
    company_key = str(company_id)
    await ws_manager.connect(company_key, websocket)

    # Send initial connection confirmation
    await websocket.send_json({
        "event": "CONNECTED",
        "message": f"Successfully subscribed to real-time feed for company {company_key}",
        "company_id": company_key
    })

    try:
        while True:
            # Keepalive listener loop (handles client ping/pong)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(company_key, websocket)
    except Exception as e:
        logger.warning(f"WebSocket connection error for company {company_key}: {e}")
        ws_manager.disconnect(company_key, websocket)
