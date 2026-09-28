"""Real-time sync para el Centro de Control vía WebSocket.

Los eventos se broadcast a todos los dispositivos conectados del mismo negocio.
La fuente de verdad sigue siendo PostgreSQL; el WebSocket es solo un canal
de notificación. Si un dispositivo se desconecta, al reconectar hace resync
vía GET /api/control-center/state.

Diseño de la tabla de conexiones:
    business_id -> device_id -> {WebSocket, ...}

Un mismo dispositivo puede tener VARIAS conexiones abiertas (dos pestañas,
el panel en dos monitores, reconexión solapada). Antes cada conexión nueva
cerraba la anterior con `await close()`; con N paneles abiertos eso llegaba
a bloquear el event loop del servidor. Ahora no se cierra nada: se añade al
conjunto y se limpia solo la conexión que se cae.
"""
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.db.models import Business, Device

log = logging.getLogger("realtime")


class ConnectionManager:
    """Gestiona conexiones WebSocket activas por negocio y dispositivo."""

    def __init__(self):
        self._connections: dict[int, dict[int, set[WebSocket]]] = {}

    async def connect(self, business_id: int, device: Device, websocket: WebSocket) -> None:
        """Acepta una conexión WebSocket y la registra (sin tocar las demás)."""
        await websocket.accept()
        bucket = self._connections.setdefault(business_id, {}).setdefault(device.id, set())
        bucket.add(websocket)
        log.info(
            "Device %s (business %s) connected — %d conexión(es) de este dispositivo",
            device.id, business_id, len(bucket),
        )

    def disconnect(self, business_id: int, device_id: int, websocket: WebSocket) -> None:
        """Retira UNA conexión concreta; solo borra el device si queda sin conexiones."""
        conns = self._connections.get(business_id)
        if not conns:
            return
        bucket = conns.get(device_id)
        if bucket and websocket in bucket:
            bucket.discard(websocket)
            if not bucket:
                conns.pop(device_id, None)
        if not conns:
            self._connections.pop(business_id, None)
        log.info("Device %s (business %s) disconnected", device_id, business_id)

    def connected_devices(self, business_id: int) -> int:
        """Cuántos dispositivos distintos tiene el negocio escuchando."""
        return len(self._connections.get(business_id, {}))

    def total_connections(self, business_id: int) -> int:
        """Cuántas conexiones (pestañas/paneles) hay abiertas en total."""
        return sum(len(b) for b in self._connections.get(business_id, {}).values())

    async def broadcast(self, business_id: int, event: dict[str, Any]) -> None:
        """Broadcast un evento a todas las conexiones del negocio."""
        conns = self._connections.get(business_id)
        if not conns:
            return

        message = json.dumps(event, default=str)
        # Copia: los handlers pueden desconectar mientras iteramos.
        targets = [ws for bucket in list(conns.values()) for ws in list(bucket)]
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                await self._drop(business_id, ws)

    async def send_to_device(self, business_id: int, device_db_id: int, event: dict[str, Any]) -> None:
        """Envía un evento a todas las conexiones de un dispositivo."""
        bucket = (self._connections.get(business_id) or {}).get(device_db_id)
        if not bucket:
            return
        message = json.dumps(event, default=str)
        for ws in list(bucket):
            try:
                await ws.send_text(message)
            except Exception:
                await self._drop(business_id, ws)

    async def _drop(self, business_id: int, websocket: WebSocket) -> None:
        """Elimina una conexión muerta (búsqueda por identidad, no por device)."""
        conns = self._connections.get(business_id)
        if not conns:
            return
        for device_id, bucket in list(conns.items()):
            if websocket in bucket:
                bucket.discard(websocket)
                if not bucket:
                    conns.pop(device_id, None)
        if not conns:
            self._connections.pop(business_id, None)


# Singleton global
manager = ConnectionManager()


async def broadcast_event(business_id: int, event_type: str, data: dict[str, Any]) -> None:
    """Helper para broadcast un evento a un negocio."""
    await manager.broadcast(business_id, {
        "type": event_type,
        "data": data,
    })
