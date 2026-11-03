"""The investment agent's tools and the account opening activities share investments.json."""

import json

import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import common.agents as agents
from common.agents import AgentDependencies, close_investment, list_investments
from common.investment_manager import InvestmentAccount, InvestmentManager


@pytest.fixture
def investments_file(tmp_path, monkeypatch):
    path = tmp_path / "investments.json"
    path.write_text(json.dumps({"123": [
        {"investment_id": "i-car", "name": "Car", "balance": 15000.0},
    ]}))
    monkeypatch.setattr(InvestmentManager.__init__, "__defaults__", (str(path),))

    async def no_chaos():
        pass
    monkeypatch.setattr(agents, "core_banking_api", no_chaos)
    return path


def _ctx():
    return RunContext(deps=AgentDependencies(client_id="123"), model=TestModel(), usage=RunUsage())


async def test_tools_see_and_keep_accounts_opened_after_the_worker_started(investments_file):
    assert [a["name"] for a in await list_investments(_ctx())] == ["Car"]

    # The account opening workflow adds accounts through an activity, with its own manager.
    vacation = InvestmentManager().add_investment_account(InvestmentAccount("123", "Vacation", 25000.0))

    assert [a["name"] for a in await list_investments(_ctx())] == ["Car", "Vacation"]

    # Closing one account must not save over the other.
    assert await close_investment(_ctx(), "i-car", user_confirmed=True)
    assert await close_investment(_ctx(), vacation["investment_id"], user_confirmed=True)
    assert json.loads(investments_file.read_text()) == {}
