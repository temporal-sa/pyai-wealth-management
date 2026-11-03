import asyncio
import dataclasses

from datetime import timedelta
from typing import List

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ActivityConfig

from pydantic_ai import Agent, ModelMessage
from pydantic_ai.messages import ModelRequest, UserPromptPart

from pydantic_ai.durable_exec.temporal import (
    PydanticAIWorkflow,
    TemporalAgent,
)

from common.agents import (
    AgentDependencies,
    supervisor_agent,
    beneficiary_agent,
    investment_agent,
    open_account_agent,
)

from common.agent_constants import BENE_AGENT_NAME, INVEST_AGENT_NAME, OPEN_ACCOUNT_AGENT_NAME, SUPERVISOR_AGENT_NAME
from common.user_message import ProcessUserMessageInput, ChatInteraction
from common.account_context import UpdateAccountOpeningStateInput
from common.status_update import StatusUpdate

from temporal_supervisor.activities.event_stream_activities import EventStreamActivities
from temporal_supervisor.activities.local_activities import LocalActivities
from temporal_supervisor.retry_policies import LLM_RETRY_POLICY, TOOL_RETRY_POLICY

with workflow.unsafe.imports_passed_through():
    from temporal_supervisor.model_config import LLM_TASK_QUEUE

# States the account opening workflows (Python and Java) end in
ACCOUNT_OPENING_ENDED = ("Complete", "Rolled back")

def _temporal_agent(agent: Agent, workflow_tools: tuple = ()) -> TemporalAgent:
    # Tool calls and model calls retry for as long as an outage lasts. Model calls
    # run on their own task queue so they can be rate limited without slowing the tools.
    # TemporalAgent adds to the retry policy it is given, so each agent gets its own copy.
    return TemporalAgent(
        agent,
        activity_config=ActivityConfig(
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=dataclasses.replace(TOOL_RETRY_POLICY),
        ),
        model_activity_config=ActivityConfig(
            task_queue=LLM_TASK_QUEUE,
            # Replaces the base policy for model calls, so keep the errors
            # TemporalAgent marks as non-retryable on it.
            retry_policy=dataclasses.replace(
                LLM_RETRY_POLICY, non_retryable_error_types=["UserError", "PydanticUserError"]
            ),
        ),
        # Tools run as activities, so changes they make to deps are lost. These only
        # read or set deps and do no I/O, so they run in the workflow instead.
        tool_activity_config={"<agent>": {name: False for name in workflow_tools}},
    )

temporal_super_agent = _temporal_agent(supervisor_agent, workflow_tools=("get_client_id", "set_client_id"))
temporal_bene_agent = _temporal_agent(beneficiary_agent)
temporal_invest_agent = _temporal_agent(investment_agent)
temporal_open_account_agent = _temporal_agent(open_account_agent)

@workflow.defn
class WealthManagementWorkflow(PydanticAIWorkflow):
    __pydantic_ai_agents__ = [temporal_super_agent, temporal_bene_agent, temporal_invest_agent, temporal_open_account_agent]
    
    def __init__(self):
        self.wf_id = workflow.info().workflow_id
        self.pending_chat_messages: asyncio.Queue = asyncio.Queue()
        self.pending_status_updates: asyncio.Queue = asyncio.Queue()
        self.exit_workflow = False
        self.agent_deps = AgentDependencies()
        self.message_history: List[ModelMessage] = []
        self.sched_to_close_timeout = timedelta(seconds=5)
        self.retry_policy = RetryPolicy(initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2,
                        maximum_interval=timedelta(seconds=30))

    @workflow.run
    async def run(self):
        # Get task queue configuration via local activity (runs outside sandbox)
        task_queue = await workflow.execute_local_activity(
            LocalActivities.get_task_queue_open_account,
            schedule_to_close_timeout=timedelta(seconds=5)
        )
        self.agent_deps.task_queue_open_account = task_queue
        workflow.logger.info(f"Workflow started with task_queue_open_account: {task_queue}")

        while True:
            workflow.logger.info("At the top of the loop - waiting for messages or status updates")

            # wait for a queue item or end workflow
            await workflow.wait_condition(
                lambda: not self.pending_chat_messages.empty() or not self.pending_status_updates.empty() or self.exit_workflow
            )

            if self.exit_workflow:
                workflow.logger.info("Ending workflow.")
                return

            # process chat messages
            user_input = None
            if not self.pending_chat_messages.empty():
                user_input = self.pending_chat_messages.get_nowait()
                await self._process_chat_message(user_input)
                workflow.logger.info("chat message processed.")

            # process status updates
            if not self.pending_status_updates.empty():
                status_message = self.pending_status_updates.get_nowait()
                await self._process_status_update(status_message)
                workflow.logger.info("status update processed.")

           # TODO: Implement Continue as New

    @workflow.query
    def get_chat_history(self) -> list[ModelMessage]:
        return self.message_history

    @workflow.signal
    async def end_workflow(self):
        self.exit_workflow = True

    @workflow.signal
    async def process_user_message(self, message_input: ProcessUserMessageInput):
        workflow.logger.info(f"Received user message {message_input}")
        await self.pending_chat_messages.put(message_input.user_input)

    @workflow.signal
    async def update_account_opening_state(self, state_input: UpdateAccountOpeningStateInput):
        workflow.logger.info(f"Account Opening State changed {state_input.account_name} {state_input.state}")
        await self.pending_status_updates.put(state_input)

    async def _process_chat_message(self, message: str):
        chat_interaction = ChatInteraction(
            user_prompt=message,
            text_response=""
        )

        await self._process_user_message(chat_interaction=chat_interaction, 
            user_input=message)

        # save the history in Redis
        await workflow.execute_local_activity(
            EventStreamActivities.append_chat_interaction,
            args=[workflow.info().workflow_id, chat_interaction],
            schedule_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2,
                    maximum_interval=timedelta(seconds=30))
        )

    async def _process_status_update(self, state_input: UpdateAccountOpeningStateInput):
        status_message = f"New {state_input.account_name} account status changed: {state_input.state}"
        workflow.logger.info(f"processing status update: {status_message}")

        if state_input.state.startswith(ACCOUNT_OPENING_ENDED):
            self._end_account_opening(state_input)

        # TODO: Consider filtering which messages we want to update the client
        status_update = StatusUpdate(status=status_message)
        result = await workflow.execute_local_activity(
            EventStreamActivities.append_status_update,
            args=[self.wf_id, status_update],
            schedule_to_close_timeout=self.sched_to_close_timeout, 
            retry_policy=self.retry_policy,
        )

    def _end_account_opening(self, state_input: UpdateAccountOpeningStateInput):
        # The status updates only reach the UI, so tell the agents how the account
        # opening ended. Otherwise they still believe a rolled back account was opened.
        # Not a SystemPromptPart: pydantic-ai places the agent's instructions after the
        # first N messages, N being the number of system messages, which can split a tool
        # call from its result.
        self.message_history.append(ModelRequest(parts=[UserPromptPart(
            content=f"[Automated status update, not from the customer] The {state_input.account_name} "
                    f"account opening has ended. Final state: {state_input.state}",
            timestamp=workflow.now(),
        )]))

        active = self.agent_deps.active_account_workflow_id
        if active and active.endswith(f"-{state_input.account_name}"):
            self.agent_deps.active_account_workflow_id = None
            self.agent_deps.current_client_info = None
            self.agent_deps.kyc_pending = False
            # Nothing is left for the Open Account Agent to do, so the next
            # message goes to the supervisor to be routed.
            if self.agent_deps.current_agent_name == OPEN_ACCOUNT_AGENT_NAME:
                self.agent_deps.current_agent_name = SUPERVISOR_AGENT_NAME

    async def _process_user_message(self, chat_interaction: ChatInteraction, user_input: str):
        workflow.logger.info(f"Processing user message of {user_input}")

        # Handoffs quote the customer's own words to the next agent
        self.agent_deps.customer_message = user_input

        # The first agent run adds the user input to the history, so it is only
        # added to the copy the tools see until then.
        unrecorded = [ModelRequest(
            parts=[UserPromptPart(
                content=user_input,
                timestamp=workflow.now()
            )]
        )]

        current_agent = self._get_current_agent()
        current_input = user_input

        response = ""
        # Loop to handle chain routing
        while True:
            # Sync message history to deps BEFORE running agent, including the user input
            # (so tools can access confirmation messages in context.deps.message_history)
            self.agent_deps.message_history = self.message_history + unrecorded

            workflow.logger.info(f"Running the current agent of {current_agent} with {current_input}")

            # Run the current agent
            result = await current_agent.run(
                current_input,
                deps=self.agent_deps,
                message_history=self.message_history
            )

            # Add agent's new messages to history
            self.message_history.extend(result.new_messages())
            unrecorded = []

            # Check if output function signaled a route
            if self.agent_deps.next_agent:
                # Routing detected - switch to next agent
                workflow.logger.info(f"\n>>> Routing: {self.agent_deps.current_agent_name} → {self.agent_deps.next_agent}")

                self.agent_deps.current_agent_name = self.agent_deps.next_agent
                current_agent = self._get_current_agent()
                current_input = self.agent_deps.trigger_message

                # Clear routing state
                self.agent_deps.next_agent = None
                self.agent_deps.trigger_message = None

                # Continue loop to process next agent
                continue
            else:
                # No routing - print final response and exit loop
                if result.output and result.output.strip():
                    response = result.output
                break

        # update the chat interaction
        workflow.logger.info("Getting ready to set response to {response}")
        chat_interaction.text_response = response

    def _get_current_agent(self) -> Agent:
        """Get the agent instance based on current_agent_name."""
        if self.agent_deps.current_agent_name == BENE_AGENT_NAME:
            return temporal_bene_agent
        elif self.agent_deps.current_agent_name == INVEST_AGENT_NAME:
            return temporal_invest_agent
        elif self.agent_deps.current_agent_name == OPEN_ACCOUNT_AGENT_NAME:
            return temporal_open_account_agent
        else:
            return temporal_super_agent        