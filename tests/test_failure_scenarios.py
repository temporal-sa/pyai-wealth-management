"""Tests for the failure scenarios demonstrated from the UI chaos panel.

The saga tests run the real ``OpenInvestmentAccountWorkflow`` against stub
activities (registered under the real activity names) that record the order they
were called in and fail on demand, so no Redis or data files are touched.

Requires the Temporal dev server (downloaded automatically on first run). No
OpenAI API key or Redis needed.
"""

import asyncio
import json
import uuid
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowExecutionStatus
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

import temporal_supervisor.chaos as chaos
import temporal_supervisor.model_config as model_config
from common.chaos_manager import ChaosFlag, ChaosManager
from common.domain_classes import OpenInvestmentAccountInput
from common.funding_manager import FundingManager, InsufficientFundsError
from temporal_supervisor.activities.investments import Investments
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow

TASK_QUEUE = "test-failure-scenarios"
CLIENT_ID = "C-1"
INVESTMENT_ID = "i-test"

# Activity calls, in order, recorded worker-side (same process as the test).
CALLS: list = []
# Per-test failure behavior, keyed by activity name: "reject" raises a
# non-retryable error; an int N fails the first N attempts with a transient error.
FAILURES: dict = {}


def _maybe_fail(name: str) -> None:
    behavior = FAILURES.get(name)
    if behavior == "reject":
        raise ApplicationError(f"{name} rejected", type="Rejected", non_retryable=True)
    if isinstance(behavior, int) and activity.info().attempt <= behavior:
        raise ConnectionError(f"{name} unavailable")


@activity.defn(name="get_client")
async def stub_get_client(client_id: str) -> dict:
    CALLS.append("get_client")
    return {
        "client_id": client_id, "first_name": "Sam", "last_name": "Doe", "address": "1 Main St",
        "phone": "555", "email": "s@example.com", "marital_status": "single",
    }


@activity.defn(name="open_investment")
async def stub_open_investment(new_account: Any) -> dict:
    CALLS.append(f"open_investment#{activity.info().attempt}")
    _maybe_fail("open_investment")
    return {"investment_id": INVESTMENT_ID, "name": "Test", "balance": 500.0}


@activity.defn(name="remove_investment")
async def stub_remove_investment(client_id: str, investment_id: str) -> bool:
    CALLS.append(f"remove_investment:{investment_id}")
    return True


@activity.defn(name="debit_linked_account")
async def stub_debit(client_id: str, amount: float, transfer_id: str) -> dict:
    CALLS.append(f"debit:{amount}")
    _maybe_fail("debit_linked_account")
    return {}


@activity.defn(name="credit_linked_account")
async def stub_credit(client_id: str, amount: float, transfer_id: str) -> dict:
    CALLS.append(f"credit:{amount}")
    assert transfer_id.endswith("-refund")
    return {}


@activity.defn(name="register_account")
async def stub_register(client_id: str, investment_id: str, amount: float) -> str:
    CALLS.append("register")
    _maybe_fail("register_account")
    return "CUST-1"


STUB_ACTIVITIES = [
    stub_get_client, stub_open_investment, stub_remove_investment,
    stub_debit, stub_credit, stub_register,
]


@pytest.fixture(scope="module")
async def temporal_env():
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        yield env


async def _start_and_approve(env: WorkflowEnvironment, failures: dict, promo_code=None):
    CALLS.clear()
    FAILURES.clear()
    FAILURES.update(failures)
    handle = await env.client.start_workflow(
        OpenInvestmentAccountWorkflow.run,
        OpenInvestmentAccountInput(
            client_id=CLIENT_ID, account_name="Test", initial_amount=500.0, promo_code=promo_code
        ),
        id=f"open-account-test-{uuid.uuid4()}",
        task_queue=TASK_QUEUE,
    )
    await handle.signal(OpenInvestmentAccountWorkflow.verify_kyc)
    await handle.signal(OpenInvestmentAccountWorkflow.compliance_approved)
    return handle


def _worker(env: WorkflowEnvironment) -> Worker:
    return Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[OpenInvestmentAccountWorkflow],
        activities=STUB_ACTIVITIES,
    )


async def _open_account(env: WorkflowEnvironment, failures: dict):
    async with _worker(env):
        handle = await _start_and_approve(env, failures)
        result = await handle.result()
        state = await handle.query(OpenInvestmentAccountWorkflow.get_current_state)
        return result, state


async def test_saga_happy_path(temporal_env):
    result, state = await _open_account(temporal_env, {})
    assert result.account_created
    assert state == "Complete"
    assert CALLS == ["get_client", "open_investment#1", "debit:500.0", "register"]


async def test_saga_rolls_back_in_reverse_when_custodian_rejects(temporal_env):
    result, state = await _open_account(temporal_env, {"register_account": "reject"})
    assert not result.account_created
    assert "register_account rejected" in result.message
    assert state.startswith("Rolled back")
    assert CALLS == [
        "get_client", "open_investment#1", "debit:500.0", "register",
        "credit:500.0", f"remove_investment:{INVESTMENT_ID}",
    ]


async def test_saga_undoes_only_completed_steps(temporal_env):
    result, _ = await _open_account(temporal_env, {"debit_linked_account": "reject"})
    assert not result.account_created
    # The debit never happened, so there is nothing to refund.
    assert CALLS == [
        "get_client", "open_investment#1", "debit:500.0", f"remove_investment:{INVESTMENT_ID}",
    ]


async def test_transient_failures_are_retried_not_rolled_back(temporal_env):
    result, _ = await _open_account(temporal_env, {"open_investment": 2})
    assert result.account_created
    assert CALLS == [
        "get_client", "open_investment#1", "open_investment#2", "open_investment#3",
        "debit:500.0", "register",
    ]


async def test_promo_code_path_bug_pauses_workflow_instead_of_failing_it(temporal_env):
    async with _worker(temporal_env):
        handle = await _start_and_approve(temporal_env, {}, promo_code="WELCOME-BONUS")
        failure = None
        for _ in range(40):
            async for event in handle.fetch_history_events():
                if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED:
                    failure = event.workflow_task_failed_event_attributes.failure.message
            if failure:
                break
            await asyncio.sleep(0.25)

        assert failure and "initial_deposit" in failure, "the demo bug should fail the workflow task"
        # Paused, not failed: still running, and the saga never started.
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        assert "open_investment#1" not in CALLS
        await handle.terminate()


@pytest.mark.parametrize(
    "amount, error_type",
    [(50.0, "DepositBelowMinimum"), (2_000_000.0, "DepositLimitExceeded")],
)
async def test_business_rule_violations_are_non_retryable(amount, error_type):
    with pytest.raises(ApplicationError) as exc_info:
        await ActivityEnvironment().run(Investments.validate_new_account, CLIENT_ID, "Test", amount)
    assert exc_info.value.type == error_type
    assert exc_info.value.non_retryable


async def test_valid_deposit_passes_validation():
    await ActivityEnvironment().run(Investments.validate_new_account, CLIENT_ID, "Test", 5_000.0)


def test_funding_transfers_are_idempotent(tmp_path):
    funding_file = tmp_path / "funding.json"
    funding_file.write_text(json.dumps({"accounts": {CLIENT_ID: {"name": "Bank", "balance": 1000.0}}}))

    FundingManager(funding_file).debit(CLIENT_ID, 400.0, "t-1")
    FundingManager(funding_file).debit(CLIENT_ID, 400.0, "t-1")  # a retry of the same transfer
    assert FundingManager(funding_file).get_account(CLIENT_ID)["balance"] == 600.0

    with pytest.raises(InsufficientFundsError):
        FundingManager(funding_file).debit(CLIENT_ID, 700.0, "t-2")

    FundingManager(funding_file).credit(CLIENT_ID, 400.0, "t-1-refund")
    FundingManager(funding_file).credit(CLIENT_ID, 400.0, "t-1-refund")
    assert FundingManager(funding_file).get_account(CLIENT_ID)["balance"] == 1000.0


def _model_returning(status_code=None) -> model_config.ChaosAwareModel:
    def respond(_messages, _info):
        if status_code:
            raise ModelHTTPError(status_code=status_code, model_name="test")
        return ModelResponse(parts=[TextPart("ok")])

    return model_config.ChaosAwareModel(FunctionModel(respond))


async def _request(model: model_config.ChaosAwareModel) -> ModelResponse:
    return await model.request([], None, ModelRequestParameters())


@pytest.fixture
def chaos_flags(monkeypatch):
    """The set of chaos flags that are on, without Redis."""
    enabled: set = set()

    async def is_enabled(flag):
        return flag in enabled

    async def record_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(chaos, "is_enabled", is_enabled)
    monkeypatch.setattr(ChaosManager, "record_event", record_event)
    return enabled


async def test_openai_429_waits_for_retry_after(chaos_flags):
    with pytest.raises(ApplicationError) as exc_info:
        await ActivityEnvironment().run(_request, _model_returning(429))
    assert exc_info.value.type == "ModelRateLimited"
    assert exc_info.value.next_retry_delay == model_config.RATE_LIMIT_RETRY_AFTER
    assert not exc_info.value.non_retryable


async def test_other_openai_errors_are_not_treated_as_rate_limits(chaos_flags):
    with pytest.raises(ModelHTTPError):
        await ActivityEnvironment().run(_request, _model_returning(500))


async def test_model_chaos_flags(chaos_flags):
    model = _model_returning()
    assert (await ActivityEnvironment().run(_request, model)).parts[0].content == "ok"

    chaos_flags.add(ChaosFlag.LLM_RATE_LIMITED)
    with pytest.raises(ApplicationError) as exc_info:
        await ActivityEnvironment().run(_request, model)
    assert exc_info.value.next_retry_delay == model_config.RATE_LIMIT_RETRY_AFTER

    chaos_flags.add(ChaosFlag.LLM_DOWN)
    with pytest.raises(ConnectionError):
        await ActivityEnvironment().run(_request, model)


async def test_tool_down_fails_investment_activities(chaos_flags):
    chaos_flags.add(ChaosFlag.TOOL_DOWN)
    with pytest.raises(ConnectionError):
        await ActivityEnvironment().run(Investments.list_investments, CLIENT_ID)


def test_agents_wait_out_outages_and_call_the_model_on_the_llm_queue():
    for agent in WealthManagementWorkflow.__pydantic_ai_agents__:
        # Tools: one attempt is bounded, but retries are unlimited and back off to 5s at most.
        tool_config = agent.activity_config
        assert "schedule_to_close_timeout" not in tool_config
        assert tool_config["retry_policy"].maximum_attempts == 0
        assert tool_config["retry_policy"].maximum_interval.total_seconds() == 5

        model_config_ = agent.model.activity_config
        assert model_config_["task_queue"] == model_config.LLM_TASK_QUEUE
        assert model_config_["retry_policy"].maximum_attempts == 0
        assert model_config_["retry_policy"].maximum_interval.total_seconds() == 10


async def test_supervisor_workflow_passes_sandbox_validation(temporal_env):
    # The agents now import the chaos helpers and the model wrapper. Building the
    # worker loads each workflow in the sandbox, so an import the sandbox rejects
    # fails here rather than when the first chat starts.
    from pydantic_ai.durable_exec.temporal import PydanticAIPlugin

    async with Worker(
        temporal_env.client,
        task_queue=f"{TASK_QUEUE}-supervisor",
        workflows=[WealthManagementWorkflow, OpenInvestmentAccountWorkflow],
        plugins=[PydanticAIPlugin()],
    ):
        pass
