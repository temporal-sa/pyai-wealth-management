"""Live test of the agent handoffs, against the real OpenAI model.

A customer starts with the Beneficiary Agent, then asks about their investments. The
Beneficiary Agent should route back to the supervisor (route_from_beneficiary_to_supervisor),
which routes to the Investment Agent (route_to_investment_agent), all in the same turn, so the
reply lists the investment accounts. It must not ask the customer whether they want to be
transferred, nor announce the transfer.

The model decides whether to route, so this can't be checked with a scripted model. It runs the
real WealthManagementWorkflow with real OpenAI calls (on the LLM task queue, like run_worker.py)
and the real beneficiary and investment activities, which only read data/*.json. Only the Redis
event stream activities are replaced with in-memory stand-ins, and the chaos flags read as off,
so neither Redis nor the UI is needed.

Not part of the default `uv run pytest` run (the file name doesn't match test_*.py), because it
calls the paid OpenAI API. Run it with:
    source ./setoaikey.sh && uv run pytest tests/live_agent_handoffs.py -s
LIVE_TEST_RUNS=<n> repeats the conversation n times (default 1), since model output varies.
Requires the Temporal dev server (downloaded automatically on first run).
"""

import asyncio
import os
import re
import uuid

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

import temporal_supervisor.chaos as chaos
from common.agent_constants import (
    BENE_AGENT_NAME,
    BENE_INSTRUCTIONS,
    INVEST_AGENT_NAME,
    INVEST_INSTRUCTIONS,
    OPEN_ACCOUNT_AGENT_NAME,
    OPEN_ACCOUNT_INSTRUCTIONS,
    SUPERVISOR_AGENT_NAME,
    SUPERVISOR_INSTRUCTIONS,
)
from common.user_message import ChatInteraction, ProcessUserMessageInput
from temporal_supervisor.activities.investments import Investments
from temporal_supervisor.activities.local_activities import LocalActivities
from temporal_supervisor.model_config import LLM_TASK_QUEUE
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow

RUNS = max(1, int(os.getenv("LIVE_TEST_RUNS", "1")))
TASK_QUEUE = "agent-handoffs-live-test"
REPLY_TIMEOUT_SECONDS = 180
# Client 123's accounts in data/investments.json and beneficiaries in data/beneficiaries.json
CLIENT_123_ACCOUNTS = ["Checking", "Savings", "401K"]
CLIENT_123_BENEFICIARIES = ["John", "Jane", "Joan"]
# Any talk of moving the customer to another agent
CHATTER = re.compile(r"transfer|hand(ing)?[ -]?(you )?off|connect(ing)? you|redirect|route you", re.IGNORECASE)

# conftest.py sets a dummy key so the agents can be built without one
pytestmark = pytest.mark.skipif(
    os.getenv("OPENAI_API_KEY", "test-key") in ("", "test-key"),
    reason="OPENAI_API_KEY not set: source ./setoaikey.sh first",
)

# Each agent's requests carry its instructions (as Pydantic AI reformats them), which tells
# us who said what. Matched by a line that only that agent's instructions contain.
AGENT_BY_INSTRUCTIONS = {
    "# Supervisor Agent (Router)": SUPERVISOR_AGENT_NAME,
    "You are a beneficiary agent": BENE_AGENT_NAME,
    "You are an investment agent": INVEST_AGENT_NAME,
    "# Open Account Agent": OPEN_ACCOUNT_AGENT_NAME,
}
assert all(marker in instructions for marker, instructions in zip(AGENT_BY_INSTRUCTIONS, (
    SUPERVISOR_INSTRUCTIONS, BENE_INSTRUCTIONS, INVEST_INSTRUCTIONS, OPEN_ACCOUNT_INSTRUCTIONS)))
# Fallback: the agent that owns a tool or output function (output functions are called as
# final_result_<name>).
AGENT_BY_TOOL = {
    "respond_to_user": SUPERVISOR_AGENT_NAME,
    "route_to_beneficiary_agent": SUPERVISOR_AGENT_NAME,
    "route_to_investment_agent": SUPERVISOR_AGENT_NAME,
    "get_client_id": SUPERVISOR_AGENT_NAME,
    "set_client_id": SUPERVISOR_AGENT_NAME,
    "respond_about_beneficiaries": BENE_AGENT_NAME,
    "route_from_beneficiary_to_supervisor": BENE_AGENT_NAME,
    "list_beneficiaries": BENE_AGENT_NAME,
    "respond_about_investments": INVEST_AGENT_NAME,
    "route_from_investment_to_supervisor": INVEST_AGENT_NAME,
    "route_from_investment_to_open_account": INVEST_AGENT_NAME,
    "list_investments": INVEST_AGENT_NAME,
}

# Event stream (Redis) stand-ins: the conversation is kept in memory.
INTERACTIONS: list[ChatInteraction] = []


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


def _tool(part: ToolCallPart) -> str:
    return part.tool_name.removeprefix("final_result_")


def _turn_trace(history: list, customer_message: str) -> list[tuple[str, str]]:
    """(agent, what it did) for each model response in the turn that began with customer_message."""
    start = max(i for i, m in enumerate(history) if isinstance(m, ModelRequest) and any(
        isinstance(p, UserPromptPart) and p.content == customer_message for p in m.parts))
    trace = []
    agent = "?"
    for message in history[start:]:
        if isinstance(message, ModelRequest):
            agent = next((name for marker, name in AGENT_BY_INSTRUCTIONS.items()
                          if marker in (message.instructions or "")), agent)
            for part in message.parts:
                if isinstance(part, UserPromptPart) and part.content != customer_message:
                    trace.append((agent, f"<input> {part.content!r}"))
            continue
        if not isinstance(message, ModelResponse):
            continue
        owner = next((AGENT_BY_TOOL[_tool(p)] for p in message.parts
                      if isinstance(p, ToolCallPart) and _tool(p) in AGENT_BY_TOOL), agent)
        for part in message.parts:
            if isinstance(part, TextPart) and part.content.strip():
                trace.append((owner, f"text: {part.content!r}"))
            elif isinstance(part, ToolCallPart):
                trace.append((owner, f"call {_tool(part)}({part.args_as_json_str()})"))
    return trace


def _said(trace: list[tuple[str, str]], agent: str) -> str:
    """Everything the agent wrote in the turn: text, and the replies passed to its respond_* function."""
    return "\n".join(line for who, line in trace
                     if who == agent and (line.startswith("text:") or line.startswith("call respond_")))


async def test_investment_question_asked_while_talking_to_the_beneficiary_agent(monkeypatch):
    # Chaos flags off whatever Redis holds (they would fail open without Redis anyway)
    async def chaos_off(_flag):
        return False
    monkeypatch.setattr(chaos, "is_enabled", chaos_off)

    results = []  # (run, passed, chatter, transcript, error)
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        worker = Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[WealthManagementWorkflow],
            activities=[
                Investments.list_investments,
                LocalActivities.get_task_queue_open_account,
                memory_append_chat_interaction,
                memory_append_status_update,
                memory_delete_conversation,
            ],
            plugins=[PydanticAIPlugin()],
        )
        # Model calls run on their own queue, as in run_worker.py
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
    chatter = sum(1 for r in results if r[2])
    print(f"\n=== {passed}/{RUNS} runs passed; {chatter} with transfer chatter ===")
    failures = [r for r in results if not r[1]]
    assert not failures, "\n\n".join(f"Run {run} of {RUNS} failed: {error}\n{transcript}"
                                     for run, _, _, transcript, error in failures)


async def _converse(env: WorkflowEnvironment, run: int):
    handle = await env.client.start_workflow(
        WealthManagementWorkflow.run, id=f"agent-handoffs-live-{run}-{uuid.uuid4()}", task_queue=TASK_QUEUE)
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
        for who, line in _turn_trace(history, text):
            transcript.append(f"    [{who}] {line}")
        return reply, _turn_trace(history, text)

    chatter = False
    error = None
    try:
        await say("Who are my beneficiaries?")
        beneficiaries, _ = await say("123")
        for name in CLIENT_123_BENEFICIARIES:
            assert name in beneficiaries, f"beneficiary {name} not listed"

        investments, trace = await say("What investments do I have?")
        bene_said = _said(trace, BENE_AGENT_NAME)
        chatter = any(CHATTER.search(_said(trace, agent)) for agent in AGENT_BY_TOOL.values()) \
            or bool(CHATTER.search(investments))
        for account in CLIENT_123_ACCOUNTS:
            assert account in investments, f"account {account} not listed"
        assert not re.search(r"transfer", investments, re.IGNORECASE), "the reply talks about a transfer"
        # The Beneficiary Agent should route without announcing it, even though the reply it
        # wrote would be hidden by the Investment Agent's
        assert not re.search(r"transfer", bene_said, re.IGNORECASE), \
            "the Beneficiary Agent talked about a transfer instead of just routing"
        assert any(who == BENE_AGENT_NAME and "route_from_beneficiary_to_supervisor" in line for who, line in trace), \
            "the Beneficiary Agent did not route back to the supervisor"
        assert any("route_to_investment_agent" in line for _, line in trace), \
            "the supervisor did not route to the Investment Agent"
        assert any(who == INVEST_AGENT_NAME and "respond_about_investments" in line for who, line in trace), \
            "the Investment Agent did not answer"
        # Asked once, at the start
        assert not re.search(r"client[ _-]?id", investments, re.IGNORECASE), "asked for the client ID again"
    except AssertionError as e:
        error = str(e)
    finally:
        await handle.signal(WealthManagementWorkflow.end_workflow)
        await handle.result()

    text = "\n".join(transcript)
    status = "PASSED" if error is None else f"FAILED: {error}"
    print(f"\n--- Run {run} of {RUNS}: {status}{' (transfer chatter)' if chatter else ''}\n{text}")
    return run, error is None, chatter, text, error
