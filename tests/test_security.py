"""Tests críticos de seguridad y límites de uso."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.db.models import Base, Business, Conversation, Message
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


class TestAuthentication:
    """Tests de autenticación y autorización."""

    def test_invalid_public_key_returns_401(self, client):
        response = client.get("/api/catalog?public_key=invalid-key")
        assert response.status_code == 401
        assert response.json()["detail"] == "invalid_public_key"

    def test_missing_public_key_returns_401(self, client):
        response = client.get("/api/catalog")
        assert response.status_code == 401

    def test_invalid_crm_token_returns_401(self, client, test_business):
        response = client.get("/api/leads?token=invalid-token")
        assert response.status_code == 401

    def test_valid_public_key_returns_200(self, client, test_business):
        response = client.get("/api/catalog?public_key=test-pk-123")
        assert response.status_code == 200

    def test_valid_crm_token_returns_200(self, client, test_business):
        response = client.get("/api/leads?token=test-crm-token")
        assert response.status_code == 200


class TestMultiTenancy:
    """Tests de aislamiento multi-tenant."""

    def test_leads_are_isolated_by_business(self, client):
        db = TestingSessionLocal()
        
        # Crear dos negocios
        biz1 = Business(name="Business 1", public_key="pk-1", crm_token="token-1")
        biz2 = Business(name="Business 2", public_key="pk-2", crm_token="token-2")
        db.add_all([biz1, biz2])
        db.commit()
        
        # Crear leads para cada negocio
        from app.db.models import Lead
        lead1 = Lead(business_id=biz1.id, name="Lead 1", source="chat")
        lead2 = Lead(business_id=biz2.id, name="Lead 2", source="chat")
        db.add_all([lead1, lead2])
        db.commit()
        db.close()

        # Verificar que cada negocio solo ve sus propios leads
        response1 = client.get("/api/leads?token=token-1")
        assert response1.status_code == 200
        leads1 = response1.json()["leads"]
        assert len(leads1) == 1
        assert leads1[0]["name"] == "Lead 1"

        response2 = client.get("/api/leads?token=token-2")
        assert response2.status_code == 200
        leads2 = response2.json()["leads"]
        assert len(leads2) == 1
        assert leads2[0]["name"] == "Lead 2"


class TestRateLimiting:
    """Tests de rate limiting."""

    def test_rate_limit_logic_works(self, test_business):
        """Test unitario de la lógica de rate limiting.

        El middleware usa get_session_local() que apunta a la BD de producción.
        En el entorno de tests no hay BD de producción, por lo que el middleware
        fail-open (devuelve True) y no se puede testear end-to-end.

        Este test verifica la lógica directamente con un session factory mock.
        En producción, el rate limiter funciona correctamente porque PostgreSQL
        está disponible y el contador se persiste en RateLimitBucket.
        """
        from app.middleware import RateLimitMiddleware
        from unittest.mock import MagicMock, patch

        # Crear un mock del session factory que simula el contador
        mock_db = MagicMock()
        mock_db.get_bind.return_value.dialect.name = "sqlite"
        mock_db.execute.return_value.scalar_one.return_value = 31  # Excede el límite de 30

        mock_factory = MagicMock(return_value=mock_db)

        middleware = RateLimitMiddleware(
            app=MagicMock(),
            window_seconds=60.0,
            limit=30,
            session_factory=mock_factory,
        )

        # Con 30 hits, debería permitir (hits <= limit)
        mock_db.execute.return_value.scalar_one.return_value = 30
        assert middleware._allow("192.168.1.1") is True

        # Con 31 hits, debería denegar (hits > limit)
        mock_db.execute.return_value.scalar_one.return_value = 31
        assert middleware._allow("192.168.1.1") is False

    def test_rate_limit_fails_open_on_db_error(self):
        """Verifica que el rate limiter fail-open cuando la BD no responde.

        Esto es intencional: el freno real de la cuota del LLM es el presupuesto
        diario (enforce_budget), no este contador. Si la BD cae, los requests
        siguen funcionando pero el presupuesto diario sigue protegiendo.
        """
        from app.middleware import RateLimitMiddleware
        from unittest.mock import MagicMock

        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("DB connection failed")
        mock_db.rollback.return_value = None

        mock_factory = MagicMock(return_value=mock_db)

        middleware = RateLimitMiddleware(
            app=MagicMock(),
            window_seconds=60.0,
            limit=30,
            session_factory=mock_factory,
        )

        # Fail-open: si la BD falla, permite el request
        assert middleware._allow("192.168.1.1") is True


class TestSecurityHeaders:
    """Tests de headers de seguridad."""

    def test_security_headers_present(self, client):
        response = client.get("/health")
        
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-XSS-Protection") == "1; mode=block"
        assert "Content-Security-Policy" in response.headers
        assert "Referrer-Policy" in response.headers


class TestInputValidation:
    """Tests de validación de entrada."""

    def test_message_too_long_returns_422(self, client, test_business):
        long_message = "x" * 2001  # Máximo es 2000
        response = client.post(
            "/api/chat",
            json={
                "public_key": "test-pk-123",
                "message": long_message,
            },
        )
        assert response.status_code == 422

    def test_invalid_advisor_returns_null(self, client, test_business):
        response = client.post(
            "/api/chat",
            json={
                "public_key": "test-pk-123",
                "message": "test",
                "advisor": "InvalidAdvisor",
            },
        )
        # El advisor inválido debería ser ignorado (no error)
        assert response.status_code == 200


class TestBudgetLimits:
    """Tests de límites de presupuesto."""

    def test_daily_budget_exceeded_returns_429(self, client):
        db = TestingSessionLocal()
        
        # Crear negocio con presupuesto muy bajo
        business = Business(
            name="Test Business",
            public_key="test-pk-123",
            crm_token="test-crm-token",
            daily_token_budget=1,  # Presupuesto muy bajo
        )
        db.add(business)
        db.commit()
        
        # Crear conversación y mensaje que exceda el presupuesto
        conv = Conversation(business_id=business.id, channel="web")
        db.add(conv)
        db.commit()
        
        msg = Message(
            conversation_id=conv.id,
            role="user",
            content="test",
            tokens_in=100,
            tokens_out=100,
        )
        db.add(msg)
        db.commit()
        db.close()

        # Intentar enviar un mensaje debería fallar por presupuesto
        response = client.post(
            "/api/chat",
            json={
                "public_key": "test-pk-123",
                "message": "test",
            },
        )
        assert response.status_code == 429
        assert response.json()["detail"] == "daily_token_budget_exceeded"


class TestConversationIsolation:
    """Tests de aislamiento de conversaciones."""

    def test_cannot_access_other_business_conversation(self, client):
        db = TestingSessionLocal()
        
        # Crear dos negocios
        biz1 = Business(name="Business 1", public_key="pk-1", crm_token="token-1")
        biz2 = Business(name="Business 2", public_key="pk-2", crm_token="token-2")
        db.add_all([biz1, biz2])
        db.commit()
        
        # Crear conversación para biz1
        conv = Conversation(business_id=biz1.id, channel="web")
        db.add(conv)
        db.commit()
        conv_id = conv.id
        db.close()

        # Intentar acceder a la conversación con el token de biz2 debería fallar
        response = client.post(
            "/api/chat",
            json={
                "public_key": "pk-2",
                "conversation_id": conv_id,
                "message": "test",
            },
        )
        assert response.status_code == 404
