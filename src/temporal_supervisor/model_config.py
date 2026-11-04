"""Model and task queue configuration for the Temporal + Pydantic AI worker.

The agents' model calls run as activities on their own task queue so they can be
rate limited (and scaled) independently of the tools. See run_worker.py.
"""

import os
from datetime import timedelta
from typing import Any, Optional

from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model, cached_async_http_client
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai import OpenAIProvider
from temporalio.exceptions import ApplicationError

from common.chaos_manager import ChaosFlag
from temporal_supervisor.chaos import inject_failure

# Same default as ClientHelper.taskQueue.
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "PY-AI-Supervisor")
# TEMPORAL_TASK_QUEUE_LLM is the name the OpenAI Agents SDK version uses;
# TEMPORAL_LLM_TASK_QUEUE is still accepted.
LLM_TASK_QUEUE = (os.getenv("TEMPORAL_TASK_QUEUE_LLM")
                  or os.getenv("TEMPORAL_LLM_TASK_QUEUE")
                  or f"{TASK_QUEUE}-llm")


def _optional_float(name: str) -> Optional[float]:
    value = os.getenv(name)
    return float(value) if value else None


# Proactive rate limiting: the most model calls per second the Temporal service
# will dispatch from LLM_TASK_QUEUE, across every worker. Unset means no limit.
LLM_MAX_CALLS_PER_SECOND = _optional_float("LLM_MAX_CALLS_PER_SECOND")

# How long a rate-limited model call waits before its next attempt, as if the
# provider had returned a Retry-After header.
RATE_LIMIT_RETRY_AFTER = timedelta(seconds=15)


def _rate_limited(message: str) -> ApplicationError:
    return ApplicationError(
        message,
        type="ModelRateLimited",
        next_retry_delay=RATE_LIMIT_RETRY_AFTER,
    )


def agent_model(model_name: str) -> Model | str:
    """The model the agents call.

    OpenAI models get a client that never retries on its own, so Temporal's
    activity retry policy (LLM_RETRY_POLICY) alone decides how a failed model
    call is retried. Other model names are left for Pydantic AI to resolve.
    """
    provider_name, _, name = model_name.partition(":")
    if provider_name != "openai":
        return model_name
    openai_client = AsyncOpenAI(max_retries=0, http_client=cached_async_http_client(provider="openai"))
    return OpenAIChatModel(name, provider=OpenAIProvider(openai_client=openai_client))


class ChaosAwareModel(WrapperModel):
    """Wraps the agents' model to honor the demo's chaos flags and handle throttling.

    TemporalAgent calls request() inside its model activity, so raising here fails
    the activity and Temporal retries the model call according to LLM_RETRY_POLICY.
    The agents use agent.run() without an event stream handler, so request_stream()
    is never called.
    """

    async def request(self, *args: Any, **kwargs: Any) -> ModelResponse:
        await inject_failure(ChaosFlag.LLM_DOWN, ConnectionError("OpenAI API unavailable"))
        await inject_failure(
            ChaosFlag.LLM_RATE_LIMITED, _rate_limited("OpenAI API rate limit exceeded (HTTP 429)")
        )
        try:
            return await super().request(*args, **kwargs)
        except ModelHTTPError as e:
            # A real 429 from OpenAI gets the same treatment.
            if e.status_code == 429:
                raise _rate_limited(f"OpenAI API throttled the request: {e}") from e
            raise
