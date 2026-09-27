"""Regresión P0 (AUDITORIA_VENTAS): el texto del LLM sin tools NO es fallback.

Caso observado en vivo: objeciones, comparación y descubrimiento hacen que el
modelo responda en crudo (sin tool calls). graph.py trataba `tool_results == {}`
como "todos los tools fallaron" (any([]) == False) y descartaba la respuesta.
"""
import asyncio

from app.agent.graph import build_graph
from app.agent.prompts import FALLBACK_NO_INFO
from app.db.models import Base, Business, Conversation
from app.llm.base import LLMProvider, LLMResult


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    e = create_engine("sqlite:////tmp/opencode/graph_fallback_test.db",
                      connect_args={"check_same_thread": False})
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    db = sessionmaker(bind=e)()
    b = Business(name="A", description="d")
    db.add(b)
    db.commit()
    conv = Conversation(business_id=b.id)
    db.add(conv)
    db.commit()
    return db, b.id, conv.id


class FakeProviderSinTools(LLMProvider):
    """El modelo responde en crudo, sin tool calls: el caso de las objeciones."""

    def __init__(self, text: str):
        self.text = text

    async def chat(self, messages, tools=None) -> LLMResult:
        return LLMResult(text=self.text)


class FakeProviderToolsQueFallan(LLMProvider):
    """Toda tool que se ejecuta devuelve {"error": ...} (H8 real)."""

    async def chat(self, messages, tools=None) -> LLMResult:
        from app.llm.base import LLMToolCall

        # 1ª llamada (con tools): pide get_customer, que falla sin cliente.
        if tools:
            return LLMResult(tool_calls=[LLMToolCall("get_customer", {}, id="1")])
        return LLMResult(text="nunca debería llegar a la 2ª llamada")


def _invoke(provider, message, intent_hint=""):
    db, biz_id, conv_id = _db()
    g = build_graph(provider, db)
    out = asyncio.run(g.ainvoke({
        "business_id": biz_id,
        "conversation_id": conv_id,
        "user_message": message,
        "history": [],
        "profile": "",
        "agent_name": "Sofi",
        "intent": intent_hint,
        "blocked": False,
    }))
    db.close()
    return out


def test_llm_sin_tools_usa_su_texto_no_fallback():
    """Objeción/comparación: el LLM responde sin tools → se usa su texto."""
    texto_bueno = ("Entiendo tu preocupación por el precio. "
                   "¿Podrías contarme más de tu negocio?")
    out = _invoke(FakeProviderSinTools(texto_bueno), "Me parece caro")

    assert out["draft"] == texto_bueno
    assert out["draft"] != FALLBACK_NO_INFO
    assert out["reply"] == texto_bueno  # pasó por verify (sin montos → aprueba)


def test_tools_existen_y_todas_fallan_si_usa_fallback():
    """H8 real: tools ejecutadas y TODAS con error → fallback."""
    out = _invoke(FakeProviderToolsQueFallan(), "¿Qué onda el servicio?")

    assert out["tool_results"]
    assert all("error" in v for v in out["tool_results"].values())
    assert out["reply"] == FALLBACK_NO_INFO
