"""The beneficiary agent deletes a beneficiary by its beneficiary id, after the customer confirms."""

import inspect
import json

import pytest
from pydantic import TypeAdapter
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import common.agents as agents
from common.agent_constants import BENE_INSTRUCTIONS
from common.agents import AgentDependencies, delete_beneficiaries
from common.beneficiaries_manager import BeneficiariesManager

# Two beneficiaries with the same name: only the id tells them apart.
BENEFICIARIES = {"123": [
    {"beneficiary_id": "b-first", "first_name": "Junior", "last_name": "Doe", "relationship": "son"},
    {"beneficiary_id": "b-second", "first_name": "Junior", "last_name": "Doe", "relationship": "nephew"},
]}


@pytest.fixture
def beneficiaries_file(tmp_path, monkeypatch):
    path = tmp_path / "beneficiaries.json"
    path.write_text(json.dumps(BENEFICIARIES))
    monkeypatch.setattr(agents, "beneficiaries_mgr", BeneficiariesManager(str(path)))

    async def no_chaos():
        pass
    monkeypatch.setattr(agents, "core_banking_api", no_chaos)
    return path


def _ctx(customer_message: str):
    # Tools run as activities, so the deps they see have been serialized
    adapter = TypeAdapter(AgentDependencies)
    deps = adapter.validate_python(adapter.dump_python(
        AgentDependencies(client_id="123", customer_message=customer_message), mode="json"))
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def _ids(path):
    return [b["beneficiary_id"] for b in json.loads(path.read_text())["123"]]


def test_the_tool_takes_a_beneficiary_id_not_a_name():
    params = list(inspect.signature(delete_beneficiaries).parameters)
    assert params == ["context", "beneficiary_id", "user_confirmed"]


async def test_deletes_the_chosen_beneficiary_by_id(beneficiaries_file):
    assert await delete_beneficiaries(_ctx("Yes"), "b-second", user_confirmed=True) == "Successfully deleted Junior Doe"
    assert _ids(beneficiaries_file) == ["b-first"]


async def test_deleting_needs_the_customer_to_confirm_first(beneficiaries_file):
    with pytest.raises(ModelRetry):
        await delete_beneficiaries(_ctx("Yes"), "b-first")
    with pytest.raises(ModelRetry):
        await delete_beneficiaries(_ctx("Delete Junior Doe"), "b-first", user_confirmed=True)
    with pytest.raises(ModelRetry):
        await delete_beneficiaries(_ctx("Remove Junior Doe"), "b-first", user_confirmed=True)
    assert _ids(beneficiaries_file) == ["b-first", "b-second"]


async def test_an_unknown_id_deletes_nothing(beneficiaries_file):
    result = await delete_beneficiaries(_ctx("Yes"), "Junior Doe", user_confirmed=True)
    assert result.startswith("ERROR")
    assert _ids(beneficiaries_file) == ["b-first", "b-second"]


def test_the_prompt_lists_by_id_and_confirms_before_deleting():
    assert "Remember the beneficiary id but don't display it." in BENE_INSTRUCTIONS
    assert "use the beneficiary id that is mapped to their choice" in BENE_INSTRUCTIONS
    assert "Ask for confirmation before deleting the beneficiary." in BENE_INSTRUCTIONS
    assert 'delete_beneficiaries(beneficiary_id=' in BENE_INSTRUCTIONS
    assert "first_name=" not in BENE_INSTRUCTIONS


def test_the_prompt_offers_one_consistent_set_of_operations():
    closing = "Would you like to add, delete or list your beneficiaries?"
    assert closing in BENE_INSTRUCTIONS
    # The only offered operations are add, delete and list.
    assert "add, remove" not in BENE_INSTRUCTIONS
    assert '**ONLY ALLOWED**' not in BENE_INSTRUCTIONS
