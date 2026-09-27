from typing import Any
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    business_id: int
    conversation_id: int
    user_message: str
    history: list[dict]
    agent_name: str
    profile: str
    intent: str
    blocked: bool
    tool_results: dict[str, Any]
    draft: str
    reply: str
    usage: dict
    # Jeff (spec §13): decisión del turno, ya serializada para EventLog.
    decision: dict[str, Any]
    # Handoff (spec §16): el turno pidió humano → conversation.state = "human".
    handoff: bool
    handoff_reason: str
    # Verificación (H4): issues del verifier para EventLog.
    issues: list[str]
