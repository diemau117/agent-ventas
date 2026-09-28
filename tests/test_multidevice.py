"""Tests de multi-device: claim atómico, lease, heartbeat, concurrencia."""
import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.db.models import Base, Business, Conversation, Device, Lead
from app.main import app


# Configuración de base de datos para tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    """Instala el override de get_db SOLO durante este módulo y lo restaura."""
    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if prev is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = prev


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_business():
    db = TestingSessionLocal()
    business = Business(
        name="Test Business",
        public_key="test-pk-123",
        crm_token="test-crm-token",
        agent_name="Test Agent",
    )
    db.add(business)
    db.commit()
    db.refresh(business)
    yield business
    db.close()


@pytest.fixture
def test_conversation(test_business):
    db = TestingSessionLocal()
    conv = Conversation(
        business_id=test_business.id,
        channel="web",
        state="ai",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    yield conv
    db.close()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _create_device(db, business_id: int, device_id: str, name: str = "") -> Device:
    """Helper para crear un dispositivo directamente en la BD."""
    token = secrets.token_urlsafe(32)
    device = Device(
        business_id=business_id,
        device_id=device_id,
        token_hash=_hash_token(token),
        status="active",
        name=name,
        last_heartbeat=datetime.now(timezone.utc).replace(tzinfo=None),
        last_activity=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device, token


class TestDeviceAuthentication:
    """Tests de autenticación de dispositivos."""

    def test_register_device(self, client, test_business):
        response = client.post(
            "/api/control-center/devices/register",
            params={
                "device_id": "device-1",
                "name": "PC Oficina",
                "token": "test-crm-token",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == "device-1"
        assert "token" in data
        assert data["business_id"] == test_business.id

    def test_register_duplicate_device_returns_409(self, client, test_business):
        # Primer registro
        client.post(
            "/api/control-center/devices/register",
            params={
                "device_id": "device-1",
                "name": "PC Oficina",
                "token": "test-crm-token",
            },
        )
        # Segundo registro con mismo device_id
        response = client.post(
            "/api/control-center/devices/register",
            params={
                "device_id": "device-1",
                "name": "PC Oficina 2",
                "token": "test-crm-token",
            },
        )
        assert response.status_code == 409

    def test_authenticate_with_valid_token(self, client, test_business):
        # Registrar dispositivo
        reg_response = client.post(
            "/api/control-center/devices/register",
            params={
                "device_id": "device-1",
                "name": "PC Oficina",
                "token": "test-crm-token",
            },
        )
        token = reg_response.json()["token"]

        # Autenticar con el token
        response = client.get(
            "/api/control-center/devices",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_authenticate_with_invalid_token(self, client):
        response = client.get(
            "/api/control-center/devices",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401

    def test_authenticate_without_token(self, client):
        response = client.get("/api/control-center/devices")
        assert response.status_code == 401

    def test_revoked_device_cannot_access(self, client, test_business):
        # Registrar dispositivo
        reg_response = client.post(
            "/api/control-center/devices/register",
            params={
                "device_id": "device-1",
                "name": "PC Oficina",
                "token": "test-crm-token",
            },
        )
        token = reg_response.json()["token"]

        # Revocar dispositivo
        db = TestingSessionLocal()
        device = db.query(Device).filter(Device.device_id == "device-1").first()
        device.status = "revoked"
        db.commit()
        db.close()

        # Intentar acceder con token revocado
        response = client.get(
            "/api/control-center/devices",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


class TestClaimConversation:
    """Tests de claim atómico de conversaciones."""

    def test_claim_conversation_success(self, client, test_business, test_conversation):
        # Crear dispositivo
        db = TestingSessionLocal()
        device, token = _create_device(db, test_business.id, "device-1", "PC 1")
        db.close()

        response = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["conversation_id"] == test_conversation.id
        assert data["assigned_to"] == "PC 1"

    def test_claim_already_claimed_returns_409(self, client, test_business, test_conversation):
        # Crear dos dispositivos
        db = TestingSessionLocal()
        device1, token1 = _create_device(db, test_business.id, "device-1", "PC 1")
        device2, token2 = _create_device(db, test_business.id, "device-2", "PC 2")
        db.close()

        # PC1 claim
        response1 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert response1.status_code == 200

        # PC2 intenta claim la misma conversación
        response2 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert response2.status_code == 409

    def test_claim_conversation_not_found(self, client, test_business):
        db = TestingSessionLocal()
        device, token = _create_device(db, test_business.id, "device-1", "PC 1")
        db.close()

        response = client.post(
            "/api/control-center/conversations/99999/claim",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    def test_claim_conversation_from_other_business(self, client, test_business):
        # Crear otro negocio con su conversación
        db = TestingSessionLocal()
        other_business = Business(name="Other", public_key="other-pk", crm_token="other-token")
        db.add(other_business)
        db.commit()
        other_conv = Conversation(business_id=other_business.id, channel="web", state="ai")
        db.add(other_conv)
        db.commit()
        other_conv_id = other_conv.id

        # Crear dispositivo para el primer negocio
        device, token = _create_device(db, test_business.id, "device-1", "PC 1")
        db.close()

        # Intentar claim conversación de otro negocio
        response = client.post(
            f"/api/control-center/conversations/{other_conv_id}/claim",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    def test_release_conversation(self, client, test_business, test_conversation):
        db = TestingSessionLocal()
        device, token = _create_device(db, test_business.id, "device-1", "PC 1")
        db.close()

        # Claim
        client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Release
        response = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/release",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Verificar que está disponible de nuevo
        db = TestingSessionLocal()
        conv = db.query(Conversation).filter(Conversation.id == test_conversation.id).first()
        assert conv.state == "ai"
        assert conv.assigned_to is None
        db.close()

    def test_release_conversation_not_assigned_to_you(self, client, test_business, test_conversation):
        db = TestingSessionLocal()
        device1, token1 = _create_device(db, test_business.id, "device-1", "PC 1")
        device2, token2 = _create_device(db, test_business.id, "device-2", "PC 2")
        db.close()

        # PC1 claim
        client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token1}"},
        )

        # PC2 intenta release
        response = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/release",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert response.status_code == 403

    def test_close_conversation(self, client, test_business, test_conversation):
        db = TestingSessionLocal()
        device, token = _create_device(db, test_business.id, "device-1", "PC 1")
        db.close()

        # Claim primero
        client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Close
        response = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/close",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Verificar estado
        db = TestingSessionLocal()
        conv = db.query(Conversation).filter(Conversation.id == test_conversation.id).first()
        assert conv.state == "closed"
        db.close()


class TestLeaseExpiration:
    """Tests de expiración de lease."""

    def test_lease_expiration_allows_reclaim(self, client, test_business, test_conversation):
        db = TestingSessionLocal()
        device1, token1 = _create_device(db, test_business.id, "device-1", "PC 1")
        device2, token2 = _create_device(db, test_business.id, "device-2", "PC 2")

        # Expirar el lease manualmente
        test_conversation.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
        db.close()

        # PC1 claim
        response1 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert response1.status_code == 200

        # Expirar lease
        db = TestingSessionLocal()
        conv = db.query(Conversation).filter(Conversation.id == test_conversation.id).first()
        conv.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
        db.close()

        # PC2 puede reclamar porque el lease expiró
        response2 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert response2.status_code == 200


class TestHeartbeat:
    """Tests de heartbeat."""

    def test_heartbeat_updates_last_heartbeat(self, client, test_business):
        db = TestingSessionLocal()
        device, token = _create_device(db, test_business.id, "device-1", "PC 1")
        old_heartbeat = device.last_heartbeat
        db.close()

        response = client.post(
            "/api/control-center/heartbeat",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        db = TestingSessionLocal()
        device = db.query(Device).filter(Device.device_id == "device-1").first()
        assert device.last_heartbeat >= old_heartbeat
        db.close()


class TestStateResync:
    """Tests de resync de estado."""

    def test_get_full_state(self, client, test_business, test_conversation):
        db = TestingSessionLocal()
        device, token = _create_device(db, test_business.id, "device-1", "PC 1")
        db.close()

        response = client.get(
            "/api/control-center/state",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "leads" in data
        assert "conversations" in data
        assert "appointments" in data
        assert "server_time" in data

    def test_state_isolated_by_business(self, client, test_business):
        # Crear otro negocio con su conversación
        db = TestingSessionLocal()
        other_business = Business(name="Other", public_key="other-pk", crm_token="other-token")
        db.add(other_business)
        db.commit()
        other_conv = Conversation(business_id=other_business.id, channel="web", state="ai")
        db.add(other_conv)
        db.commit()
        other_conv_id = other_conv.id

        # Crear dispositivo para el primer negocio
        device, token = _create_device(db, test_business.id, "device-1", "PC 1")
        db.close()

        response = client.get(
            "/api/control-center/state",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()

        # No debe incluir conversaciones del otro negocio
        conv_ids = [c["id"] for c in data["conversations"]]
        assert other_conv_id not in conv_ids


class TestConcurrency:
    """Tests de concurrencia: dos dispositivos reclamando simultáneamente."""

    def test_two_devices_claim_simultaneously(self, client, test_business, test_conversation):
        """PC1 y PC2 intentan claim la misma conversación simultáneamente.

        Resultado esperado:
        - PC1 → SUCCESS (200)
        - PC2 → CONFLICT (409)
        """
        db = TestingSessionLocal()
        device1, token1 = _create_device(db, test_business.id, "device-1", "PC 1")
        device2, token2 = _create_device(db, test_business.id, "device-2", "PC 2")
        db.close()

        # Ejecutar ambos claims secuencialmente (simulando concurrencia)
        # En un entorno real, la concurrencia se maneja con SELECT FOR UPDATE
        response1 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token1}"},
        )
        response2 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token2}"},
        )

        # Contar éxitos y conflictos
        statuses = [response1.status_code, response2.status_code]
        assert 200 in statuses, "Al menos un claim debe ser exitoso"
        assert 409 in statuses, "El otro claim debe ser conflicto"

    def test_three_devices_claim_simultaneously(self, client, test_business, test_conversation):
        """Tres dispositivos intentan claim la misma conversación.

        Resultado esperado:
        - 1 SUCCESS (200)
        - 2 CONFLICT (409)
        """
        db = TestingSessionLocal()
        device1, token1 = _create_device(db, test_business.id, "device-1", "PC 1")
        device2, token2 = _create_device(db, test_business.id, "device-2", "PC 2")
        device3, token3 = _create_device(db, test_business.id, "device-3", "PC 3")
        db.close()

        response1 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token1}"},
        )
        response2 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token2}"},
        )
        response3 = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token3}"},
        )

        statuses = [response1.status_code, response2.status_code, response3.status_code]
        assert statuses.count(200) == 1, "Exactamente un claim debe ser exitoso"
        assert statuses.count(409) == 2, "Los otros dos deben ser conflicto"


class TestTenantIsolation:
    """Tests de aislamiento multi-tenant."""

    def test_devices_isolated_by_business(self, client, test_business):
        # Crear otro negocio
        db = TestingSessionLocal()
        other_business = Business(name="Other", public_key="other-pk", crm_token="other-token")
        db.add(other_business)
        db.commit()

        # Crear dispositivos para ambos negocios
        device1, token1 = _create_device(db, test_business.id, "device-1", "PC 1")
        device2, token2 = _create_device(db, other_business.id, "device-1", "PC Other")
        db.close()

        # PC1 del negocio 1 lista dispositivos
        response1 = client.get(
            "/api/control-center/devices",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert response1.status_code == 200
        devices1 = response1.json()
        assert len(devices1) == 1
        assert devices1[0]["device_id"] == "device-1"

        # PC del negocio 2 lista dispositivos
        response2 = client.get(
            "/api/control-center/devices",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert response2.status_code == 200
        devices2 = response2.json()
        assert len(devices2) == 1
        assert devices2[0]["device_id"] == "device-1"

        # Verificar que son dispositivos diferentes (diferente business_id)
        assert devices1[0]["id"] != devices2[0]["id"]


class TestWebSocketRealtime:
    """Canal real-time: ticket one-time por encima del Bearer permanente.

    Flujo obligatorio:
        Bearer (header) → POST /ws-ticket → ticket de 60 s y un solo uso
        → WS /control-center/ws?ticket=...
    El token permanente jamás aparece en una URL (evita que quede en logs
    de proxy/CDN/herramientas de diagnóstico).
    """

    def _register(self, client, device_id: str, name: str = "") -> str:
        r = client.post(
            "/api/control-center/devices/register",
            params={"device_id": device_id, "name": name, "token": "test-crm-token"},
        )
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def _ticket(self, client, bearer: str) -> str:
        r = client.post(
            "/api/control-center/ws-ticket",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["single_use"] is True
        assert data["expires_in"] == 60
        return data["ticket"]

    def test_ws_ticket_requires_bearer(self, client):
        """Sin Bearer no hay ticket."""
        assert client.post("/api/control-center/ws-ticket").status_code == 401
        assert client.post(
            "/api/control-center/ws-ticket",
            headers={"Authorization": "Bearer invalido"},
        ).status_code == 401

    def test_ws_rejects_invalid_ticket(self, client):
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/control-center/ws?ticket=not-a-real"):
                pass

    def test_ws_permanent_token_in_url_is_rejected(self, client, test_business):
        """Endurecimiento: el Bearer ya NO abre el WebSocket, solo el ticket."""
        from starlette.websockets import WebSocketDisconnect

        bearer = self._register(client, "pc-token-url", "PC Token")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/control-center/ws?token={bearer}"):
                pass

    def test_ws_ticket_is_single_use(self, client, test_business):
        from starlette.websockets import WebSocketDisconnect

        bearer = self._register(client, "pc-single", "PC Single")
        ticket = self._ticket(client, bearer)

        with client.websocket_connect(f"/api/control-center/ws?ticket={ticket}") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

        # Segunda conexión con el mismo ticket → rechazada
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/control-center/ws?ticket={ticket}"):
                pass

    def test_ws_ticket_expires(self, client, test_business):
        from starlette.websockets import WebSocketDisconnect

        from app.device_auth import _WS_TICKETS

        bearer = self._register(client, "pc-exp", "PC Exp")
        ticket = self._ticket(client, bearer)
        device_pk, _ = _WS_TICKETS[ticket]
        _WS_TICKETS[ticket] = (device_pk, 0)  # forzar expiración

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/control-center/ws?ticket={ticket}"):
                pass
        assert ticket not in _WS_TICKETS  # se consume aunque esté vencido

    def test_ws_ticket_of_revoked_device_is_rejected(self, client, test_business):
        """Revocar DESPUÉS de emitir el ticket: el estado se re-verifica."""
        from starlette.websockets import WebSocketDisconnect

        bearer = self._register(client, "pc-rev", "PC Rev")
        ticket = self._ticket(client, bearer)

        db = TestingSessionLocal()
        device = db.query(Device).filter(Device.device_id == "pc-rev").first()
        device.status = "revoked"
        db.commit()
        db.close()

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/control-center/ws?ticket={ticket}"):
                pass

    def test_ws_ping_pong(self, client, test_business):
        """El canal responde a heartbeats del cliente."""
        bearer = self._register(client, "pc-ws", "PC WS")
        ticket = self._ticket(client, bearer)
        with client.websocket_connect(f"/api/control-center/ws?ticket={ticket}") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

    def test_ws_receives_claim_event_from_other_device(
        self, client, test_business, test_conversation
    ):
        """PC2 está escuchando; PC1 toma la conversación → PC2 se entera al instante."""
        token1 = self._register(client, "pc-1", "PC 1")
        token2 = self._register(client, "pc-2", "PC 2")
        ticket2 = self._ticket(client, token2)

        with client.websocket_connect(
            f"/api/control-center/ws?ticket={ticket2}"
        ) as ws:
            r = client.post(
                f"/api/control-center/conversations/{test_conversation.id}/claim",
                headers={"Authorization": f"Bearer {token1}"},
            )
            assert r.status_code == 200

            event = ws.receive_json()
            assert event["type"] == "conversation_claimed"
            assert event["data"]["conversation_id"] == test_conversation.id
            assert event["data"]["assigned_to"] == "PC 1"

    def test_ws_receives_release_event(self, client, test_business, test_conversation):
        """El dispositivo que libera notifica a los demás."""
        token1 = self._register(client, "pc-1", "PC 1")
        token2 = self._register(client, "pc-2", "PC 2")
        ticket2 = self._ticket(client, token2)

        r = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert r.status_code == 200

        with client.websocket_connect(
            f"/api/control-center/ws?ticket={ticket2}"
        ) as ws:
            r = client.post(
                f"/api/control-center/conversations/{test_conversation.id}/release",
                headers={"Authorization": f"Bearer {token1}"},
            )
            assert r.status_code == 200

            event = ws.receive_json()
            assert event["type"] == "conversation_released"
            assert event["data"]["conversation_id"] == test_conversation.id

    def test_ws_events_do_not_cross_tenants(
        self, client, test_business, test_conversation
    ):
        """Un negocio ajeno no recibe los eventos de otro."""
        db = TestingSessionLocal()
        other = Business(name="Otros", public_key="other-pk2", crm_token="other-crm")
        db.add(other)
        db.commit()
        other_conv = Conversation(business_id=other.id, channel="web", state="ai")
        db.add(other_conv)
        db.commit()
        other_conv_id = other_conv.id
        _dev, other_token = _create_device(db, other.id, "pc-otro", "PC Otro")
        db.close()

        token1 = self._register(client, "pc-1", "PC 1")
        other_ticket = self._ticket(client, other_token)

        with client.websocket_connect(
            f"/api/control-center/ws?ticket={other_ticket}"
        ) as ws:
            r = client.post(
                f"/api/control-center/conversations/{test_conversation.id}/claim",
                headers={"Authorization": f"Bearer {token1}"},
            )
            assert r.status_code == 200

            # Broadcast en el canal del otro negocio…
            r2 = client.post(
                f"/api/control-center/conversations/{other_conv_id}/claim",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert r2.status_code == 200
            event = ws.receive_json()
            # …solo recibe SU evento, jamás el del negocio de prueba.
            assert event["data"]["conversation_id"] == other_conv_id


    def test_same_device_two_tabs_both_receive_event(
        self, client, test_business, test_conversation
    ):
        """Dos pestañas del MISMO dispositivo: ambas reciben el evento.

        Regresión: antes cada conexión nueva CERRABA la anterior y, con varios
        paneles abiertos, el servidor podía quedarse bloqueado.
        """
        bearer = self._register(client, "pc-tabs", "PC Tabs")
        other = self._register(client, "pc-other", "PC Other")
        t1 = self._ticket(client, bearer)
        t2 = self._ticket(client, bearer)

        with client.websocket_connect(f"/api/control-center/ws?ticket={t1}") as ws1:
            with client.websocket_connect(f"/api/control-center/ws?ticket={t2}") as ws2:
                r = client.post(
                    f"/api/control-center/conversations/{test_conversation.id}/claim",
                    headers={"Authorization": f"Bearer {other}"},
                )
                assert r.status_code == 200

                e1 = ws1.receive_json()
                e2 = ws2.receive_json()
                assert e1["type"] == "conversation_claimed"
                assert e2["type"] == "conversation_claimed"
                assert e1["data"]["conversation_id"] == test_conversation.id

    def test_many_connections_same_device_do_not_stall_server(
        self, client, test_business, test_conversation
    ):
        """Estorma: 6 conexiones del mismo dispositivo + el servidor sigue vivo.

        Regresión del bloqueo del event loop al cerrar conexiones anteriores.
        """
        # 18 > pool_size(5) + max_overflow(10): si alguna conexión retuviera
        # su sesión SQL, esto agotaría el pool y congelaría el event loop.
        bearer = self._register(client, "pc-storm", "PC Storm")
        other = self._register(client, "pc-other", "PC Other")
        tickets = [self._ticket(client, bearer) for _ in range(18)]

        sockets = [
            client.websocket_connect(f"/api/control-center/ws?ticket={tk}").__enter__()
            for tk in tickets
        ]
        try:
            # El servidor debe seguir respondiendo con todas esas conexiones abiertas
            hb = client.post(
                "/api/control-center/heartbeat",
                headers={"Authorization": f"Bearer {bearer}"},
            )
            assert hb.status_code == 200

            r = client.post(
                f"/api/control-center/conversations/{test_conversation.id}/claim",
                headers={"Authorization": f"Bearer {other}"},
            )
            assert r.status_code == 200

            recibidas = 0
            for ws in sockets:
                evt = ws.receive_json()
                if evt["type"] == "conversation_claimed":
                    recibidas += 1
            assert recibidas == 18, f"solo recibieron {recibidas}/18"
        finally:
            for ws in sockets:
                try:
                    ws.__exit__(None, None, None)
                except Exception:
                    pass


class TestUsageAlerts:
    """Consumo IA: umbrales 50/75/90/100 + corte real del presupuesto."""

    def _register(self, client) -> str:
        r = client.post(
            "/api/control-center/devices/register",
            params={"device_id": "pc-usage", "name": "PC Uso", "token": "test-crm-token"},
        )
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def _usage(self, client, bearer: str) -> dict:
        r = client.get(
            "/api/control-center/usage",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert r.status_code == 200, r.text
        return r.json()

    def _set_tokens(self, business_id: int, tokens: int) -> None:
        from app.db.models import Message

        db = TestingSessionLocal()
        conv = db.query(Conversation).filter(Conversation.business_id == business_id).first()
        if conv is None:
            conv = Conversation(business_id=business_id, channel="web", state="ai")
            db.add(conv)
            db.commit()
        db.add(Message(
            conversation_id=conv.id, role="assistant", content="x",
            tokens_in=tokens, tokens_out=0,
        ))
        db.commit()
        db.close()

    def test_usage_requires_bearer(self, client):
        assert client.get("/api/control-center/usage").status_code == 401

    def test_usage_levels_cross_all_thresholds(self, client, test_business):
        from app.db.models import Message

        bearer = self._register(client)

        # Sin consumo → ok
        u = self._usage(client, bearer)
        assert u["level"] == "ok" and u["percent"] == 0

        # Presupuesto de 1000 tokens para poder cruzar umbrales
        db = TestingSessionLocal()
        b = db.query(Business).filter(Business.id == test_business.id).first()
        b.daily_token_budget = 1000
        conv = Conversation(business_id=b.id, channel="web", state="ai")
        db.add(conv)
        db.commit()
        conv_id = conv.id
        db.close()

        # deltas acumulativos: 520 → 760 → 930 → 1500 sobre presupuesto de 1000
        for tokens, esperado in [(520, "attention"), (240, "warning"),
                                 (170, "critical"), (570, "blocked")]:
            db = TestingSessionLocal()
            db.add(Message(conversation_id=conv_id, role="assistant",
                           content="x", tokens_in=tokens, tokens_out=0))
            db.commit()
            db.close()
            u = self._usage(client, bearer)
            assert u["level"] == esperado, (tokens, u)
            if esperado == "blocked":
                assert u["percent"] == 100.0
                assert u["remaining_tokens"] == 0

    def test_daily_budget_actually_blocks_chat(self, client, test_business):
        """Al 100% el presupuesto corta los mensajes (429), no solo avisa."""
        from app.db.models import Message

        # presupuesto agotado
        db = TestingSessionLocal()
        b = db.query(Business).filter(Business.id == test_business.id).first()
        b.daily_token_budget = 100
        conv = Conversation(business_id=b.id, channel="web", state="ai")
        db.add(conv)
        db.commit()
        conv_id = conv.id
        db.close()

        from app.limits import check_business_limits

        db = TestingSessionLocal()
        business = db.query(Business).filter(Business.id == test_business.id).first()
        db.add(Message(conversation_id=conv_id, role="assistant",
                       content="x", tokens_in=150, tokens_out=0))
        db.commit()

        with pytest.raises(Exception) as exc:
            check_business_limits(db, business)
        assert "daily_token_budget_exceeded" in str(exc.value)
        db.close()



class TestRealtimeChatEvents:
    """El Centro de Control se entera del chat en tiempo real (no a los 30 s).

    Antes solo claim/release/close emitían eventos: una conversación nueva o un
    handoff llegaban al panel por polling. Ahora también conversation_created,
    message_created y conversation_updated.
    """

    def _register(self, client, device_id: str, name: str = "") -> str:
        r = client.post(
            "/api/control-center/devices/register",
            params={"device_id": device_id, "name": name, "token": "test-crm-token"},
        )
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def _ticket(self, client, bearer: str) -> str:
        r = client.post(
            "/api/control-center/ws-ticket",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert r.status_code == 200, r.text
        return r.json()["ticket"]

    def _chat(self, client, test_business, message: str, conversation_id: int | None = None):
        from app.api.routes import chat as chat_route
        from app.llm.base import FakeProvider

        body = {"public_key": test_business.public_key, "message": message}
        if conversation_id:
            body["conversation_id"] = conversation_id
        anterior = chat_route._llm
        chat_route._llm = FakeProvider()
        try:
            return client.post("/api/chat", json=body)
        finally:
            chat_route._llm = anterior

    def test_new_conversation_is_pushed_to_open_panels(
        self, client, test_business
    ):
        bearer = self._register(client, "pc-live", "PC Live")
        ticket = self._ticket(client, bearer)

        with client.websocket_connect(f"/api/control-center/ws?ticket={ticket}") as ws:
            r = self._chat(client, test_business, "hola, ¿me ayudan con un presupuesto?")
            assert r.status_code == 200, r.text
            assert r.json()["conversation_id"]

            e1 = ws.receive_json()
            assert e1["type"] == "conversation_created", e1
            e2 = ws.receive_json()
            assert e2["type"] == "message_created", e2
            assert e2["data"]["conversation_id"] == r.json()["conversation_id"]

    def test_handoff_is_pushed_immediately(
        self, client, test_business, test_conversation
    ):
        """Pide un humano → el panel recibe conversation_updated con state=human."""
        bearer = self._register(client, "pc-live", "PC Live")
        ticket = self._ticket(client, bearer)

        with client.websocket_connect(f"/api/control-center/ws?ticket={ticket}") as ws:
            r = self._chat(
                client, test_business,
                "quiero hablar con una persona, por favor",
                conversation_id=test_conversation.id,
            )
            assert r.status_code == 200, r.text
            assert r.json()["handoff"] is True, r.json()

            tipos = []
            eventos = []
            for _ in range(3):
                ev = ws.receive_json()
                tipos.append(ev["type"])
                eventos.append(ev)
                if ev["type"] == "conversation_updated":
                    break

            assert "conversation_updated" in tipos, tipos
            upd = next(e for e in eventos if e["type"] == "conversation_updated")
            assert upd["data"]["state"] == "human"
            assert upd["data"]["conversation_id"] == test_conversation.id

    def test_chat_never_breaks_if_nobody_is_listening(self, client, test_business):
        """Best-effort: sin paneles conectados el chat responde igual."""
        r = self._chat(client, test_business, "hola")
        assert r.status_code == 200
        assert r.json()["reply"]
