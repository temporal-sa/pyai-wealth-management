"""How the chat session reacts when an account opening ends.

_end_account_opening only touches workflow state, so it is called on a stand-in
for the workflow rather than a running one.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from temporalio import workflow

from common.account_context import UpdateAccountOpeningStateInput
from common.agent_constants import OPEN_ACCOUNT_AGENT_NAME, SUPERVISOR_AGENT_NAME
from common.agents import AgentDependencies
from temporal_supervisor.workflows.supervisor_workflow import ACCOUNT_OPENING_ENDED, WealthManagementWorkflow

ROLLED_BACK = "Rolled back. The account was not opened: Custodian rejected the account."


@pytest.fixture(autouse=True)
def _workflow_clock(monkeypatch):
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(timezone.utc))


def _session(account_name="Vacation"):
    deps = AgentDependencies(
        client_id="123",
        current_agent_name=OPEN_ACCOUNT_AGENT_NAME,
        active_account_workflow_id=f"OpenAccount-session-123-{account_name}",
        kyc_pending=True,
    )
    return SimpleNamespace(agent_deps=deps, message_history=[])


def _end(session, account_name, state):
    WealthManagementWorkflow._end_account_opening(
        session, UpdateAccountOpeningStateInput(account_name=account_name, state=state))


def test_both_end_states_are_recognised():
    assert ROLLED_BACK.startswith(ACCOUNT_OPENING_ENDED)
    assert "Complete".startswith(ACCOUNT_OPENING_ENDED)
    assert not "Rolling back: Refund $5,000.00 to the linked bank account.".startswith(ACCOUNT_OPENING_ENDED)
    assert not "Waiting Compliance Review".startswith(ACCOUNT_OPENING_ENDED)


def test_a_rollback_is_recorded_for_the_agents():
    session = _session()
    _end(session, "Vacation", ROLLED_BACK)

    [note] = session.message_history
    [part] = note.parts
    assert isinstance(part, UserPromptPart)
    assert "not from the customer" in part.content
    assert "Vacation" in part.content and ROLLED_BACK in part.content


async def test_the_history_stays_valid_for_openai_after_several_updates():
    # The agents' instructions are inserted after as many messages as there are system
    # messages, so system-message notes would split the tool call from its result.
    session = _session()
    session.message_history = [
        ModelRequest(parts=[UserPromptPart("Open Vacation with $5,000")]),
        ModelResponse(parts=[ToolCallPart("final_result_route_to_investment_agent", {}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart("final_result_route_to_investment_agent", "ok", tool_call_id="c1")]),
    ]
    _end(session, "Vacation", ROLLED_BACK)
    _end(session, "Vacation", "Complete")
    session.message_history.append(ModelRequest(parts=[UserPromptPart("What investments do I have?")],
                                                instructions="Agent instructions"))

    model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="test-key"))
    mapped = await model._map_messages(session.message_history, ModelRequestParameters())
    roles = [m["role"] for m in mapped]
    assert roles.index("tool") == roles.index("assistant") + 1, roles


def test_the_supervisor_takes_over_once_the_account_opening_ends():
    session = _session()
    _end(session, "Vacation", ROLLED_BACK)

    deps = session.agent_deps
    assert deps.active_account_workflow_id is None
    assert not deps.kyc_pending
    assert deps.current_agent_name == SUPERVISOR_AGENT_NAME


def test_another_accounts_opening_is_left_alone():
    session = _session("Boat")
    _end(session, "Vacation", "Complete")

    deps = session.agent_deps
    assert deps.active_account_workflow_id == "OpenAccount-session-123-Boat"
    assert deps.kyc_pending
    assert deps.current_agent_name == OPEN_ACCOUNT_AGENT_NAME
