"""Las 5 voces (spec personas): bloque por negocio + override dinámico."""
from app.agent.personas import DEFAULT_PERSONA, PERSONAS, dynamic_persona, persona_block

CLAVES = ("consultiva", "cercana", "directa", "empatica", "experta")


def test_cinco_personas_definidas():
    assert set(CLAVES) == set(PERSONAS)
    for key in CLAVES:
        assert len(PERSONAS[key]) > 50  # bloque real, no placeholder


def test_persona_block_default_y_desconocida():
    assert persona_block(None) == PERSONAS[DEFAULT_PERSONA]
    assert persona_block("") == PERSONAS[DEFAULT_PERSONA]
    assert persona_block("no_existe") == PERSONAS[DEFAULT_PERSONA]
    assert persona_block("directa") == PERSONAS["directa"]


def test_override_vence_a_la_base():
    assert persona_block("empatica", override="directa") == PERSONAS["directa"]
    assert persona_block("experta", override=None) == PERSONAS["experta"]


def test_dynamic_solo_para_precio_tardio_en_voces_pausadas():
    # Precio temprano → sin override (el descubrimiento es lo correcto).
    assert dynamic_persona("empatica", "pricing", 2) is None
    # Precio tardío en voz pausada → directa.
    assert dynamic_persona("empatica", "pricing", 8) == "directa"
    assert dynamic_persona("consultiva", "objection", 9) == "directa"
    # Voz que no frena el precio → nunca cambia.
    assert dynamic_persona("directa", "pricing", 20) is None
    assert dynamic_persona("experta", "objection", 20) is None
    # Otros intents → nunca cambia.
    assert dynamic_persona("empatica", "discovery", 20) is None


def test_system_prompt_lleva_bloque_de_persona():
    from app.agent.prompts import SYSTEM_TEMPLATE

    s = SYSTEM_TEMPLATE.format(
        business_name="A", agent_name="Ana", persona=persona_block("cercana")
    )
    assert PERSONAS["cercana"] in s
    assert "{persona}" not in s
