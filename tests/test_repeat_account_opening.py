"""Every request to open an account starts a new account opening, with its own KYC.

Runs the real WealthManagementWorkflow and OpenInvestmentAccountWorkflow with a scripted
model instead of OpenAI. The customer opens "Vacation", confirms their details, then opens
"Vacation" again while the first opening waits for compliance review. Two child workflows
must run (IDs ending -Vacation-1 and -Vacation-2), and the second needs its own KYC
confirmation: an approval in the turn its details are first shown is refused.

Activities that would touch Redis, the data files or another Temporal server are stubs.
Requires the Temporal dev server (downloaded automatically on first run). No OpenAI API
key or Redis needed.
"""

import asyncio
import uuid
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

import common.agents as agents
import temporal_supervisor.chaos as chaos
from common.user_message import ChatInteraction, ProcessUserMessageInput
from temporal_supervisor.activities.clients import WealthManagementClient
from temporal_supervisor.activities.investments import Investments
from temporal_supervisor.model_config import LLM_TASK_QUEUE
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow

TASK_QUEUE = "test-repeat-account-opening"
OPEN_VACATION = "Open Vacation with $13,000"
ENV: dict = {}
INTERACTIONS: list[ChatInteraction] = []
KYC_SHOWN_FOR: list[str] = []  # child workflow IDs whose details were fetched for KYC


@activity.defn(name="get_task_queue_open_account")
async def stub_get_task_queue_open_account() -> str:
    return TASK_QUEUE


@activity.defn(name="delete_conversation")
async def stub_delete_conversation(workflow_id: str) -> bool:
    return True


@activity.defn(name="append_status_update")
async def stub_append_status_update(workflow_id: str, status_update: Any) -> int:
    return 0


@activity.defn(name="append_chat_interaction")
async def stub_append_chat_interaction(workflow_id: str, chat_interaction: ChatInteraction) -> int:
    INTERACTIONS.append(chat_interaction)
    return len(INTERACTIONS)


@activity.defn(name="list_investments")
async def stub_list_investments(client_id: str) -> list:
    return [{"investment_id": "i-1", "name": "Checking", "balance": 1000.0}]


@activity.defn(name="get_client")
async def stub_get_client(client_id: str) -> dict:
    return {"client_id": client_id, "first_name": "Don", "last_name": "Doe", "address": "123 Main Street",
            "phone": "999-555-1212", "email": "jd@someplace.com", "marital_status": "married"}


@activity.defn(name="get_current_client_info")
async def stub_get_current_client_info(workflow_id: str) -> WealthManagementClient:
    KYC_SHOWN_FOR.append(workflow_id)
    handle = ENV["env"].client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)
    return await handle.execute_update(OpenInvestmentAccountWorkflow.get_client_details)


async def _test_server_handle(workflow_id):
    return ENV["env"].client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)


def _call(name: str, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, args)])


def _script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Plays every agent, by its instructions and the latest message."""
    instructions = info.instructions or ""
    last = messages[-1]
    prompt = next((p.content for p in last.parts if isinstance(p, UserPromptPart)), None)
    returned = [p.tool_name for p in last.parts if isinstance(p, ToolReturnPart)]

    if "# Supervisor Agent (Router)" in instructions:
        return _call("final_result_route_to_investment_agent", {"client_id": "123"})
    if "You are an investment agent" in instructions:
        return _call("final_result_route_from_investment_to_open_account", {
            "client_id": "123",
            "account_info": {"client_id": "123", "account_name": "Vacation", "initial_amount": 13000},
        })
    # Open Account Agent
    if prompt == OPEN_VACATION:
        return _call("final_result_route_from_open_account_to_investment_agent", {"client_id": "123"})
    if prompt and prompt.startswith("The user wants to open a new account"):
        # Also tries to approve KYC straight away, as if the customer's confirmation
        # of the earlier opening counted for this one. It must not.
        return ModelResponse(parts=[
            ToolCallPart("final_result_respond_about_account_opening",
                         {"response": "Processing your account opening request..."}),
            ToolCallPart("approve_client_kyc", {}),
        ])
    if prompt == "Yes":
        return _call("approve_client_kyc", {})
    if "approve_client_kyc" in returned:
        return _call("final_result_respond_about_account_opening", {
            "response": "Your KYC information has been approved. Your account is now pending compliance review."})
    raise AssertionError(f"unscripted model request: {last}")


@pytest.fixture
async def chat(monkeypatch):
    async def chaos_off(_flag):
        return False

    monkeypatch.setattr(chaos, "is_enabled", chaos_off)
    monkeypatch.setattr(agents.agent_model, "wrapped", FunctionModel(_script))
    monkeypatch.setattr(agents, "get_workflow_handle", _test_server_handle)
    INTERACTIONS.clear()
    KYC_SHOWN_FOR.clear()

    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        ENV["env"] = env
        # Unsandboxed, so the workflows see the scripted model patched in above.
        worker = Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[WealthManagementWorkflow, OpenInvestmentAccountWorkflow],
            activities=[stub_get_task_queue_open_account, stub_delete_conversation, stub_append_status_update,
                        stub_append_chat_interaction, stub_list_investments, stub_get_client, stub_get_current_client_info,
                        Investments.validate_new_account, Investments.find_promotion],
            plugins=[PydanticAIPlugin()],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        llm_worker = Worker(
            env.client,
            task_queue=LLM_TASK_QUEUE,
            activities=[a for agent in WealthManagementWorkflow.__pydantic_ai_agents__
                        for a in agent.temporal_activities],
        )
        async with worker, llm_worker:
            handle = await env.client.start_workflow(
                WealthManagementWorkflow.run, id=f"repeat-opening-{uuid.uuid4()}", task_queue=TASK_QUEUE)
            try:
                yield env, handle
            finally:
                # Terminated rather than ended, so a stuck run can't hang the test
                await handle.terminate()


async def _say(handle, text: str) -> str:
    before = len(INTERACTIONS)
    await handle.signal(WealthManagementWorkflow.process_user_message, ProcessUserMessageInput(user_input=text))
    for _ in range(300):
        if len(INTERACTIONS) > before:
            return INTERACTIONS[-1].text_response
        await asyncio.sleep(0.1)
    raise AssertionError(f"no reply to {text!r}")


async def _state(env, workflow_id: str, expected: str) -> str:
    handle = env.client.get_workflow_handle(workflow_id)
    state = None
    for _ in range(50):
        state = await handle.query(OpenInvestmentAccountWorkflow.get_current_state)
        if state == expected:
            break
        await asyncio.sleep(0.1)
    return state


async def test_each_request_opens_a_new_account_with_its_own_kyc(chat):
    env, handle = chat
    first_id = f"OpenAccount-{handle.id}-123-Vacation-1"
    second_id = f"OpenAccount-{handle.id}-123-Vacation-2"

    reply = await _say(handle, OPEN_VACATION)
    assert "Don" in reply and "123 Main Street" in reply
    assert f"(Workflow ID: {first_id})" in reply
    # The approval in the same turn as the details were shown was refused.
    assert await _state(env, first_id, "Waiting KYC") == "Waiting KYC"

    await _say(handle, "Yes")
    assert await _state(env, first_id, "Waiting Compliance Review") == "Waiting Compliance Review"

    # The same request again, while the first opening waits for compliance review.
    reply = await _say(handle, OPEN_VACATION)
    assert "Don" in reply and "123 Main Street" in reply
    assert f"(Workflow ID: {second_id})" in reply
    assert KYC_SHOWN_FOR == [first_id, second_id]
    # Its own KYC: the first opening's confirmation doesn't approve it.
    assert await _state(env, second_id, "Waiting KYC") == "Waiting KYC"

    await _say(handle, "Yes")
    assert await _state(env, second_id, "Waiting Compliance Review") == "Waiting Compliance Review"
    # Both openings are still running.
    for workflow_id in (first_id, second_id):
        description = await env.client.get_workflow_handle(workflow_id).describe()
        assert description.status.name == "RUNNING"
        assert description.parent_id == handle.id
