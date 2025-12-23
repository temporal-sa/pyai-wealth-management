import asyncio
import uuid
import logging

from typing import Optional, TypedDict

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, ModelMessage
from dataclasses import dataclass

from common.agent_constants import RECOMMENDED_PROMPT_PREFIX
from common.beneficiaries_manager import BeneficiariesManager
from common.investment_manager import InvestmentManager

### Logging Configuration
# logging.basicConfig(level=logging.INFO,
#                     filename="py_supervisor.log",
#                     format="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)s | %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.info("Wealth Management Pydantic Chatbot Example Starting")

### Chat History
class ChatMessage(TypedDict):
    role: Literal['user', 'model']
    content: str

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
    response: str
    """ Response to the client """
    client_id: Optional[str] = None
    """ Set if we have been given one """
    handoff: Optional[HandoffInformation] = None
    """ Provides handoff details if another agent needs to process this request """

class BeneficiaryAgentOutput(BaseModel):
    response: str
    """ Response to the client """
    client_id: Optional[str] = None
    """ Set if we have been given one """
    need_more_info: bool = False
    """ Set if more information is needed from the client """


### Agents
AGENT_MODEL = 'openai:gpt-4.1'

supervisor_agent = Agent(
    AGENT_MODEL,
    name="Supervisor Agent",
    deps_type=AgentDependencies,
    output_type=WealthManagmentAgentOutput,
    system_prompt=f""""
    # Supervisor Agent (Router)

    You are the Supervisor agent routing requests to specialized agents.

    ## Your Responsibility
    Ensure you have a client_id, then route beneficiary requests to the beneficiary agent.

    ## Step-by-Step Logic (Follow Exactly)

    **For EVERY user message, follow these steps in order:**

    1. **Check conversation history**: Is there a beneficiary-related request in the recent conversation?
       - Beneficiary requests include: list/add/delete/update beneficiaries

    2. **Check if you have a client_id stored** (context.deps.client_id):
       - If YES and there's a beneficiary request (current or previous) → IMMEDIATELY call `handoff_to_beneficiary_agent()`
       - If YES but no beneficiary request → respond conversationally
       - If NO → continue to step 3

    3. **If no client_id is stored**:
       - Does the current message contain an identifier (like "12345", "c-01922", "client_abc")?
         - YES → Call `set_client_id(client_id="<id>")` then check if there's a pending beneficiary request
         - NO → If current message is beneficiary-related, ask: "What is your client_id?"

    ## Critical Rules
    - After calling set_client_id successfully, check the conversation history for beneficiary requests
    - If you find a beneficiary request AND now have a client_id, call handoff_to_beneficiary_agent() immediately
    - NEVER call set_client_id without an actual identifier from the user
    - Do not mention agents, tools, or handoffs to the user

    ## Examples

    Example 1 - User asks beneficiary question first:
    User: "List my beneficiaries"
    You: No client_id stored, beneficiary request detected
    Response: "What is your client_id?"

    Example 2 - User provides client_id after beneficiary request:
    User: "c-01922"
    You: Contains identifier, call set_client_id("c-01922")
    You: Check history - found "List my beneficiaries" request
    You: Now have client_id AND beneficiary request → call handoff_to_beneficiary_agent()
    Response: (after handoff) "Let me help you with your beneficiaries."

    Example 3 - User has client_id and makes beneficiary request:
    User: "Show my beneficiaries"
    You: Have client_id, beneficiary request detected
    You: Call handoff_to_beneficiary_agent()
    Response: "Let me pull up your beneficiaries."
    """
)

BENEFICIARY_AGENT_NAME = "Beneficiaries Agent"

beneficiary_agent = Agent(
    AGENT_MODEL,
    name=BENEFICIARY_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=WealthManagmentAgentOutput,
    system_prompt=f"""{RECOMMENDED_PROMPT_PREFIX}
    You are a beneficiary agent handling all beneficiary-related operations.

    ## CRITICAL: When You Receive Control
    You have been handed off from the supervisor agent. The current user message may be empty.

    **YOU MUST look back in the conversation history to find the user's original beneficiary request.**

    Common requests in history include:
    - "Who are my beneficiaries?"
    - "List my beneficiaries"
    - "Show my beneficiaries"
    - "Add a beneficiary"
    - "Delete a beneficiary"

    **IMMEDIATELY process that historical request as if the user just asked it.**

    ## Your Actions Based on Historical Request

    1. **If history shows list/view/show beneficiaries request**:
       - IMMEDIATELY call the `list_beneficiaries` tool (no need to ask the user again)
       - Display the results
       - Then ask: "Would you like to add or delete a beneficiary?"

    2. **If history shows add beneficiary request**:
       - Ask for: first name, last name, relationship
       - Use `add_beneficiaries` tool

    3. **If history shows delete beneficiary request**:
       - First call `list_beneficiaries` to show options
       - Ask which one to delete
       - Use `delete_beneficiaries` tool with the beneficiary_id
       - Confirm before deleting

    4. **If no clear request in history**:
       - Say: "How can I help with your beneficiaries today?"

    ## Important Notes
    - Hide beneficiary IDs from users (they're internal)
    - Remember the mapping between names and IDs for deletions
    """
)

### Managers 

beneficiaries_mgr = BeneficiariesManager()

### Tools

@supervisor_agent.tool
async def get_client_id(context: RunContext[AgentDependencies]) -> str:
    """
    Check if a client_id is already stored.

    Returns:
        The stored client_id if available, or a message indicating it's not set.
    """
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
    return f"Client ID set to: {client_id}"

@supervisor_agent.tool
async def handoff_to_beneficiary_agent(context: RunContext[AgentDependencies]) -> HandoffInformation:
    """
    Hand off to the beneficiary agent to handle beneficiary-related requests.
    Uses the client_id from the context dependencies.
    """
    client_id = context.deps.client_id

    if not client_id or client_id.strip() == "":
        raise ValueError("client_id is required before handoff!")

    return HandoffInformation(next_agent=BENEFICIARY_AGENT_NAME, client_id=client_id)

@beneficiary_agent.tool
async def add_beneficiaries(
        context: RunContext[AgentDependencies],
        first_name: str, last_name: str, relationship: str
) -> None:
    beneficiaries_mgr.add_beneficiary(context.deps.client_id, first_name, last_name, relationship)

@beneficiary_agent.tool
async def list_beneficiaries(
        context: RunContext[AgentDependencies]
) -> list:
    """
    List the beneficiaries for the given client id.

    Args:
        client_id: The customer's client id
    """
    return beneficiaries_mgr.list_beneficiaries(context.deps.client_id)

@beneficiary_agent.tool
async def delete_beneficiaries(
        context: RunContext[AgentDependencies], beneficiary_id: str):
        logger.info(f"Tool: Deleting beneficiary {beneficiary_id} from account {context.deps.client_id}")
        beneficiaries_mgr.delete_beneficiary(context.deps.client_id, beneficiary_id)

async def main():
    print("Welcome to ABC Wealth Management. How can I help you?")
    agent_deps = AgentDependencies()
    chat_messages : List[ChatMessage] = []
    message_history : List[ModelMessage] = []
    current_agent = supervisor_agent
    current_agent_name = "Supervisor Agent"
    pending_input: str | None = None # What to feed into the agent this iteration

    while True:
        # Display current agent
        print(f"\n[Current Agent: {current_agent_name}]")

        if pending_input is None:
            user_input = input("Enter your message: ")
        else:
            user_input = pending_input
            pending_input = None

        lower_input = user_input.lower() if user_input is not None else ""
        if lower_input in {"exit","end","quit"}:
            break

        # Add user input to history before running agent
        from pydantic_ai.messages import ModelRequest, UserPromptPart
        import datetime
        user_message = ModelRequest(parts=[UserPromptPart(content=user_input, timestamp=datetime.datetime.now(datetime.timezone.utc))])
        message_history.append(user_message)

        result = await current_agent.run(user_input, deps=agent_deps,
            message_history=message_history)

        # Append new messages to history instead of replacing
        new_messages = result.new_messages()
        message_history.extend(new_messages)
        handoff = getattr(result.output, "handoff", None)

        if handoff and handoff.next_agent:
            print(f"\n>>> Handoff detected: Switching from {current_agent_name} to {handoff.next_agent}")
            if handoff.next_agent == BENEFICIARY_AGENT_NAME:
                agent_deps = AgentDependencies(client_id=handoff.client_id)
                current_agent = beneficiary_agent
                current_agent_name = BENEFICIARY_AGENT_NAME

                # Immediately run the beneficiary agent with a trigger message to process the history
                print(f"\n[Current Agent: {current_agent_name}]")
                trigger_message = "Process the user's beneficiary request from the conversation history."
                result = await current_agent.run(trigger_message, deps=agent_deps, message_history=message_history)

                # Append new messages from beneficiary agent
                new_messages = result.new_messages()
                message_history.extend(new_messages)

                print(result.output.response)
            else:
                raise ValueError(f"unknown next agent type {handoff.next_agent}")
            # Don't print the supervisor's handoff response
        else:
            # Print response if not handing off
            print(result.output.response)

        

        
        
if __name__ == "__main__":
     asyncio.run(main())
