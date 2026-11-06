"""Live test: asking to open an account again starts a new account opening, with its own KYC.

The conversation is "Who are my beneficiaries?", "123", "What investments do I have?",
"Open Vacation with $13,000" (the agent shows the client's details for KYC), "Yes" (the
opening waits for compliance review), then "Open Vacation with $13,000" again. The model
decides whether to start a new opening or answer from the conversation history, so this
runs the real WealthManagementWorkflow and OpenInvestmentAccountWorkflow with real OpenAI
calls. Both replies to the open request must show the KYC details, and two child
workflows must be started, with IDs ending -Vacation-1 and -Vacation-2.

The beneficiary, investment and client activities only read data/*.json, and compliance is
never approved, so no data file is written. The Redis event stream activities are in-memory
stand-ins, the chaos flags read as off, and the KYC activities talk to the test server
rather than the one in ClientHelper's configuration.

Not part of the default `uv run pytest` run, because it calls the paid OpenAI API. Run it with:
    source ./setoaikey.sh && LIVE_TEST_RUNS=3 uv run pytest tests/live_repeat_opening.py -s
Requires the Temporal dev server (downloaded automatically on first run).
"""

import asyncio
import os
import uuid

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

import common.agents as agents
import temporal_supervisor.chaos as chaos
from common.user_message import ChatInteraction, ProcessUserMessageInput
from live_agent_handoffs import _turn_trace
from temporal_supervisor.activities.clients import ClientActivities, WealthManagementClient
from temporal_supervisor.activities.investments import Investments
from temporal_supervisor.model_config import LLM_TASK_QUEUE
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow

RUNS = max(1, int(os.getenv("LIVE_TEST_RUNS", "1")))
TASK_QUEUE = "repeat-opening-live-test"
REPLY_TIMEOUT_SECONDS = 180
OPEN_VACATION = "Open Vacation with $13,000"
# Client 123's details in data/clients.json
KYC_DETAILS = ["Don", "123 Main Street"]

pytestmark = pytest.mark.skipif(
    os.getenv("OPENAI_API_KEY", "test-key") in ("", "test-key"),
    reason="OPENAI_API_KEY not set: source ./setoaikey.sh first",
)

ENV: dict = {}
INTERACTIONS: list[ChatInteraction] = []
KYC_SHOWN_FOR: list[str] = []  # child workflow IDs whose details were fetched for KYC


@activity.defn(name="append_chat_interaction")
async def memory_append_chat_interaction(workflow_id: str, chat_interaction: ChatInteraction) -> int:
    INTERACTIONS.append(chat_interaction)
    return len(INTERACTIONS)


@activity.defn(name="append_status_update")
async def memory_append_status_update(workflow_id: str, status_update: dict) -> int:
    return 0


@activity.defn(name="delete_conversation")
async def memory_delete_conversation(workflow_id: str) -> bool:
    return True


@activity.defn(name="get_task_queue_open_account")
async def stub_task_queue_open_account() -> str:
    return TASK_QUEUE


@activity.defn(name="get_current_client_info")
async def stub_get_current_client_info(workflow_id: str) -> WealthManagementClient:
    KYC_SHOWN_FOR.append(workflow_id)
    handle = ENV["env"].client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)
    return await handle.execute_update(OpenInvestmentAccountWorkflow.get_client_details)


async def _test_server_handle(workflow_id):
    # approve_client_kyc / update_kyc_details signal the child workflow through this
    return ENV["env"].client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)


async def test_opening_the_same_account_again_starts_a_new_opening(monkeypatch):
    async def chaos_off(_flag):
        return False
    monkeypatch.setattr(chaos, "is_enabled", chaos_off)
    monkeypatch.setattr(agents, "get_workflow_handle", _test_server_handle)

    results = []  # (run, passed, transcript, error)
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        ENV["env"] = env
        worker = Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[WealthManagementWorkflow, OpenInvestmentAccountWorkflow],
            activities=[
                Investments.list_investments,
                Investments.validate_new_account,
                Investments.find_promotion,
                ClientActivities.get_client,
                stub_task_queue_open_account,
                stub_get_current_client_info,
                memory_append_chat_interaction,
                memory_append_status_update,
                memory_delete_conversation,
            ],
            plugins=[PydanticAIPlugin()],
        )
        llm_worker = Worker(
            env.client,
            task_queue=LLM_TASK_QUEUE,
            activities=[a for agent in WealthManagementWorkflow.__pydantic_ai_agents__
                        for a in agent.temporal_activities],
        )
        async with worker, llm_worker:
            for run in range(1, RUNS + 1):
                results.append(await _converse(env, run))

    passed = sum(1 for r in results if r[1])
    print(f"\n=== {passed}/{RUNS} runs passed ===")
    failures = [r for r in results if not r[1]]
    assert not failures, "\n\n".join(f"Run {run} of {RUNS} failed: {error}\n{transcript}"
                                     for run, _, transcript, error in failures)


async def _converse(env: WorkflowEnvironment, run: int):
    KYC_SHOWN_FOR.clear()
    handle = await env.client.start_workflow(
        WealthManagementWorkflow.run, id=f"repeat-opening-live-{run}-{uuid.uuid4()}", task_queue=TASK_QUEUE)
    transcript: list[str] = []

    async def say(text: str) -> str:
        before = len(INTERACTIONS)
        await handle.signal(WealthManagementWorkflow.process_user_message, ProcessUserMessageInput(user_input=text))
        for _ in range(REPLY_TIMEOUT_SECONDS * 10):
            if len(INTERACTIONS) > before:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError(f"no reply to {text!r} within {REPLY_TIMEOUT_SECONDS}s")
        reply = INTERACTIONS[-1].text_response
        history = await handle.query(WealthManagementWorkflow.get_chat_history)
        transcript.append(f"Customer: {text}")
        transcript.append(f"Agent: {reply}")
        # The same message twice: the trace starts at the latest one
        for who, line in _turn_trace(history, text):
            transcript.append(f"    [{who}] {line}")
        return reply

    error = None
    try:
        await say("Who are my beneficiaries?")
        await say("123")
        await say("What investments do I have?")
        first = await say(OPEN_VACATION)
        for detail in KYC_DETAILS:
            assert detail in first, f"first open request: KYC detail {detail!r} not shown"
        await say("Yes")
        second = await say(OPEN_VACATION)
        for detail in KYC_DETAILS:
            assert detail in second, f"second open request: KYC detail {detail!r} not shown"

        started = []
        for n in (1, 2):
            child_id = f"OpenAccount-{handle.id}-123-Vacation-{n}"
            description = await env.client.get_workflow_handle(child_id).describe()
            assert description.parent_id == handle.id
            started.append(child_id)
        assert KYC_SHOWN_FOR == started, f"KYC shown for {KYC_SHOWN_FOR}, expected {started}"
        transcript.append(f"Child workflows started: {', '.join(started)}")
    except Exception as e:  # noqa: BLE001 - a missing child workflow raises RPCError
        error = f"{type(e).__name__}: {e}"
    finally:
        await handle.terminate()

    text = "\n".join(transcript)
    status = "PASSED" if error is None else f"FAILED: {error}"
    print(f"\n--- Run {run} of {RUNS}: {status}\n{text}")
    return run, error is None, text, error
