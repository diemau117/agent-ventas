from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # entorno: "dev" | "prod". En prod aplica fail-fast (ver validate_runtime).
    app_env: str = "dev"
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-20b"
    database_url: str = "postgresql+psycopg2://agent@127.0.0.1:5433/agent_ventas"
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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        v = (v or "dev").strip().lower()
        if v not in ("dev", "prod"):
            raise ValueError("APP_ENV debe ser dev o prod")
        return v

    @field_validator("database_url")
    @classmethod
    def _db_url(cls, v: str) -> str:
        """Fuerza psycopg2 para evitar ModuleNotFoundError: psycopg (v3)."""
        if v and v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    def validate_runtime(self) -> None:
        """Fail-fast al arrancar: en prod, key y DB reales son obligatorias.

        Sin esto, un despliegue sin GROQ_API_KEY caería en silencio a
        FakeProvider y el bot "respondería" con datos inventados.
        """
        if not self.is_prod:
            return
        problems = []
        if not self.groq_api_key:
            problems.append("GROQ_API_KEY vacía en APP_ENV=prod")
        if "127.0.0.1" in self.database_url or "localhost" in self.database_url:
            problems.append("DATABASE_URL apunta a localhost en APP_ENV=prod")
        if not self.allow_fake_llm and not self.groq_api_key:
            problems.append("ALLOW_FAKE_LLM=false sin GROQ_API_KEY")
        if problems:
            raise RuntimeError("Configuración de producción inválida: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
