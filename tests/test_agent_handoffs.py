"""Tests for the checks that keep the agents on track between turns.

They call the output functions directly with a hand-built RunContext, so no model,
Temporal server or Redis is needed.
"""

import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from common.agents import (
    AgentDependencies,
    customer_said,
    respond_about_account_opening,
    route_from_investment_to_supervisor,
)

KYC_QUESTION = ModelResponse(parts=[TextPart("Is this information correct and up to date? Please confirm.")])


def _ctx(customer_message: str, messages: list, kyc_pending: bool = True) -> RunContext[AgentDependencies]:
    deps = AgentDependencies(
        client_id="123",
        customer_message=customer_message,
        kyc_pending=kyc_pending,
        active_account_workflow_id="OpenAccount-test-123-Vacation",
    )
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage(), messages=messages)


def _turn(customer_message: str, *parts) -> list:
    """History up to the KYC question, then this turn's prompt and model output."""
    return [KYC_QUESTION, ModelRequest(parts=[UserPromptPart(customer_message)]), *parts]


@pytest.mark.parametrize("reply", ["Yes", "yes, that's correct", "That's right", "Correct", "ok"])
async def test_confirmation_without_approving_kyc_is_sent_back(reply):
    ctx = _ctx(reply, _turn(reply))
    with pytest.raises(ModelRetry, match="approve_client_kyc"):
        await respond_about_account_opening(ctx, "Your KYC information has been approved.")
    assert ctx.deps.kyc_pending


async def test_confirmation_after_approving_kyc_goes_through():
    messages = _turn(
        "Yes, that's correct",
        ModelResponse(parts=[ToolCallPart("approve_client_kyc", {})]),
        ModelRequest(parts=[ToolReturnPart("approve_client_kyc", "KYC information approved.")]),
    )
    ctx = _ctx("Yes, that's correct", messages)
    reply = "Your KYC information has been approved. Your account is now pending compliance review."
    assert await respond_about_account_opening(ctx, reply) == reply
    assert not ctx.deps.kyc_pending


async def test_approval_in_the_same_response_counts():
    # With end_strategy='exhaustive' the tool runs alongside the output function.
    messages = _turn("Yes", ModelResponse(parts=[ToolCallPart("approve_client_kyc", {})]))
    ctx = _ctx("Yes", messages)
    await respond_about_account_opening(ctx, "Your KYC information has been approved.")
    assert not ctx.deps.kyc_pending


async def test_an_approval_from_an_earlier_turn_does_not_count():
    earlier = [
        ModelRequest(parts=[UserPromptPart("Yes")]),
        ModelResponse(parts=[ToolCallPart("approve_client_kyc", {})]),
        ModelRequest(parts=[ToolReturnPart("approve_client_kyc", "KYC information approved.")]),
    ]
    ctx = _ctx("Yes", earlier + _turn("Yes"))
    with pytest.raises(ModelRetry):
        await respond_about_account_opening(ctx, "Your KYC information has been approved.")


async def test_asking_what_to_update_needs_no_tool():
    ctx = _ctx("No, that's not right", _turn("No, that's not right"))
    reply = "Which information needs to be updated? Please let me know what needs to be changed."
    assert await respond_about_account_opening(ctx, reply) == reply
    assert ctx.deps.kyc_pending


async def test_replies_are_not_checked_once_kyc_is_done():
    ctx = _ctx("Yes", _turn("Yes"), kyc_pending=False)
    assert await respond_about_account_opening(ctx, "Thanks!") == "Thanks!"


async def test_handoffs_quote_the_customer_word_for_word():
    ctx = _ctx("Please open a new investment account called Vacation with $5000", [], kyc_pending=False)
    assert '"Please open a new investment account called Vacation with $5000"' in customer_said(ctx)

    await route_from_investment_to_supervisor(ctx, "123")
    assert "Vacation with $5000" in ctx.deps.trigger_message
