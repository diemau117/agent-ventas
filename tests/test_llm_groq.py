"""GroqProvider: costo real con cache de prompt + reasoning_effort barato."""
import httpx
import pytest

import app.llm.groq as groq
from app.llm.groq import GroqProvider, _body_has_nudge


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import json as _json

            req = httpx.Request("POST", "https://api.groq.com")
            resp = httpx.Response(
                self.status_code, request=req, content=_json.dumps(self._payload).encode()
            )
            raise httpx.HTTPStatusError("err", request=req, response=resp)

    def json(self):
        return self._payload


class _Client:
    """Captura el body enviado y consume la cola de respuestas."""

    last_body: dict | None = None
    bodies: list = []
    responses: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, timeout=None):
        _Client.last_body = json
        _Client.bodies.append(json)
        return _Client.responses.pop(0)


def _reply(text="", tool_calls=None, prompt=0, completion=0, cached=None):
    usage = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return _Resp(
        {"choices": [{"message": {"content": text, "tool_calls": tool_calls or []}}], "usage": usage}
    )


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    _Client.last_body = None
    _Client.bodies = []
    _Client.responses = []
    monkeypatch.setattr(groq.httpx, "AsyncClient", _Client)
    yield


@pytest.mark.asyncio
async def test_reasoning_effort_low_se_envia():
    _Client.responses = [_reply(text="hola")]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    await p.chat([{"role": "user", "content": "hola"}])
    assert _Client.last_body["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_cost_est_aplica_descuento_de_cache():
    # 1000 tok in (400 cacheados) + 500 out; precios por 1K tok:
    # 0.6*0.000075 + 0.4*0.000037 + 0.5*0.0003
    _Client.responses = [_reply(text="ok", prompt=1000, completion=500, cached=400)]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    r = await p.chat([{"role": "user", "content": "hola"}])
    assert r.usage["input"] == 1000
    assert r.usage["output"] == 500
    assert r.usage["cost_est"] == pytest.approx(0.6 * 0.000075 + 0.4 * 0.000037 + 0.5 * 0.0003)


@pytest.mark.asyncio
async def test_cost_est_sin_cache_cobra_input_completo():
    _Client.responses = [_reply(prompt=2000)]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    r = await p.chat([{"role": "user", "content": "hola"}])
    assert r.usage["cost_est"] == pytest.approx(2 * 0.000075)


@pytest.mark.asyncio
async def test_400_tool_use_failed_se_reintenta():
    # El modelo emite un tool call en una llamada sin tools: Groq devuelve
    # 400 tool_use_failed. El reintento agrega el nudge y devuelve texto.
    _Client.responses = [
        _Resp(
            {"error": {"message": "Tool choice is none, but model called a tool",
                       "code": "tool_use_failed"}},
            status=400,
        ),
        _reply(text="¡Hola Pedro! Coordinamos con Diego.", prompt=10, completion=10),
    ]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    r = await p.chat([{"role": "user", "content": "me llamo Pedro"}])
    assert r.text.startswith("¡Hola Pedro!")
    assert len(_Client.responses) == 0


@pytest.mark.asyncio
async def test_retry_tool_use_failed_agrega_nudge():
    """El 2º intento debe llevar el nudge (sin tools no basta remuestrear)."""
    from app.llm.groq import _NO_TOOLS_NUDGE

    _Client.responses = [
        _Resp(
            {"error": {"message": "tool", "code": "tool_use_failed"}},
            status=400,
        ),
        _reply(text="ok", prompt=5, completion=5),
    ]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    await p.chat([{"role": "user", "content": "hola"}])
    sent = _Client.bodies[-1]["messages"]
    assert any(m.get("content") == _NO_TOOLS_NUDGE for m in sent)


@pytest.mark.asyncio
async def test_400_con_tools_recupera_failed_generation():
    """400 por args fuera de schema (advisor/facts) → tool call saneado."""
    from app.tools.registry import TOOL_DEFS

    raw = (
        '{"name": "update_customer", "arguments": '
        '{"name": "Pedro", "phone": "3515555888", "advisor": "Valentina", '
        '"facts": {"nombre": "Pedro", "nota": "prefiere WhatsApp"}}}'
    )
    _Client.responses = [
        _Resp(
            {"error": {"message": "Tool call validation failed", "code": "tool_use_failed",
                       "failed_generation": raw}},
            status=400,
        ),
    ]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    r = await p.chat([{"role": "user", "content": "me llamo Pedro"}], tools=TOOL_DEFS)
    # Sin retry ni nudge: se recupera en el primer intento.
    assert len(_Client.responses) == 0
    assert _body_has_nudge(_Client.bodies[0]) is False
    assert len(r.tool_calls) == 1
    call = r.tool_calls[0]
    assert call.name == "update_customer"
    assert call.args["name"] == "Pedro" and call.args["phone"] == "3515555888"
    # advisor fuera de properties → descartado; facts filtrado por H12.
    assert "advisor" not in call.args
    assert call.args["facts"] == {"nota": "prefiere WhatsApp"}


@pytest.mark.asyncio
async def test_400_sin_tools_sigue_con_nudge():
    """Sin tools el 400 tool_use_failed va por el nudge (2ª pasada)."""
    from app.llm.groq import _NO_TOOLS_NUDGE

    _Client.responses = [
        _Resp({"error": {"message": "Tool choice is none", "code": "tool_use_failed",
                         "failed_generation": '{"name": "update_customer", "arguments": {}}'}},
              status=400),
        _reply(text="ok, quedó guardado", prompt=5, completion=5),
    ]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    r = await p.chat([{"role": "user", "content": "me llamo Pedro"}])
    assert r.text == "ok, quedó guardado"
    assert any(m.get("content") == _NO_TOOLS_NUDGE for m in _Client.bodies[-1]["messages"])


@pytest.mark.asyncio
async def test_400_con_tools_failed_generation_invalido_lanza():
    """Si failed_generation no se puede parsear, no se inventa un tool call."""
    from app.tools.registry import TOOL_DEFS

    _Client.responses = [
        _Resp({"error": {"message": "Tool call validation failed",
                         "failed_generation": "no-es-json{"}}, status=400),
    ]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    with pytest.raises(Exception):
        await p.chat([{"role": "user", "content": "hola"}], tools=TOOL_DEFS)
    assert len(_Client.responses) == 0


@pytest.mark.asyncio
async def test_400_sin_tool_use_failed_no_se_reintenta():
    _Client.responses = [
        _Resp({"error": {"message": "bad request"}}, status=400),
    ]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    with pytest.raises(Exception):
        await p.chat([{"role": "user", "content": "precio"}])
    assert len(_Client.responses) == 0


@pytest.mark.asyncio
async def test_tool_calls_se_parsean():
    _Client.responses = [
        _reply(
            tool_calls=[{"id": "1", "function": {"name": "show_plans", "arguments": "{}"}}],
            prompt=10,
            completion=10,
        )
    ]
    p = GroqProvider("key", "openai/gpt-oss-20b")
    r = await p.chat([{"role": "user", "content": "precios"}], tools=[{"type": "function"}])
    assert [c.name for c in r.tool_calls] == ["show_plans"]
    assert _Client.last_body["tool_choice"] == "auto"
