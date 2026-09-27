"""Cierre (AUDITORIA_VENTAS P1): la dirección de Jeff llega al prompt del LLM.

Antes Jeff decidía (CLOSE/SCHEDULE/HANDLE_OBJECTION…) y el modelo nunca se
enteraba — el "closer" no cerraba. Ahora cada llamada al LLM lleva el bloque
"## Dirección de este turno (Jeff)" con la acción concreta.
"""
import asyncio

from app.agent.graph import build_graph
from app.agent.prompts import STEP_DIRECTIVES
from app.db.models import Base, Business, Conversation
from app.llm.base import LLMProvider, LLMResult


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    e = create_engine("sqlite:////tmp/opencode/close_directive_test.db",
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


class _Recorder(LLMProvider):
    """Guarda el system prompt de cada llamada y responde con texto."""

    def __init__(self):
        self.systems: list[str] = []

    async def chat(self, messages, tools=None) -> LLMResult:
        self.systems.append(messages[0]["content"])
        if tools:
            return LLMResult(text="ok con tools")
        return LLMResult(text="ok")


def _run(provider, message):
    db, biz_id, conv_id = _db()
    g = build_graph(provider, db)
    out = asyncio.run(g.ainvoke({
        "business_id": biz_id,
        "conversation_id": conv_id,
        "user_message": message,
        "history": [],
        "profile": "",
        "agent_name": "Sofi",
        "blocked": False,
    }))
    db.close()
    return out


def test_directiva_en_system_prompt():
    rec = _Recorder()
    _run(rec, "¿Me pasan los precios?")
    assert "## Dirección de este turno (Jeff)" in rec.systems[0]
    # pricing sin catálogo cargado → EDUCATE (decisión preliminar de Jeff)
    assert STEP_DIRECTIVES["EDUCATE"] in rec.systems[0]


def test_directiva_cubre_todo_el_vocabulario():
    """Cada next_step tiene texto accionable — sin huecos en el cierre."""
    from app.agent.jeff import NEXT_STEPS

    assert set(NEXT_STEPS) == set(STEP_DIRECTIVES)


def test_objecion_recibe_direccion_de_manejo():
    rec = _Recorder()
    _run(rec, "Me parece caro")
    assert STEP_DIRECTIVES["HANDLE_OBJECTION"] in rec.systems[0]
    assert "sin discutir" in rec.systems[0]


def test_conversion_recibe_direccion_de_agenda():
    rec = _Recorder()
    _run(rec, "Agendamos una llamada para el martes")
    assert STEP_DIRECTIVES["SCHEDULE"] in rec.systems[0]
    assert "check_availability" in rec.systems[0]


def test_segunda_llamada_recibe_directiva_final():
    """Con tools, la 2ª llamada reemplaza la preliminar por la decisión final."""
    from app.llm.base import LLMToolCall

    class _ConTools(LLMProvider):
        def __init__(self):
            self.systems: list[str] = []

        async def chat(self, messages, tools=None) -> LLMResult:
            self.systems.append(messages[0]["content"])
            if tools:
                return LLMResult(tool_calls=[LLMToolCall("get_business_info", {}, id="1")])
            return LLMResult(text="segunda")

    rec = _ConTools()
    _run(rec, "¿Qué onda el servicio?")
    assert len(rec.systems) == 2
    # Ambas llamadas llevan directiva (preliminar y final).
    assert "## Dirección de este turno (Jeff)" in rec.systems[0]
    assert "## Dirección de este turno (Jeff)" in rec.systems[1]
