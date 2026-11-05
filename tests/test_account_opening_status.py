"""How the chat session reacts when an account opening ends.

_end_account_opening only touches workflow state, so it is called on a stand-in
for the workflow rather than a running one.
"""

import asyncio
import logging
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
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from common.agent_constants import INVEST_AGENT_NAME, OPEN_ACCOUNT_AGENT_NAME
from common.agents import AgentDependencies, get_account_opening_status
from temporal_supervisor.workflows.supervisor_workflow import (
    ACCOUNT_OPENING_ENDED,
    WealthManagementWorkflow,
)

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


def _end(session, account_name, state, workflow_id=None):
    WealthManagementWorkflow._end_account_opening(
        session, UpdateAccountOpeningStateInput(account_name=account_name, state=state, workflow_id=workflow_id))


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


def test_the_investment_agent_takes_over_once_the_account_opening_ends():
    session = _session()
    _end(session, "Vacation", ROLLED_BACK)

    deps = session.agent_deps
    assert deps.active_account_workflow_id is None
    assert not deps.kyc_pending
    assert deps.current_agent_name == INVEST_AGENT_NAME


def test_the_workflow_id_identifies_the_account_opening_that_ended():
    session = _session("Vacation")
    # Same account name, but a different account opening.
    _end(session, "Vacation", "Complete", workflow_id="OpenAccount-other-session-123-Vacation")
    assert session.agent_deps.active_account_workflow_id == "OpenAccount-session-123-Vacation"

    _end(session, "Vacation", "Complete", workflow_id="OpenAccount-session-123-Vacation")
    assert session.agent_deps.active_account_workflow_id is None


def test_another_accounts_opening_is_left_alone():
    session = _session("Boat")
    _end(session, "Vacation", "Complete")

    deps = session.agent_deps
    assert deps.active_account_workflow_id == "OpenAccount-session-123-Boat"
    assert deps.kyc_pending
    assert deps.current_agent_name == OPEN_ACCOUNT_AGENT_NAME


async def test_the_state_signal_is_kept_for_the_status_tool(monkeypatch):
    monkeypatch.setattr(workflow, "logger", logging.getLogger(__name__))
    session = SimpleNamespace(agent_deps=AgentDependencies(), pending_status_updates=asyncio.Queue())
    update = UpdateAccountOpeningStateInput(
        account_name="Vacation", state="Waiting Compliance Review", workflow_id="OpenAccount-1")
    await WealthManagementWorkflow.update_account_opening_state(session, update)

    assert session.agent_deps.account_opening_states == {"OpenAccount-1": "Waiting Compliance Review"}
    assert session.pending_status_updates.get_nowait() == update


def _tool_ctx(deps: AgentDependencies) -> RunContext[AgentDependencies]:
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


async def test_get_account_opening_status():
    deps = AgentDependencies(
        account_opening_states={"OpenAccount-1": "Waiting KYC", "OpenAccount-2": ROLLED_BACK},
        active_account_workflow_id="OpenAccount-1",
    )
    ctx = _tool_ctx(deps)
    assert await get_account_opening_status(ctx, "OpenAccount-2") == ROLLED_BACK
    # No ID: the current account opening.
    assert await get_account_opening_status(ctx) == "Waiting KYC"
    assert await get_account_opening_status(ctx, "OpenAccount-9") == (
        "No account opening with workflow ID OpenAccount-9 was found in this conversation.")


def test_the_status_tool_runs_in_the_workflow():
    # It reads deps that only the workflow keeps up to date.
    from temporal_supervisor.workflows.supervisor_workflow import temporal_open_account_agent

    toolset = next(t for t in temporal_open_account_agent._toolsets if t.id == "<agent>")
    assert toolset.tool_activity_config == {"get_account_opening_status": False}
