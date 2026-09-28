from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # entorno: "dev" | "prod". En prod aplica fail-fast (ver validate_runtime).
    # Acepta tanto APP_ENV como ENVIRONMENT (Render usa ENVIRONMENT por defecto).
    app_env: str = Field(default="dev", validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"))
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-20b"
    database_url: str = "postgresql://agent@127.0.0.1:5433/agent_ventas"
    max_history: int = 8

    # Rate limit y presupuesto (spec §21).
    rate_limit_per_min: int = 30
    daily_token_budget: int = 200_000
    # true SOLO detrás de un proxy de confianza (cloudflared / Worker de la
    # landing). Ahí el peer del socket es 127.0.0.1 para todos los visitantes
    # y el rate limit se volvería un bucket global de 30/min.
    trust_proxy_headers: bool = False

    # Seguridad: permite operar sin API key SOLO fuera de prod (FakeProvider).
    allow_fake_llm: bool = True

    # Búsqueda externa (spec §14).
    external_search_enabled: bool = True

    # Chatwoot por defecto; cada negocio puede sobreescribir en BD.
    chatwoot_url: str = ""
    chatwoot_token: str = ""

    # CORS/origen del widget.
    allowed_origins: str = "*"

    # Clave pública del tenant dueño de esta landing. El servidor la inyecta
    # en index.html (window.AGENT_VENTAS_KEY) para que el widget sepa a qué
    # negocio consultar sin exponer business_id. Vacío = sin inyectar (el
    # widget acepta ?key=... en la URL como alternativa).
    landing_public_key: str = ""

    # CRM de lectura (GET /api/leads, /api/appointments): token de operador.
    # Vacío = endpoints deshabilitados (403 crm_disabled).
    crm_token: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_starter: str = ""
    stripe_price_pro: str = ""
    stripe_price_enterprise: str = ""

    # Twilio (WhatsApp)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""

    # Admin API Key — protege endpoints administrativos (/api/onboarding, /api/init-db)
    # Sin esta key, esos endpoints devuelven 403. En producción es obligatoria.
    admin_api_key: str = ""

    # Device lease/heartbeat (multi-device Control Center)
    device_lease_duration_seconds: int = 300  # 5 minutos
    device_heartbeat_interval_seconds: int = 60  # 1 minuto

    # Agent hard limits — previenen loops infinitos
    max_tool_calls_per_turn: int = 10
    max_agent_steps: int = 20

    # Message limit por conversación (backend-controlled)
    max_messages_per_conversation: int = 100
    max_tokens_per_conversation: int = 50000
    max_active_conversations_per_business: int = 1000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        """Acepta cualquier valor. Solo 'prod' activa fail-fast.

        Render manda ENVIRONMENT=production, pero el código espera 'prod'.
        Para no fallar, aceptamos cualquier valor y solo usamos 'prod' para
        fail-fast. Todo lo demás se trata como 'dev'.
        """
        v = (v or "dev").strip().lower()
        if v in ("prod", "production"):
            return "prod"
        return "dev"

    @field_validator("database_url")
    @classmethod
    def _db_url(cls, v: str) -> str:
        """Limpia la URL de base de datos.

        No agregamos sslmode aquí — se pasa como connect_arg en database.py
        para que psycopg2 lo maneje correctamente (evita "SSL connection has
        been closed unexpectedly").
        """
        return v

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    def validate_runtime(self) -> None:
        """Fail-fast al arrancar: en prod, key real, DB real y LLM real obligatorios.

        Sin esto, un despliegue sin GROQ_API_KEY caería en silencio a
        FakeProvider y el bot "respondería" con datos inventados.

        Regla dura: **production + ALLOW_FAKE_LLM=true → NO ARRANCA**.
        No es un warning: es una clase completa de error humano (un deploy
        olvidando una variable) eliminada de raíz. Si alguna vez se necesita
        el fake en un entorno parecido a prod, se usa APP_ENV=dev.
        """
        if not self.is_prod:
            return
        problems = []
        if not self.groq_api_key:
            problems.append("GROQ_API_KEY vacía en APP_ENV=prod")
        if self.allow_fake_llm:
            problems.append(
                "ALLOW_FAKE_LLM=true en APP_ENV=prod — producción exige el LLM real "
                "(define ALLOW_FAKE_LLM=false y GROQ_API_KEY)"
            )
        if "127.0.0.1" in self.database_url or "localhost" in self.database_url:
            problems.append("DATABASE_URL apunta a localhost en APP_ENV=prod")
        if self.database_url.startswith("sqlite"):
            problems.append(
                "DATABASE_URL es SQLite en APP_ENV=prod — producción usa PostgreSQL "
                "(SQLite es un archivo local: se pierde entre despliegues)"
            )
        if problems:
            raise RuntimeError("Configuración de producción inválida: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
