import asyncio

from datetime import timedelta
from typing import List

from temporalio import workflow
from temporalio.common import RetryPolicy

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
    investment_agent
)

from common.agent_constants import BENE_AGENT_NAME, INVEST_AGENT_NAME
from common.user_message import ProcessUserMessageInput, ChatInteraction

from temporal_supervisor.activities.event_stream_activities import EventStreamActivities

temporal_super_agent = TemporalAgent(supervisor_agent)
temporal_bene_agent = TemporalAgent(beneficiary_agent)
temporal_invest_agent = TemporalAgent(investment_agent)

@workflow.defn
class WealthManagementWorkflow(PydanticAIWorkflow):
    __pydantic_ai_agents__ = [temporal_super_agent, temporal_bene_agent, temporal_invest_agent]
    
    def __init__(self):
        self.pending_chat_messages: asyncio.Queue = asyncio.Queue()
        self.exit_workflow = False
        self.agent_deps = AgentDependencies()
        self.message_history: List[ModelMessage] = []

    @workflow.run
    async def run(self):
        while True:
            workflow.logger.info("At the top of the loop - waiting for messages or status updates")

            # wait for a queue item or end workflow
            await workflow.wait_condition(
                lambda: not self.pending_chat_messages.empty() or self.exit_workflow
            )

            if self.exit_workflow:
                workflow.logger.info("Ending workflow.")
                return

            # process chat messages
            user_input = None
            if not self.pending_chat_messages.empty():
                user_input = self.pending_chat_messages.get_nowait()
                workflow.logger.info(f"Message pulled from queue is {user_input}")

            chat_interaction = ChatInteraction(
                user_prompt=user_input,
                text_response=""
            )

            await self._process_user_message(chat_interaction=chat_interaction, 
                user_input=user_input)

            # save the history in Redis
            await workflow.execute_local_activity(
                EventStreamActivities.append_chat_interaction,
                args=[workflow.info().workflow_id, chat_interaction],
                schedule_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2,
                        maximum_interval=timedelta(seconds=30))
            )

    @workflow.query
    def get_chat_history(self) -> list[ModelMessage]:
        return self.message_history

    @workflow.signal
    async def end_workflow(self):
        self.exit_workflow = True

    @workflow.signal
    async def process_user_message(self, message_input: ProcessUserMessageInput):
        workflow.logger.info(f"processing user message {message_input}")
        await self.pending_chat_messages.put(message_input.user_input)

    async def _process_user_message(self, chat_interaction: ChatInteraction, user_input: str):
        workflow.logger.info(f"Processing user message of {user_input}")

        # Add user input to history before running agent
        user_message = ModelRequest(
            parts=[UserPromptPart(
                content=user_input, 
                timestamp=workflow.now()
            )]
        )
        self.message_history.append(user_message)

        # Start with supervisor agent
        # Start with supervisor agent
        current_agent = self._get_current_agent()
        current_input = user_input

        response = ""
        # Loop to handle chain routing
        while True:
            # Sync message history to deps BEFORE running agent
            # (so tools can access confirmation messages in context.deps.message_history)
            self.agent_deps.message_history = self.message_history

            workflow.logger.info(f"Running the current agent of {current_agent} with {current_input}")

            # Run the current agent
            result = await current_agent.run(
                current_input,
                deps=self.agent_deps,
                message_history=self.message_history
            )
            workflow.logger.info("Done running the current agent.")

            # Add agent's new messages to history
            self.message_history.extend(result.new_messages())

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
        else:
            return temporal_super_agent        