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
    """Tests del canal real-time: WebSocket por negocio + auth de dispositivo."""

    def _register(self, client, device_id: str, name: str = "") -> str:
        r = client.post(
            "/api/control-center/devices/register",
            params={"device_id": device_id, "name": name, "token": "test-crm-token"},
        )
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def test_ws_invalid_token_is_rejected(self, client):
        """Un token de dispositivo inválido no abre el WebSocket."""
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/control-center/ws?token=not-a-real-token"
            ):
                pass

    def test_ws_ping_pong(self, client, test_business):
        """El canal responde a heartbeats del cliente."""
        token = self._register(client, "pc-ws", "PC WS")
        with client.websocket_connect(
            f"/api/control-center/ws?token={token}"
        ) as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

    def test_ws_receives_claim_event_from_other_device(
        self, client, test_business, test_conversation
    ):
        """PC2 está escuchando; PC1 toma la conversación → PC2 se entera al instante."""
        token1 = self._register(client, "pc-1", "PC 1")
        token2 = self._register(client, "pc-2", "PC 2")

        with client.websocket_connect(
            f"/api/control-center/ws?token={token2}"
        ) as ws:
            # PC1 toma la conversación vía HTTP
            r = client.post(
                f"/api/control-center/conversations/{test_conversation.id}/claim",
                headers={"Authorization": f"Bearer {token1}"},
            )
            assert r.status_code == 200

            # PC2 recibe el evento de inmediato
            event = ws.receive_json()
            assert event["type"] == "conversation_claimed"
            assert event["data"]["conversation_id"] == test_conversation.id
            assert event["data"]["assigned_to"] == "PC 1"

    def test_ws_receives_release_event(self, client, test_business, test_conversation):
        """El dispositivo que libera notifica a los demás."""
        token1 = self._register(client, "pc-1", "PC 1")
        token2 = self._register(client, "pc-2", "PC 2")

        r = client.post(
            f"/api/control-center/conversations/{test_conversation.id}/claim",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert r.status_code == 200

        with client.websocket_connect(
            f"/api/control-center/ws?token={token2}"
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
        # Otro negocio con su dispositivo
        db = TestingSessionLocal()
        other = Business(name="Otros", public_key="other-pk2", crm_token="other-crm")
        db.add(other)
        db.commit()
        other_conv = Conversation(business_id=other.id, channel="web", state="ai")
        db.add(other_conv)
        db.commit()
        other_conv_id = other_conv.id
        other_token_row = _create_device(db, other.id, "pc-otro", "PC Otro")
        db.close()
        other_token = other_token_row[1]

        token1 = self._register(client, "pc-1", "PC 1")

        with client.websocket_connect(
            f"/api/control-center/ws?token={other_token}"
        ) as ws:
            # El negocio de prueba toma su conversación
            r = client.post(
                f"/api/control-center/conversations/{test_conversation.id}/claim",
                headers={"Authorization": f"Bearer {token1}"},
            )
            assert r.status_code == 200

            # El negocio ajeno no debe recibir nada: reclamamos su propia
            # conversación para forzar un broadcast en su canal.
            r2 = client.post(
                f"/api/control-center/conversations/{other_conv_id}/claim",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert r2.status_code == 200
            event = ws.receive_json()
            # Solo el evento de SU negocio, jamás el del negocio de prueba.
            assert event["data"]["conversation_id"] == other_conv_id
