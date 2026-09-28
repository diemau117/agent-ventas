"""Real-time sync para el Centro de Control vía WebSocket.

Los eventos se broadcast a todos los dispositivos conectados del mismo negocio.
La fuente de verdad sigue siendo PostgreSQL; el WebSocket es solo un canal
de notificación. Si un dispositivo se desconecta, al reconectar hace resync
vía GET /api/control-center/state.
"""
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.db.models import Business, Device

log = logging.getLogger("realtime")


class ConnectionManager:
    """Gestiona conexiones WebSocket activas por negocio.

    Estructura:
    {
        business_id: {
            device_id: WebSocket,
            ...
        },
        ...
    }
    """

    def __init__(self):
        self._connections: dict[int, dict[int, WebSocket]] = {}

    async def connect(self, business_id: int, device: Device, websocket: WebSocket) -> None:
        """Acepta una nueva conexión WebSocket."""
        await websocket.accept()
        if business_id not in self._connections:
            self._connections[business_id] = {}
        # Si ya existe una conexión para este dispositivo, la reemplazamos
        # (un dispositivo no debería tener dos conexiones simultáneas)
        old_ws = self._connections[business_id].get(device.id)
        if old_ws:
            try:
                await old_ws.close()
            except Exception:
                pass
        self._connections[business_id][device.id] = websocket
        log.info(f"Device {device.id} (business {business_id}) connected")

    def disconnect(self, business_id: int, device_id: int) -> None:
        """Desconecta un dispositivo."""
        if business_id in self._connections:
            self._connections[business_id].pop(device_id, None)
            if not self._connections[business_id]:
                del self._connections[business_id]
            log.info(f"Device {device_id} (business {business_id}) disconnected")

    async def broadcast(self, business_id: int, event: dict[str, Any]) -> None:
        """Broadcast un evento a todos los dispositivos del negocio."""
        if business_id not in self._connections:
            return

        message = json.dumps(event, default=str)
        disconnected = []

        for device_id, ws in self._connections[business_id].items():
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(device_id)

        # Limpiar conexiones muertas
        for device_id in disconnected:
            self._connections[business_id].pop(device_id, None)

    async def send_to_device(self, business_id: int, device_db_id: int, event: dict[str, Any]) -> None:
        """Envía un evento a un dispositivo específico."""
        if business_id not in self._connections:
            return
        ws = self._connections[business_id].get(device_db_id)
        if ws:
            try:
                await ws.send_text(json.dumps(event, default=str))
            except Exception:
                pass


# Singleton global
manager = ConnectionManager()


async def broadcast_event(business_id: int, event_type: str, data: dict[str, Any]) -> None:
    """Helper para broadcast un evento a un negocio."""
    await manager.broadcast(business_id, {
        "type": event_type,
        "data": data,
    })
