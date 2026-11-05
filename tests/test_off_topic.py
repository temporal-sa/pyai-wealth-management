"""Questions about anything other than wealth management are declined by the supervisor.

There are no guardrails: the prompts tell the specialists to hand anything else back
to the supervisor, and the supervisor to decline it. No model, Temporal server or
Redis needed.
"""

import re
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import common.agents as agents
from common.agent_constants import (
    BENE_INSTRUCTIONS,
    INVEST_INSTRUCTIONS,
    OFF_TOPIC_INSTRUCTIONS,
    OPEN_ACCOUNT_INSTRUCTIONS,
    SUPERVISOR_AGENT_NAME,
    SUPERVISOR_INSTRUCTIONS,
)
from py_supervisor.main import PydanticAIWealthManagement

DECLINE_BLOCK = """You only help with wealth management: the customer's beneficiaries and investment accounts.
If the customer asks about anything else (for example general knowledge, weather, news, or
other financial products), politely decline, say that you can only help with beneficiaries
and investment accounts, and do not answer the question. Then ask how you can help with
their beneficiaries or investment accounts."""

SRC = Path(__file__).resolve().parents[1] / "src"


def test_the_supervisor_declines_anything_else():
    assert OFF_TOPIC_INSTRUCTIONS == DECLINE_BLOCK
    assert DECLINE_BLOCK in SUPERVISOR_INSTRUCTIONS
    # Right after the supervisor's opening line, before the routing rules.
    assert SUPERVISOR_INSTRUCTIONS.index(DECLINE_BLOCK) < SUPERVISOR_INSTRUCTIONS.index("## Your Output Functions")


def test_the_specialists_hand_anything_else_back():
    transfer = "If the customer asks a question that is not related to the routine, transfer back to the supervisor agent"
    assert transfer in BENE_INSTRUCTIONS
    assert transfer in INVEST_INSTRUCTIONS
    # The Open Account Agent hands back to the Investment Agent, which hands back to the supervisor.
    assert "anything not related to wealth management" in OPEN_ACCOUNT_INSTRUCTIONS


def test_no_guardrail_code_remains():
    pattern = re.compile(r"guardrail|RoutingGuardrailOutput|ROUTING_INSTRUCTIONS|disable_guardrails", re.IGNORECASE)
    offenders = [str(path) for path in SRC.rglob("*.py") if pattern.search(path.read_text())]
    assert offenders == []


async def test_an_off_topic_question_reaches_the_supervisor_and_is_declined(capsys):
    session = PydanticAIWealthManagement()
    session.agent_deps.client_id = "123"
    session.agent_deps.current_agent_name = agents.BENE_AGENT_NAME
    supervisor_saw = []

    def beneficiary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("final_result_route_from_beneficiary_to_supervisor",
                                                 {"client_id": "123"})])

    def supervisor(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        supervisor_saw.append((info.instructions, messages[-1].parts[-1].content))
        return ModelResponse(parts=[ToolCallPart("final_result_respond_to_user", {
            "response": "I'm sorry, I can only help with beneficiaries and investment accounts. "
                        "How can I help with those?"})])

    with agents.beneficiary_agent.override(model=FunctionModel(beneficiary)), \
            agents.supervisor_agent.override(model=FunctionModel(supervisor)):
        await session._process_user_message("What's the weather in Paris?")

    [(instructions, prompt)] = supervisor_saw
    assert DECLINE_BLOCK in instructions
    assert "What's the weather in Paris?" in prompt
    assert "decline it if it is not about beneficiaries or investment accounts" in prompt
    assert "only help with beneficiaries and investment accounts" in capsys.readouterr().out
    assert session.agent_deps.current_agent_name == SUPERVISOR_AGENT_NAME
