import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from pydantic_ai import Agent, ModelMessage
from pydantic_ai.messages import ModelRequest, UserPromptPart

from pydantic_ai.durable_exec.temporal import (
    PydanticAIPlugin,
    PydanticAIWorkflow,
    TemporalAgent,
)

from py_supervisor.main import (
    supervisor_agent, 
    beneficiary_agent, 
    investment_agent,
    AgentDependencies,
    validate_beneficiary_response,
    validate_investment_response,
    HandoffInformation,
) 

from common.agent_constants import SUPERVISOR_AGENT_NAME, BENE_AGENT_NAME, INVEST_AGENT_NAME
from common.user_message import ProcessUserMessageInput, ChatInteraction

from temporal_supervisor.activities.event_stream_activities import EventStreamActivities

SUPERVISOR_AGENT_TOOL_ACTIVITY_CONFIG: dict[str, dict[str, ActivityConfig | Literal[False]]] = {
    "set_client_id": False,
}

temporal_super_agent = TemporalAgent(supervisor_agent, 
    tool_activity_config=SUPERVISOR_AGENT_TOOL_ACTIVITY_CONFIG)

temporal_bene_agent = TemporalAgent(beneficiary_agent)
temporal_invest_agent = TemporalAgent(investment_agent)

@workflow.defn
class WealthManagementWorkflow(PydanticAIWorkflow):
    __pydantic_ai_agents__ = [temporal_super_agent, temporal_bene_agent, temporal_invest_agent]
    
    def __init__(self):
        self.pending_chat_messages: asyncio.Queue = asyncio.Queue()
        self.end_workflow = False
        self.agent_deps = AgentDependencies()
        self.message_history : List[ModelMessage] = []
        self.current_agent = temporal_super_agent
        self.current_agent_name = SUPERVISOR_AGENT_NAME
        self.pending_input: str | None = None # What to feed into the agent this iteration

    @workflow.run
    async def run(self):
        while True:
            workflow.logger.info("At the top of the loop - waiting for messages or status updates")

            # wait for a queue item or end workflow
            await workflow.wait_condition(
                lambda: not self.pending_chat_messages.empty() or self.end_workflow
            )

            if self.end_workflow:
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
        self.end_workflow = True

    @workflow.signal
    async def process_user_message(self, message_input: ProcessUserMessageInput):
        workflow.logger.info(f"processing user message {message_input}")
        await self.pending_chat_messages.put(message_input.user_input)

    async def _check_for_forced_handoff(self, user_input: str):
        should_force_handoff = False
        result = None
        if self.current_agent_name == INVEST_AGENT_NAME and any(keyword in user_input.lower() for keyword in ['beneficiary', 'beneficiaries']):
            workflow.logger.info(f"\n>>> Forced handoff detected: Investment agent cannot handle beneficiary requests")
            should_force_handoff = True
            handoff = HandoffInformation(next_agent="Supervisor Agent", client_id=self.agent_deps.client_id)
        elif self.current_agent_name == BENE_AGENT_NAME and any(keyword in user_input.lower() for keyword in ['investment', 'account']):
            workflow.logger.info(f"\n>>> Forced handoff detected: Beneficiary agent cannot handle investment requests")
            should_force_handoff = True
            handoff = HandoffInformation(next_agent="Supervisor Agent", client_id=self.agent_deps.client_id)
        else:
            workflow.logger.info(f"No forced handoffs found. Running agent {self.current_agent.name}")
            result = await self.current_agent.run(user_input, deps=self.agent_deps,
                message_history=self.message_history)
            # Append new messages to history instead of replacing
            new_messages = result.new_messages()
            self.message_history.extend(new_messages)
            handoff = getattr(result.output, "handoff", None)

        return should_force_handoff, handoff, result

    async def _set_handoff_agent(self, handoff: HandoffInformation) -> str:
        trigger_message = ""
        workflow.logger.info("We have a handoff and a next agent set...")

        if handoff.next_agent == BENE_AGENT_NAME:
            workflow.logger.info("Next agent is set to Benefit agent")
            self.agent_deps = AgentDependencies(client_id=handoff.client_id)
            self.current_agent = temporal_bene_agent
            self.current_agent_name = BENE_AGENT_NAME
            trigger_message = "Process the user's beneficiary request from the conversation history. CRITICAL: You do NOT have access to investment data. If the user asks about investments, you MUST call handoff_to_supervisor() with NO response text."

        elif handoff.next_agent == INVEST_AGENT_NAME:
            workflow.logger.info("Next agent is set to invest agent")
            self.agent_deps = AgentDependencies(client_id=handoff.client_id)
            self.current_agent = temporal_invest_agent
            self.current_agent_name = INVEST_AGENT_NAME
            trigger_message = "Process the user's investment request from the conversation history. CRITICAL: You do NOT have access to beneficiary data. If the user asks about beneficiaries, you MUST call handoff_to_supervisor() with NO response text."

        elif handoff.next_agent == "Supervisor Agent":
            workflow.logger.info("Next agent is set to supervisor")
            # Handoff back to supervisor - keep client_id in deps
            self.agent_deps = AgentDependencies(client_id=handoff.client_id)
            self.current_agent = temporal_super_agent
            self.current_agent_name = SUPERVISOR_AGENT_NAME
            # Look at the most recent user message to understand what they want
            trigger_message = "The user has a new request. Check the most recent user message in the conversation history and route it to the appropriate agent."

        else:
            raise ValueError(f"unknown next agent type {handoff.next_agent}")      

        return trigger_message  

    async def _handle_handoffs(self, should_force_handoff: bool, handoff: HandoffInformation, result: WealthManagementWorkflow.result) -> str:
        workflow.logger.info(f"in _handle_handoffs {should_force_handoff} {handoff}")
        response = ""
        if handoff and handoff.next_agent:
            trigger_message = await self._set_handoff_agent(handoff)

            # Loop to handle chain handoffs
            while True:
                workflow.logger.info(f"Running current agent of {self.current_agent.name}")
                result = await self.current_agent.run(trigger_message, deps=self.agent_deps, message_history=self.message_history)

                # Append new messages from agent
                new_messages = result.new_messages()
                self.message_history.extend(new_messages)

                # Check if there's another handoff (chain routing)
                handoff = getattr(result.output, "handoff", None)
                if handoff and handoff.next_agent:
                    # There's a chain handoff! Continue routing without printing
                    workflow.logger.info(f"\n>>> Chain handoff detected: Continuing to {handoff.next_agent}")
                    trigger_message = await self._set_handoff_agent(handoff)
                    # Continue the loop to process the next agent
                else:
                    # No more handoffs, print the final response and break
                    workflow.logger.info("No more handouts. will print the final response")
                    if result.output.response:
                        # Validate and correct response based on agent type
                        validated_response = result.output.response
                        if self.current_agent_name == BENE_AGENT_NAME:
                            validated_response = validate_beneficiary_response(validated_response)
                        elif self.current_agent_name == INVEST_AGENT_NAME:
                            validated_response = validate_investment_response(validated_response)
                        print(validated_response)
                        response = validated_response
                    # current_agent, current_agent_name, and agent_deps are already set correctly
                    # These will be used for the next user input in the outer loop
                    break
        else:
            # Print response if not handing off
            if not should_force_handoff and result.output.response:
                workflow.logger.info("No forced handoff and we have a response.")
                # Validate and correct response based on agent type
                validated_response = result.output.response
                if self.current_agent_name == BENE_AGENT_NAME:
                    validated_response = validate_beneficiary_response(validated_response)
                elif self.current_agent_name == INVEST_AGENT_NAME:
                    validated_response = validate_investment_response(validated_response)
                print(validated_response)
                response = validated_response

        return response

    async def _process_user_message(self, chat_interaction: ChatInteraction, user_input: str):
        workflow.logger.info(f"Processing user message of {user_input}")

        # Add user input to history before running agent
        user_message = ModelRequest(parts=[UserPromptPart(content=user_input, timestamp=workflow.now())])
        self.message_history.append(user_message)

        # Pre-check: Force handoff for cross-domain requests
        should_force_handoff, handoff, result = await self._check_for_forced_handoff(user_input)

        # do set up any handoffs that are necessary

        response = await self._handle_handoffs(should_force_handoff, handoff, result)

        # update the chat interaction
        workflow.logger.info("Getting ready to set response to {response}")
        chat_interaction.text_response = response