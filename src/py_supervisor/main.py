import asyncio
import uuid
import logging
import datetime

from typing import Optional, TypedDict, List, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, AgentRunResult, RunContext, ModelMessage
from pydantic_ai.messages import ModelRequest, UserPromptPart
from dataclasses import dataclass

from common.agent_constants import SUPERVISOR_AGENT_NAME, SUPERVISOR_INSTRUCTIONS, BENE_AGENT_NAME, BENE_INSTRUCTIONS, INVEST_AGENT_NAME, INVEST_INSTRUCTIONS
from common.beneficiaries_manager import BeneficiariesManager
from common.investment_manager import InvestmentManager, InvestmentAccount

### Logging Configuration
# logging.basicConfig(level=logging.INFO,
#                     filename="py_supervisor.log",
#                     format="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)s | %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.info("Wealth Management Pydantic Chatbot Example Starting")

### Debug Configuration
DEBUG_MODE = False  # Set to True to see handoff routing debug messages

def debug_print(message: str):
    """Print debug messages only when DEBUG_MODE is enabled"""
    if DEBUG_MODE:
        print(message)

### Dependencies
@dataclass
class AgentDependencies:
    client_id: Optional[str] = None

### Handoff Details
class HandoffInformation(BaseModel):
    next_agent: "str"
    """ What agent to run next """
    client_id: str
    """ What client to use """

### Output classes
class WealthManagmentAgentOutput(BaseModel):
    response: Optional[str] = None
    """ Response to the client - only provide if NOT handing off """
    client_id: Optional[str] = None
    """ Set if we have been given one """
    handoff: Optional[HandoffInformation] = None
    """ Provides handoff details if another agent needs to process this request """

### Agents
AGENT_MODEL = 'openai:gpt-4.1'

supervisor_agent = Agent(
    AGENT_MODEL,
    name=SUPERVISOR_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=WealthManagmentAgentOutput,
    system_prompt=SUPERVISOR_INSTRUCTIONS,
)

beneficiary_agent = Agent(
    AGENT_MODEL,
    name=BENE_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=WealthManagmentAgentOutput,
    system_prompt=BENE_INSTRUCTIONS,
)

investment_agent = Agent(
    AGENT_MODEL,
    name=INVEST_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=WealthManagmentAgentOutput,
    system_prompt=INVEST_INSTRUCTIONS,
)

### Response Validation

def validate_beneficiary_response(response: str) -> str:
    """
    Cleans beneficiary agent responses and adds the exact follow-up question programmatically.
    Removes any LLM-generated follow-up text and replaces it with the exact required question.
    """
    if not response:
        return response

    # Check if this is a beneficiary list response (contains numbered items or bullet points)
    lines = response.split('\n')
    has_list = any(line.strip() and (line.strip()[0].isdigit() or line.strip().startswith('-')) for line in lines)

    if has_list:
        # This is a beneficiary list - extract only the list portion
        list_lines = []
        for line in lines:
            # Stop at any line that looks like a question or instruction
            lower_line = line.lower()
            if any(phrase in lower_line for phrase in ['would you', 'if you', 'let me know', 'please', 'feel free', 'need to', 'want to']):
                break
            list_lines.append(line)

        # Reconstruct with exact question appended by application
        list_portion = '\n'.join(list_lines).strip()
        exact_question = "Would you like to add a beneficiary, remove a beneficiary, or list your beneficiaries again?"

        return f"{list_portion}\n\n{exact_question}"

    return response

def validate_investment_response(response: str) -> str:
    """
    Cleans investment agent responses and adds the exact follow-up question programmatically.
    Removes any LLM-generated follow-up text and replaces it with the exact required question.
    """
    if not response:
        return response

    # Check if this is an investment list response (contains numbered items or bullet points)
    lines = response.split('\n')
    has_list = any(line.strip() and (line.strip()[0].isdigit() or line.strip().startswith('-')) for line in lines)

    if has_list:
        # This is an investment list - extract only the list portion
        list_lines = []
        for line in lines:
            # Stop at any line that looks like a question or instruction
            lower_line = line.lower()
            if any(phrase in lower_line for phrase in ['would you', 'if you', 'let me know', 'please', 'feel free', 'need to', 'want to', 'need details', 'make changes']):
                break
            list_lines.append(line)

        # Reconstruct with exact question appended by application
        list_portion = '\n'.join(list_lines).strip()
        exact_question = "Would you like to open an investment account, close an investment account, or list your investments again?"

        return f"{list_portion}\n\n{exact_question}"

    return response

### Managers

beneficiaries_mgr = BeneficiariesManager()
investment_mgr = InvestmentManager()

### Tools

@supervisor_agent.tool
async def get_client_id(context: RunContext[AgentDependencies]) -> str:
    """
    Check if a client_id is already stored.

    Returns:
        The stored client_id if available, or a message indicating it's not set.
    """
    debug_print(f"Retrieveing client id {context.deps.client_id}")

    if context.deps.client_id:
        return f"Client ID is already set to: {context.deps.client_id}"
    else:
        return "No client_id is currently stored."

@supervisor_agent.tool
async def set_client_id(context: RunContext[AgentDependencies], client_id: str) -> str:
    """
    Store the client ID for future operations. Only call this when the user provides an actual identifier.

    Args:
        client_id: The client ID provided by the user (e.g., "12345", "c-01922", "client_abc")
    """

    if not client_id or client_id.strip() == "":
        return "ERROR: Cannot set empty client_id. Ask the user for their client_id."

    context.deps.client_id = client_id

    debug_print(f"****>>> Deps.client id is now set to {context.deps.client_id}")
    return f"Client ID set to: {client_id}"

@supervisor_agent.tool
async def handoff_to_beneficiary_agent(context: RunContext[AgentDependencies], client_id: str) -> HandoffInformation:
    """
    Hand off to the beneficiary agent to handle beneficiary-related requests.
    Requires that the client_id is passed in as a parameter
    """

    if not client_id or client_id.strip() == "":
        raise ValueError("client_id is required before handoff!")

    return HandoffInformation(next_agent=BENE_AGENT_NAME, client_id=client_id)

@supervisor_agent.tool
async def handoff_to_investment_agent(context: RunContext[AgentDependencies], client_id: str) -> HandoffInformation:
    """
    Hand off to the investment agent to handle investment-related requests.
    Requires that the client_id is passed in as a parameter
    """
    
    if not client_id or client_id.strip() == "":
        raise ValueError("client_id is required before handoff!")

    return HandoffInformation(next_agent=INVEST_AGENT_NAME, client_id=client_id)

@beneficiary_agent.tool
async def add_beneficiaries(
        context: RunContext[AgentDependencies],
        first_name: str, last_name: str, relationship: str
) -> None:
    beneficiaries_mgr.add_beneficiary(context.deps.client_id, first_name, last_name, relationship)

@beneficiary_agent.tool
async def list_beneficiaries(
        context: RunContext[AgentDependencies], 
        client_id: str
) -> list:
    """
    List the beneficiaries for the given client id.
    """
    return beneficiaries_mgr.list_beneficiaries(context.deps.client_id)

@beneficiary_agent.tool
async def delete_beneficiaries(
        context: RunContext[AgentDependencies], beneficiary_id: str):
        logger.info(f"Tool: Deleting beneficiary {beneficiary_id} from account {context.deps.client_id}")
        beneficiaries_mgr.delete_beneficiary(context.deps.client_id, beneficiary_id)

@beneficiary_agent.tool
async def handoff_to_supervisor(context: RunContext[AgentDependencies], client_id: str) -> HandoffInformation:
    """
    Hand off back to the supervisor agent when the request is not beneficiary-related.
    Use this when the user asks about investments, general questions, or other non-beneficiary topics.
    """
    if not client_id or client_id.strip() == "":
        raise ValueError("client_id is required before handoff!")

    return HandoffInformation(next_agent=SUPERVISOR_AGENT_NAME, client_id=client_id)

@investment_agent.tool
async def list_investments(context: RunContext[AgentDependencies]) -> list:
    """
    List the investments for a given client id.
    """    
    return investment_mgr.list_investment_accounts(context.deps.client_id)


@investment_agent.tool
async def open_investment(context: RunContext[AgentDependencies],
    name: str, balance: float):
    """
    Adds a new investment account for the given information
    """
    investment_account = InvestmentAccount(
        client_id=context.deps.client_id,
        name=name, 
        balance=balance)

    return investment_mgr.add_investment_account(investment_account)

@investment_agent.tool
async def close_investment(context: RunContext[AgentDependencies],
    investment_id: str):
    """
    Deletes a given investment account
    """
    return investment_mgr.delete_investment_account(
        client_id=context.deps.client_id,
        investment_id=investment_id)

@investment_agent.tool
async def handoff_to_supervisor(context: RunContext[AgentDependencies], client_id: str) -> HandoffInformation:
    """
    Hand off back to the supervisor agent when the request is not investment-related.
    Use this when the user asks about beneficiaries, general questions, or other non-investment topics.
    """
    if not client_id or client_id.strip() == "":
        raise ValueError("client_id is required before handoff!")

    return HandoffInformation(next_agent="Supervisor Agent", client_id=client_id)


class PydanticAIWealthManagement:
    def __init__(self):
        self.agent_deps = AgentDependencies()
        self.message_history : List[ModelMessage] = []
        self.current_agent = supervisor_agent
        self.current_agent_name = SUPERVISOR_AGENT_NAME
        self.pending_input: str | None = None # What to feed into the agent this iteration

    async def run_agent_loop(self):
        while True:
            # Get user input with current agent displayed on same line
            if self.pending_input is None:
                user_input = input(f"\n[{self.current_agent_name}] Enter your message: ")
            else:
                user_input = pending_input
                self.pending_input = None

            lower_input = user_input.lower() if user_input is not None else ""
            if lower_input in {"exit","end","quit"}:
                break

            await self._process_user_message(user_input)

    async def _process_user_message(self, user_input: str):
        debug_print(f"Processing user message of {user_input}")
        # Add user input to history before running agent

        user_message = ModelRequest(parts=[UserPromptPart(content=user_input, timestamp=datetime.datetime.now(datetime.timezone.utc))])
        self.message_history.append(user_message)

        should_force_handoff, handoff, result = await self._check_for_forced_handoff(user_input)

        await self._handle_handoffs(should_force_handoff, handoff, result)

    async def _check_for_forced_handoff(self, user_input: str):
        should_force_handoff = False
        result = None
        if self.current_agent_name == INVEST_AGENT_NAME and any(keyword in user_input.lower() for keyword in ['beneficiary', 'beneficiaries']):
            debug_print(f"\n>>> Forced handoff detected: Investment agent cannot handle beneficiary requests")
            should_force_handoff = True
            handoff = HandoffInformation(next_agent="Supervisor Agent", client_id=self.agent_deps.client_id)
        elif self.current_agent_name == BENE_AGENT_NAME and any(keyword in user_input.lower() for keyword in ['investment', 'account']):
            debug_print(f"\n>>> Forced handoff detected: Beneficiary agent cannot handle investment requests")
            should_force_handoff = True
            handoff = HandoffInformation(next_agent="Supervisor Agent", client_id=self.agent_deps.client_id)
        else:
            result = await self.current_agent.run(user_input, deps=self.agent_deps,
                message_history=self.message_history)
            # Append new messages to history instead of replacing
            new_messages = result.new_messages()
            self.message_history.extend(new_messages)
            handoff = getattr(result.output, "handoff", None)

        debug_print(f"returning {should_force_handoff}, {handoff}, {result}")

        return should_force_handoff, handoff, result

    def _set_handoff_agent(self, handoff: HandoffInformation) -> str:
        trigger_message = ""
        debug_print("We have a handoff and a next agent set...")

        if handoff.next_agent == BENE_AGENT_NAME:
            self.agent_deps = AgentDependencies(client_id=handoff.client_id)
            self.current_agent = beneficiary_agent
            self.current_agent_name = BENE_AGENT_NAME
            trigger_message = "Process the user's beneficiary request from the conversation history. CRITICAL: You do NOT have access to investment data. If the user asks about investments, you MUST call handoff_to_supervisor() with NO response text."

        elif handoff.next_agent == INVEST_AGENT_NAME:
            self.agent_deps = AgentDependencies(client_id=handoff.client_id)
            self.current_agent = investment_agent
            self.current_agent_name = INVEST_AGENT_NAME
            trigger_message = "Process the user's investment request from the conversation history. CRITICAL: You do NOT have access to beneficiary data. If the user asks about beneficiaries, you MUST call handoff_to_supervisor() with NO response text."

        elif handoff.next_agent == "Supervisor Agent":
            # Handoff back to supervisor - keep client_id in deps
            self.agent_deps = AgentDependencies(client_id=handoff.client_id)
            self.current_agent = supervisor_agent
            self.current_agent_name = SUPERVISOR_AGENT_NAME
            # Look at the most recent user message to understand what they want
            trigger_message = "The user has a new request. Check the most recent user message in the conversation history and route it to the appropriate agent."

        else:
            raise ValueError(f"unknown next agent type {handoff.next_agent}")

        return trigger_message


    async def _handle_handoffs(self, should_force_handoff: bool, handoff: HandoffInformation, result: AgentRunResult):
        if handoff and handoff.next_agent:
            if not should_force_handoff:
                debug_print(f"\n>>> Handoff detected: Switching from {self.current_agent_name} to {handoff.next_agent}")

            trigger_message = self._set_handoff_agent(handoff)

            # Loop to handle chain handoffs
            while True:
                result = await self.current_agent.run(trigger_message, deps=self.agent_deps, message_history=self.message_history)

                # Append new messages from agent
                new_messages = result.new_messages()
                self.message_history.extend(new_messages)

                # Check if there's another handoff (chain routing)
                handoff = getattr(result.output, "handoff", None)
                if handoff and handoff.next_agent:
                    # There's a chain handoff! Continue routing without printing
                    debug_print(f"\n>>> Chain handoff detected: Continuing to {handoff.next_agent}")
                    trigger_message = self._set_handoff_agent(handoff)
                    # Continue the loop to process the next agent
                else:
                    # No more handoffs, print the final response and break
                    if result.output.response:
                        # Validate and correct response based on agent type
                        validated_response = result.output.response
                        if self.current_agent_name == BENE_AGENT_NAME:
                            validated_response = validate_beneficiary_response(validated_response)
                        elif self.current_agent_name == INVEST_AGENT_NAME:
                            validated_response = validate_investment_response(validated_response)
                        print(validated_response)
                    # current_agent, current_agent_name, and agent_deps are already set correctly
                    # These will be used for the next user input in the outer loop
                    break
        else:
            # Print response if not handing off
            if not should_force_handoff and result.output.response:
                # Validate and correct response based on agent type
                validated_response = result.output.response
                if self.current_agent_name == BENE_AGENT_NAME:
                    validated_response = validate_beneficiary_response(validated_response)
                elif self.current_agent_name == INVEST_AGENT_NAME:
                    validated_response = validate_investment_response(validated_response)
                print(validated_response)

async def main():
    print("Welcome to ABC Wealth Management. How can I help you?")
    wealth_management_flow = PydanticAIWealthManagement()
    await wealth_management_flow.run_agent_loop()
    print("Agent loop complete.")
        
if __name__ == "__main__":
     asyncio.run(main())
