"""Fail-fast de producción: el servidor se niega a arrancar si algo está mal.

Reglas verificadas aquí:
- prod + ALLOW_FAKE_LLM=true  → RuntimeError (nunca FakeProvider en producción)
- prod + GROQ_API_KEY vacía   → RuntimeError
- prod + DATABASE_URL local   → RuntimeError
- dev (cualquier configuración) → arranca (fail-fast solo aplica a prod)
"""
import pytest

from app.config import Settings


def _prod(**kw) -> Settings:
    base = dict(
        app_env="prod",
        groq_api_key="gsk_real_key",
        database_url="postgresql://user:pass@db.example.com/agent_ventas",
        allow_fake_llm=False,
    )
    base.update(kw)
    return Settings(**base)


class TestProdFailFast:
    def test_prod_valido_arranca(self):
        _prod().validate_runtime()  # no debe lanzar

    def test_prod_con_fake_llm_no_arranca(self):
        """LA regla: production + fake_llm → FAIL FAST."""
        s = _prod(allow_fake_llm=True)
        with pytest.raises(RuntimeError) as exc:
            s.validate_runtime()
        assert "ALLOW_FAKE_LLM" in str(exc.value)
        assert "ALLOW_FAKE_LLM=true" in str(exc.value)

    def test_prod_sin_groq_key_no_arranca(self):
        with pytest.raises(RuntimeError) as exc:
            _prod(groq_api_key="").validate_runtime()
        assert "GROQ_API_KEY" in str(exc.value)

    def test_prod_con_db_local_no_arranca(self):
        with pytest.raises(RuntimeError) as exc:
            _prod(database_url="postgresql://u:p@localhost:5432/db").validate_runtime()
        assert "localhost" in str(exc.value) or "127.0.0.1" in str(exc.value)

    def test_prod_con_sqlite_no_arranca(self):
        """SQLite en prod = datos que se pierden entre despliegues → fail fast."""
        with pytest.raises(RuntimeError) as exc:
            _prod(database_url="sqlite:////tmp/x.db").validate_runtime()
        assert "SQLite" in str(exc.value)

    def test_dev_con_fake_llm_arranca(self):
        """En dev el fake LLM es legítimo: no hay fail-fast."""
        Settings(app_env="dev", allow_fake_llm=True, groq_api_key="").validate_runtime()

    def test_prod_reporta_todos_los_problemas_juntos(self):
        s = _prod(app_env="prod", groq_api_key="", allow_fake_llm=True,
                  database_url="sqlite:////tmp/x.db")
        with pytest.raises(RuntimeError) as exc:
            s.validate_runtime()
        msg = str(exc.value)
        assert "GROQ_API_KEY" in msg and "ALLOW_FAKE_LLM" in msg and "SQLite" in msg
