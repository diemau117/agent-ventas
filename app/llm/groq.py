import asyncio
import json

import httpx

from app.llm.base import LLMProvider, LLMResult, LLMToolCall

API_URL = "https://api.groq.com/openai/v1/chat/completions"
# USD por 1K tokens: (input, input_cache_read, output). Precios oficiales
# Groq: gpt-oss-20b $0.075 / $0.037 / $0.30 por 1M.
_COST_PER_1K = {
    "openai/gpt-oss-20b": (0.000075, 0.000037, 0.0003),
    "openai/gpt-oss-120b": (0.00015, 0.000075, 0.0006),
}

# Nudge de reintento para tool_use_failed: le dice al modelo que los datos ya
# están y que conteste en texto. Medido 3/3 respuestas válidas.
_NO_TOOLS_NUDGE = (
    "No uses herramientas ni tools: los datos ya están guardados. "
    "Contestandole en texto, breve."
)


def _body_has_nudge(body: dict) -> bool:
    return any(
        isinstance(m, dict) and m.get("content") == _NO_TOOLS_NUDGE
        for m in body.get("messages", [])
    )


def _sanitize_tool_args(args: dict, schema: dict) -> dict:
    """Recorta los argumentos al schema que se envió.

    Groq valida el tool call contra el schema del body y devuelve 400 con la
    generación cruda en `failed_generation`. Remuestrear no sirve (medido: 3/3
    el mismo incumplimiento: `advisor` y `facts.{nombre,telefono}`), así que se
    cumple el schema aquí. `execute_tool` sigue aplicando H12 al ejecutar.
    """
    props = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        args = {k: v for k, v in args.items() if k in props}
    for k, v in list(args.items()):
        enum = ((props.get(k) or {}).get("propertyNames") or {}).get("enum")
        if enum and isinstance(v, dict):
            args[k] = {kk: vv for kk, vv in v.items() if kk in enum}
    return args


def _tool_call_from_failed_generation(e: httpx.HTTPStatusError, tools: list[dict]) -> LLMToolCall | None:
    """Extrae y sanea el tool call de `failed_generation`, si es recuperable."""
    try:
        err = e.response.json().get("error") or {}
        raw = err.get("failed_generation")
        if not raw:
            return None
        call = json.loads(raw) if isinstance(raw, str) else raw
        name = call.get("name")
        args = call.get("arguments") or {}
        if isinstance(args, str):
            args = json.loads(args)
        schema = next(
            (t["function"]["parameters"] for t in tools if t["function"]["name"] == name),
            None,
        )
        if schema is None or not isinstance(args, dict):
            return None
        return LLMToolCall(name=name, args=_sanitize_tool_args(args, schema))
    except (ValueError, AttributeError, TypeError, KeyError):
        return None


class GroqProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResult:
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.4,
            # gpt-oss razona por defecto: con "low" el output de un turno de
            # chat cae ~3x (240→93 tokens medidos) sin tocar la calidad de la
            # redacción — la decisión la toma Jeff, no el razonamiento largo.
            "reasoning_effort": "low",
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = None
        # 3 intentos: 429/5xx y el 400 tool_use_failed son todos recuperables
        # por remuestreo; con 2 fallos seguidos de ~1/4 cada uno el residual
        # baja de 6% a ~1.5%.
        for attempt in (1, 2, 3):
            try:
                async with httpx.AsyncClient(timeout=30) as c:
                    r = await c.post(
                        API_URL,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=body,
                    )
                    r.raise_for_status()
                    data = r.json()
                break
            except httpx.HTTPStatusError as e:
                # 429/5xx: un reintento con espera salva la venta en vez de caer al fallback.
                if attempt < 3 and e.response.status_code in (408, 425, 429, 500, 502, 503, 504):
                    await asyncio.sleep(4)
                    continue
                # 400 con tools: el schema rechazó el tool call del modelo.
                # Se recupera determinísticamente desde failed_generation.
                if e.response.status_code == 400 and tools:
                    recovered = _tool_call_from_failed_generation(e, tools)
                    if recovered is not None:
                        return LLMResult(
                            tool_calls=[recovered],
                            usage={"input": 0, "output": 0, "cost_est": 0.0},
                        )
                    raise
                # 400 tool_use_failed sin tools (2ª pasada del grafo): el
                # modelo insiste con update_customer 3/3 — remuestreo no basta,
                # el reintento agrega un nudge explícito. Medido: nudge 3/3 ok.
                if attempt < 3 and e.response.status_code == 400 and (
                    "tool_use_failed" in (e.response.text or "")
                ):
                    if not _body_has_nudge(body):
                        body["messages"] = list(body["messages"]) + [
                            {"role": "user", "content": _NO_TOOLS_NUDGE}
                        ]
                    continue
                raise
        assert data is not None
        msg = data["choices"][0]["message"]
        calls = [
            LLMToolCall(
                name=t["function"]["name"],
                args=json.loads(t["function"].get("arguments") or "{}"),
                id=t.get("id", ""),
            )
            for t in msg.get("tool_calls") or []
        ]
        u = data.get("usage") or {}
        ci, cc, co = _COST_PER_1K.get(self.model, (0.0, 0.0, 0.0))
        pi, po = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        # Prompt caching automático de Groq: el prefijo (system + tools) se
        # factura a mitad de precio cuando cachea.
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        cost = (pi - cached) / 1000 * ci + cached / 1000 * cc + po / 1000 * co
        return LLMResult(
            text=msg.get("content") or "",
            tool_calls=calls,
            usage={"input": pi, "output": po, "cost_est": cost},
        )
