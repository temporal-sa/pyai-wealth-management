"""The standalone (non-Temporal) version in py_supervisor, with stand-in models.

No OpenAI API key, Temporal server or Redis needed.
"""

import json
from functools import partial

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import common.agents as agents
from common.agent_constants import ROUTING_FALLBACK_RESPONSE, SUPERVISOR_AGENT_NAME
from common.domain_classes import OpenInvestmentAccountInput
from common.investment_manager import InvestmentManager
from py_supervisor.main import PydanticAIWealthManagement


@pytest.fixture
def investments_file(tmp_path, monkeypatch):
    path = tmp_path / "investments.json"
    path.write_text(json.dumps({"123": []}))
    monkeypatch.setattr(agents, "InvestmentManager", partial(InvestmentManager, str(path)))
    return path


def _always_calls(tool_name: str, args: dict) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(tool_name, args)])
    return FunctionModel(respond)


async def test_routing_loops_are_cut_off(capsys):
    session = PydanticAIWealthManagement()
    supervisor_calls = []

    def supervisor(messages, info):
        supervisor_calls.append(1)
        return ModelResponse(parts=[ToolCallPart("final_result_route_to_beneficiary_agent", {"client_id": "123"})])

    with agents.supervisor_agent.override(model=FunctionModel(supervisor)), \
            agents.beneficiary_agent.override(model=_always_calls(
                "final_result_route_from_beneficiary_to_supervisor", {"client_id": "123"})):
        await session._process_user_message("Who are my beneficiaries?")

    assert ROUTING_FALLBACK_RESPONSE in capsys.readouterr().out
    assert len(supervisor_calls) == 6  # 1 + 10 routes, alternating
    assert session.agent_deps.current_agent_name == SUPERVISOR_AGENT_NAME
    assert session.agent_deps.next_agent is None


async def test_the_customer_message_is_recorded_once(capsys):
    session = PydanticAIWealthManagement()
    with agents.supervisor_agent.override(model=_always_calls(
            "final_result_respond_to_user", {"response": "What is your client ID?"})):
        await session._process_user_message("Hello")

    prompts = [part.content for message in session.message_history for part in message.parts
               if isinstance(part, UserPromptPart)]
    assert prompts == ["Hello"]
    assert "What is your client ID?" in capsys.readouterr().out


async def test_investments_are_routed_without_temporal(investments_file, capsys):
    session = PydanticAIWealthManagement()
    with agents.supervisor_agent.override(model=_always_calls(
            "final_result_route_to_investment_agent", {"client_id": "123"})), \
            agents.investment_agent.override(model=_always_calls(
                "final_result_respond_about_investments", {"response": "You have no investment accounts."})):
        await session._process_user_message("What investments do I have?")

    assert "You have no investment accounts." in capsys.readouterr().out
    assert session.agent_deps.current_agent_name == "Investment Agent"


async def test_opening_an_account_without_temporal(investments_file):
    session = PydanticAIWealthManagement()
    ctx = agents.RunContext(deps=session.agent_deps, model=FunctionModel(lambda m, i: None), usage=None)
    reply = await agents.route_from_investment_to_open_account(
        ctx, "123", OpenInvestmentAccountInput(client_id="123", account_name="Vacation account", initial_amount=500.0))

    assert reply == "Your Vacation account has been opened with $500.00."
    assert session.agent_deps.next_agent is None
    [account] = json.loads(investments_file.read_text())["123"]
    assert (account["name"], account["balance"]) == ("Vacation", 500.0)
