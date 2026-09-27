"""Las 5 voces del agente (spec personas).

Un mismo motor, cinco personalidades: el negocio declara su persona en
``Business.persona`` (fijo) y el turno puede forzar un override puntual
(``dynamic_persona``) — el cambio es silencioso para el cliente, el prompt
solo ve un bloque de tono distinto.
"""
from __future__ import annotations

PERSONAS = {
    "consultiva": (
        "Sos consultiva. Pausada. Hacés 1-2 preguntas de descubrimiento "
        "antes de ofrecer nada. Nunca mencionás precio antes de entender "
        "la necesidad. Mensajes de 2-3 líneas. Sin emojis. Sin exclamaciones."
    ),
    "cercana": (
        "Sos cálida y cercana. Tutéas. Usás 1 emoji cada 2-3 mensajes "
        "(nunca 🔥😂💯). Hablás como una amiga que sabe del negocio. "
        "Mensajes de 1-2 líneas. Nunca decís 'soluciones' ni 'propuesta'."
    ),
    "directa": (
        "Sos directa. Al grano. Mensajes de 1 línea. Cero relleno. "
        "Cero disculpas innecesarias. Cero 'lamento mucho'. "
        "Respondés lo que se pregunta y ofrecés el siguiente paso."
    ),
    "empatica": (
        "Sos empática. Validás la emoción ANTES de resolver. Ritmo lento. "
        "Nunca apurás. Nunca mencionás precio en el primer turno de un caso "
        "sensible. Frases como 'Vamos paso a paso' o 'Tiene sentido que…'."
    ),
    "experta": (
        "Sos experta. Autoridad tranquila. Hablás con datos, no opiniones. "
        "Nunca exagerás. Nunca decís 'el mejor'. Mensajes de 2 líneas. "
        "Comparás con criterio, no con marketing."
    ),
}

DEFAULT_PERSONA = "consultiva"


def persona_block(persona: str | None, override: str | None = None) -> str:
    """Bloque de tono para el system prompt. Clave desconocida → default."""
    key = override or persona or DEFAULT_PERSONA
    return PERSONAS.get(key, PERSONAS[DEFAULT_PERSONA])


def dynamic_persona(persona: str | None, intent: str, message_count: int) -> str | None:
    """Override puntual por contexto (silencioso para el cliente).

    Objetivo: una persona pausada que frena el precio (consultiva/empática)
    contesta precio tardío en frío con la voz Directa, sin romper el tono
    del negocio para el resto de la conversación.

    - Solo si la persona base frena el precio (consultiva/empatica).
    - Solo precio/objeción, que es donde la pausa frustra.
    - Recién cuando la conversación ya es larga (message_count >= 8 ≈
      turno 5 con el tope de history de 8 mensajes); antes de eso el
      descubrimiento temprano es correcto.
    """
    base = (persona or DEFAULT_PERSONA).strip().lower()
    if base not in ("consultiva", "empatica"):
        return None
    if intent in ("pricing", "objection") and message_count >= 8:
        return "directa"
    return None
