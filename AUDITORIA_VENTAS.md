# Auditoría de ventas — Agent Ventas

Fecha: 2026-09-25 (última actualización: 2026-09-26). Alcance: **cómo
contexta el agente y conocimientos de ventas** (no seguridad/infra).
Método: lectura de código + sondas en vivo contra Groq real
(`gpt-oss-20b`) con el server corriendo en `:8000` + suite
(**155 passed** al cierre).

**Estado de los hallazgos de este doc:** los dos P0 (fallback del grafo y
clasificador) y los P1 de contacto→handoff y closer de Jeff están
**arreglados y cubiertos por tests** (ver §4). Los P1 de KB de venta y de
re-engagement programado (fuera de la conversación) siguen **ABIERTOS**.

---

## 1. Cómo contexta cada turno

```
system prompt (~870 tok, prompts.py)
+ "Perfil del cliente"      → SOLO si history vacío (graph.py:116, turno 1)
+ history (últimos 8 mensajes = ~4 turnos, conversation.py:66)
+ mensaje del usuario
+ "Información autorizada (tool)"  → solo si el tool no devolvió error (H8)
+ "Información externa"            → solo si Jeff lo autorizó
```

- 1ª llamada con tools (`graph.py:128`), 2ª llamada sin tools para redactar
  con la data autorizada (`graph.py:216`).
- **Jeff decide pero no aconseja**: `next_step` (CLOSE, CAPTURE_CONTACT…)
  solo va a EventLog/CRM — **nunca se inyecta al prompt**, el LLM no sabe
  que le toca cerrar (graph.py:185-191).
- Memoria real del cliente = solo los últimos 8 mensajes. `update_customer`
  guarda en `Customer.facts` pero salvo turno 1 el perfil **no se re-inyecta**
  y `get_customer` no se llama forzado → el nombre/datos se pierden pasadas
  ~4 turnos.

## 2. Hallazgos

### P0 — Las frases de venta caían todas al fallback — ✅ ARREGLADO

`graph.py:195`:

```python
if not any(_tool_ok(v) for v in tool_results.values()):
    return {"draft": FALLBACK_NO_INFO, ...}
```

`any([])` es `False`: si el modelo **no llamó ningún tool** (respondió en
crudo, que es justo lo que hace al vender/objecionar), `tool_results` queda
vacío y la respuesta buena se descarta. El comentario dice "todos los tools
fallaron", pero **vacío ≠ fallaron**.

Evidencia en vivo (Groq real, intención `general` → rama con tools):

| Frase | Respuesta del modelo (1ª llamada) | Lo que ve el cliente |
|---|---|---|
| "Me parece caro" | "Entiendo tu preocupación por el precio. ¿Podrías contarme más sobre tu negocio…?" | "Eso no lo tengo a mano…" |
| "Está muy caro, otro lo hace más barato" | igual de buena | "Eso no lo tengo a mano…" |
| "¿En qué se diferencian de otros chatbots?" | "Sofi, la que te ayuda a sacar máximo de tu negocio… ¿qué tipo de negocio tenés?" | "Eso no lo tengo a mano…" |
| "Tengo una panadería, me interesa pero me parece caro" | pregunta de descubrimiento correcta | "Eso no lo tengo a mano…" |

**Objeciones, comparación y descubrimiento sin keyword → el closer ni habla.**
La venta murió acá: 6/8 frases de venta probadas cayeron al fallback.

Por qué los tests no lo ven: `FakeProvider` **siempre** llama al menos un
tool (`get_business_info`), nunca llega la rama vacía.

**Fix:** distinguir los tres casos en `graph.py`:

```python
if tool_results and not any(_tool_ok(v) for v in tool_results.values()):
    return {... FALLBACK_NO_INFO ...}   # tools existieron y fallaron (H8 real)
if not tool_results:
    return {... draft: first.text ...}  # modelo respondió solo: usar su texto
# hay tools OK → 2ª llamada como hoy
```

El `draft` pasa igual por `verify` antes de salir.

**Estado:** implementado en `graph.py` (los tres casos) +
`tests/test_graph_no_tools_fallback.py` (regresión). Smoke en vivo:
objeción/comparación/booking responden con el texto del modelo.

### P0 — Clasificador de intención no entiende lenguaje de venta — ✅ ARREGLADO

`policies.py:5-12` es keyword-match puro:

- **"caro"/"barato"/"descuento" no están en ninguna lista** → objeciones de
  precio caen en `general` (y de ahí al P0).
- "me **cuesta** conseguir clientes" → `pricing` (falso positivo: "cuesta"
  como verbo). Observado en el smoke: turno 1 de una panadería pidiendo
  precios de verdad igual.
- No hay intención `objection` ni `comparison`.

**Fix mínimo:** agregar `objection: ("caro", "barato", "no tengo plata",
"después veo", "ya tengo", "carísimo")` + exigir contexto de precio para
`pricing` (regex `\$\d|cuánto|precio|cuesta un` en vez de "cuesta" suelto).
Más robusto: pasar `classify_intent` a JEV (barato, $0.0016/llamada) — ya
está el modelo juez para exactamente esto.

**Estado:** implementado — `objection`/`comparison` en
`policies.py` + `pricing` exige contexto de precio por regex +
handoff por verbo de pedido (no por mención). Tests:
`tests/test_policies_verifier.py`. Smoke en vivo: "Me parece caro" →
`objection`, "¿en qué se diferencian?" → `comparison`,
"Agendemos una llamada" → `conversion` sin handoff.

**Mejora pendiente:** migrar `classify_intent` a JEV (el modelo juez)
si los keywords volvieron a quedar cortos.

### P1 — Conocimientos de venta: no existen — ⚠️ ABIERTO

- **`Knowledge` vacío para los negocios reales**: la DB tiene
  `knowledge: []` para business A y B. El seed solo carga
  `"Tienda Demo"` y `"SalesMind"` (`seed_knowledge.py:119-121`) — ningún
  negocio creado por la app recibe KB.
- El seed mismo no tiene **ni un doc de venta**: hay horarios, envíos,
  contratos… pero nada de objeciones, diferenciación vs competencia,
  ROI/justificación de precio, casos de éxito. Es KB de *atención al
  cliente*, no de *venta*.
- Resultado: "¿en qué se diferencian?" → Jeff dice `needs_external=True`
  pero como `tool_results` vacío, ni siquiera llega la búsqueda (P0 corta
  antes).
- Categorías (`models.py:35-45`) no incluyen objeción/venta.

**Fix:** (a) que el seed aplique a *todo* negocio con
`business.name`/por `business_id` o un endpoint `POST /api/knowledge/seed`;
(b) crear docs tipo battlecard: "objeción precio", "vs competencia",
"¿por qué no hacerlo uno mismo?", "qué incluye cada plan y para quién".

### P1 — El "closer" de Jeff no cierra — ✅ ARREGLADO

**Estado (2026-09-26):**

- `jeff.py` emite `HANDLE_OBJECTION` (regla 6: `intent == "objection"`) y
  `FOLLOW_UP` (regla 11: contacto en profile + `message_count >= 4` + sin
  cita previa). El vocabulario completo `NEXT_STEPS` tiene emisor.
- **El `next_step` llega al prompt**: `STEP_DIRECTIVES` en `prompts.py` +
  `_directive()` en `graph.py` — directiva preliminar antes de la 1ª llamada
  y final (decidida con tools) antes de la 2ª. El modelo sabe si le toca
  cerrar/agendar/objetar (test: `tests/test_close_directive.py`).
- `ready_to_buy` lo aporta el orquestador: `CLOSE`/`CAPTURE_CONTACT` →
  scoring `calificado` (test: `test_ready_to_buy_lo_aporta_el_orquestador`).
- `chat.py` loguea `decision`, `issues` reales (del verifier, vía
  `AgentState.issues`) y `tools` (`{tool: ok|error}`) en EventLog.

**Pendiente relacionado:** re-engagement *programado* (cron/follow-up fuera
de la conversación) sigue sin existir — el `FOLLOW_UP` de Jeff cubre solo
el caso "el lead volvió a escribir".

### P1 — Contacto del cliente dispara handoff y corta la venta — ✅ ARREGLADO

**Estado:** `jeff.py` ya no trata "llamo"/"me llamo" como handoff (solo
verbos de pedido explícito: "hablar con", "llamarme") y el clasificador
metió el contacto en la rama que guarda datos. Smoke en vivo: "Me llamo
Pedro, mi WhatsApp es 3515555888" → `Customer('Pedro', '3515555888')`
guardado, `conversations.state='ai'` (sin handoff), lead `caliente`.
Tests: `tests/test_jeff.py`.

Comportamiento original observado:

"Me llamo Pedro, mi WhatsApp es 3515555888" → keyword `whatsapp` →
`intent=handoff` → `conv.state="human"` (`conversation.py:129`) y razón
"el usuario pide contacto por whatsapp". El cliente **daba sus datos para
comprar** y la conversación pasó a cola humana mientras el bot respondía
"te mando un link de pago". Debería ser `CAPTURE_CONTACT` (regla 10 de
Jeff), no handoff — alcanza con que la keyword exija verbo de pedido
("hablar con", "llamarme") y no la mera mención de "whatsapp".

### P2 — Calidad de respuesta

- **Markdown se filtra**: respuesta en vivo con `**Café A**` a pesar de que
  el prompt prohíbe markdown (el verificador no lo chequea y `replies` solo
  parte por `\n\n`).
- **Datos demo salen a producción**: el único producto de business A es
  "Café A $10" — una panadería recibe tarjetas de café (H13 de la auditoría
  de seguridad, sigue vigente).
- No hay endpoint de knowledge (`app/api/routes/` solo tiene
  catalog/chat/health/leads/webhook); la única vía sigue siendo
  `python -m app.rag.seed_knowledge` manual.

## 3. Veredicto

| Dimensión | Estado |
|---|---|
| Contexto técnico (tokens, cache, grounding) | ✅ sano (verificado hoy) |
| Fallback del grafo (frases de venta sin tools) | ✅ arreglado (3 casos + test) |
| Clasificación de intención de venta | ✅ `objection`/`comparison`/regex pricing |
| Contacto del cliente vs handoff | ✅ arreglado (no corta la venta) |
| Cierre (Jeff → LLM) | ✅ directiva `STEP_DIRECTIVES` en el prompt + reglas `HANDLE_OBJECTION`/`FOLLOW_UP` |
| CRM ↔ calendario | ✅ cita ligada al lead, lead `qualified`, `GET /api/appointments` con token |
| Memoria de cliente | ⚠️ 4 turnos, después olvida |
| Conocimiento de venta (KB) | ❌ vacío en negocios reales |
| Follow-up / re-engagement programado | ❌ no existe (solo `FOLLOW_UP` en conversación) |

**Orden de arreglo restante:** 1) KB de venta + seed por negocio,
2) re-engagement programado (cron de leads fríos/tibios).

## 4. Verificación

- Suite: `python -m pytest -q` → **155 passed**.
- Sondas: script directo sobre `build_graph()` con Groq real (frases de la
  tabla P0) + conversaciones en vivo vía `POST /api/chat`.
- Smoke en vivo (2026-09-26): "Me parece caro" → `objection`;
  "¿en qué se diferencian?" → `comparison`; "Me llamo Pedro, mi WhatsApp
  es 3515555888" → `Customer` guardado, conv en `ai`, lead `caliente`;
  "Agendemos una llamada" → `conversion` sin handoff.
- **Ciclo de venta completo en vivo (2026-09-26):** panadería →
  `CAPTURE_CONTACT`; objeción → `HANDLE_OBJECTION` con precio real;
  "Agendemos para el martes" → `SCHEDULE` ofreciendo slots; "Sí, dale,
  perfecto" → `CLOSE`, `Appointment(confirmed, lead_id=10)` para el martes
  10:00, lead `qualified` / "Cita agendada para 28/09 10:00", y
  `GET /api/appointments?token=…` lo devuelve. EventLog registra
  `decision` + `issues` + `tools` reales por turno.
- Cobertura de la rama `tool_results == []`:
  `tests/test_graph_no_tools_fallback.py` — el LLM sin tool calls responde
  con su texto, no con fallback. Booking E2E: `tests/test_booking_crm.py`;
  directivas de cierre: `tests/test_close_directive.py`.
