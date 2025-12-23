import asyncio
import uuid
import logging

from typing import Optional, TypedDict, List, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, ModelMessage
from dataclasses import dataclass

from common.agent_constants import RECOMMENDED_PROMPT_PREFIX
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
    name="Supervisor Agent",
    deps_type=AgentDependencies,
    output_type=WealthManagmentAgentOutput,
    system_prompt=f""""
    # Supervisor Agent (Router)

    You are the Supervisor agent routing requests to specialized agents.

    ## Your Responsibility
    Ensure you have a client_id, then route requests to the appropriate specialized agent.

    ## Step-by-Step Logic (Follow Exactly)

    **For EVERY user message, follow these steps in order:**

    1. **Check the MOST RECENT user message in conversation history**: What is the user asking for?
       - Beneficiary requests: list/add/remove beneficiaries, "who are my beneficiaries"
       - Investment requests: list/open/close investment accounts, show investments, "what investment accounts"

    2. **Check if you have a client_id stored** (context.deps.client_id):
       - If YES and the most recent user request is beneficiary-related → IMMEDIATELY call `handoff_to_beneficiary_agent()` WITHOUT ANY TEXT RESPONSE
       - If YES and the most recent user request is investment-related → IMMEDIATELY call `handoff_to_investment_agent()` WITHOUT ANY TEXT RESPONSE
       - If YES but no specialized request → respond conversationally
       - If NO → continue to step 3

    3. **If no client_id is stored**:
       - Does the current message contain an identifier (like "12345", "c-01922", "client_abc")?
         - YES → Call `set_client_id(client_id="<id>")` then check if there's a pending specialized request
         - NO → If current message is beneficiary or investment related, ask: "What is your client_id?"

    ## Critical Rules
    - ALWAYS look at the MOST RECENT user message (not agent messages) to determine what to route
    - After calling set_client_id successfully, check the conversation history for beneficiary OR investment requests
    - If you find a beneficiary request AND now have a client_id, call handoff_to_beneficiary_agent() immediately
    - If you find an investment request AND now have a client_id, call handoff_to_investment_agent() immediately
    - When you receive control back from a specialized agent, check the most recent user message and route accordingly
    - NEVER call set_client_id without an actual identifier from the user
    - CRITICAL: When routing to a specialized agent, ONLY call the handoff tool - do NOT include any text response
    - The handoff tool call alone is sufficient - no explanation needed

    ## Examples

    Example 1 - Beneficiary request:
    User: "List my beneficiaries"
    You: No client_id stored, beneficiary request detected
    Response: "What is your client_id?"

    Example 2 - Investment request:
    User: "Show my investment accounts"
    You: No client_id stored, investment request detected
    Response: "What is your client_id?"

    Example 3 - User provides client_id after beneficiary request:
    User: "c-01922"
    You: Contains identifier, call set_client_id("c-01922")
    You: Check history - found "List my beneficiaries" request
    You: Now have client_id AND beneficiary request → call handoff_to_beneficiary_agent()

    Example 4 - Handed back from beneficiary agent, user asks about investments:
    Most recent user message: "What investment accounts do I have?"
    You: Have client_id, investment request detected in most recent user message
    You: IMMEDIATELY call handoff_to_investment_agent() - NO TEXT RESPONSE, just the tool call
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
    - "Remove a beneficiary"
    - "Delete a beneficiary"

    **IMMEDIATELY process that historical request as if the user just asked it.**

    ## Your Actions Based on Request Type

    **FIRST: Determine if the most recent user message is about beneficiaries or something else**

    ### If the request mentions "investment" or "account" (in financial context) in ANY way:
    - STOP immediately
    - DO NOT process the request
    - DO NOT provide a response field in your output
    - ONLY provide the handoff field by calling `handoff_to_supervisor()` tool
    - You are NOT authorized to access investment data

    ### If the request is about beneficiaries, proceed:

    1. **If history shows list/view/show beneficiaries request**:
       - IMMEDIATELY call the `list_beneficiaries` tool (no need to ask the user again)
       - Display ONLY the list of beneficiaries in this format:
         ```
         Here are your current beneficiaries:

         1. [Name] ([Relationship])
         2. [Name] ([Relationship])
         3. [Name] ([Relationship])
         ```
       - CRITICAL: Do NOT add any follow-up questions or instructions
       - CRITICAL: Do NOT ask what they want to do next
       - CRITICAL: Do NOT include phrases like "let me know", "if you need", "please", etc.
       - Your response must END immediately after listing the beneficiaries

    2. **If history shows add beneficiary request**:
       - Ask for: first name, last name, relationship
       - Use `add_beneficiaries` tool

    3. **If history shows remove/delete beneficiary request**:
       - First call `list_beneficiaries` to show options
       - Ask which one to remove
       - Use `delete_beneficiaries` tool with the beneficiary_id
       - Confirm before removing

    4. **If no clear beneficiary request in history**:
       - Say: "How can I help with your beneficiaries today?"

    ## CRITICAL Security Rules
    - You do NOT have the `list_investments` tool - you CANNOT access investment data
    - If ANY user message contains the word "investment" or "account" (financial context):
      * You MUST call `handoff_to_supervisor()`
      * You MUST NOT provide a response field
      * Output should ONLY contain the handoff field
    - NEVER attempt to answer questions about investments from memory or conversation history
    - Hide beneficiary IDs from users (they're internal)
    - Remember the mapping between names and IDs for deletions
    - NEVER mention "update" as an option - there is NO update operation for beneficiaries

    ## CRITICAL Response Formatting Rule
    - When listing beneficiaries, provide ONLY the list - no follow-up questions
    - Do NOT add phrases like "let me know", "if you need", "please", "would you like"
    - The application will handle follow-up prompts automatically
    - End your response immediately after the list
    """
)

INVESTMENT_AGENT_NAME = "Investment Agent"

investment_agent = Agent(
    AGENT_MODEL,
    name=INVESTMENT_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=WealthManagmentAgentOutput,
    system_prompt=f"""{RECOMMENDED_PROMPT_PREFIX}
    You are an investment agent handling all investment-related operations.

    ## CRITICAL: When You Receive Control
    You have been handed off from the supervisor agent. The current user message may be empty.

    **YOU MUST look back in the conversation history to find the user's original investment request.**

    Common requests in history include:
    - "What investment accounts do I have?"
    - "List my investments"
    - "Show my investments"
    - "Open an investment account"
    - "Close an investment account"

    **IMMEDIATELY process that historical request as if the user just asked it.**

    ## Your Actions Based on Request Type

    **FIRST: Determine if the most recent user message is about investments or something else**

    ### If the request mentions "beneficiary" or "beneficiaries" in ANY way:
    - STOP immediately
    - DO NOT process the request
    - DO NOT provide a response field in your output
    - ONLY provide the handoff field by calling `handoff_to_supervisor()` tool
    - You are NOT authorized to access beneficiary data

    ### If the request is about investments, proceed:

    1. **If history shows list/view/show investments request**:
       - IMMEDIATELY call the `list_investments` tool (no need to ask the user again)
       - Display ONLY the list of investments in numbered format
       - CRITICAL: Do NOT add any follow-up questions or instructions
       - CRITICAL: Do NOT ask what they want to do next
       - CRITICAL: Do NOT include phrases like "let me know", "if you need", "please", etc.
       - Your response must END immediately after listing the investments

    2. **If history shows open investment account request**:
       - Ask for: name and a balance
       - Use `open_investment` tool

    3. **If history shows close investment account request**:
       - First call `list_investments` to show options
       - Ask which one to close
       - Use `close_investment` tool with the investment_id
       - Confirm before closing

    4. **If no clear investment request in history**:
       - Say: "How can I help with your investments today?"

    ## CRITICAL Security Rules
    - You do NOT have the `list_beneficiaries` tool - you CANNOT access beneficiary data
    - If ANY user message contains the word "beneficiary" or "beneficiaries":
      * You MUST call `handoff_to_supervisor()`
      * You MUST NOT provide a response field
      * Output should ONLY contain the handoff field
    - NEVER attempt to answer questions about beneficiaries from memory or conversation history
    - Hide investment IDs from users (they're internal)
    - Remember the mapping between names and IDs for closures
    """
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

@supervisor_agent.tool
async def handoff_to_investment_agent(context: RunContext[AgentDependencies]) -> HandoffInformation:
    """
    Hand off to the investment agent to handle investment-related requests.
    Uses the client_id from the context dependencies.
    """
    client_id = context.deps.client_id

    if not client_id or client_id.strip() == "":
        raise ValueError("client_id is required before handoff!")

    return HandoffInformation(next_agent=INVESTMENT_AGENT_NAME, client_id=client_id)

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
    """
    return beneficiaries_mgr.list_beneficiaries(context.deps.client_id)

@beneficiary_agent.tool
async def delete_beneficiaries(
        context: RunContext[AgentDependencies], beneficiary_id: str):
        logger.info(f"Tool: Deleting beneficiary {beneficiary_id} from account {context.deps.client_id}")
        beneficiaries_mgr.delete_beneficiary(context.deps.client_id, beneficiary_id)

@beneficiary_agent.tool
async def handoff_to_supervisor(context: RunContext[AgentDependencies]) -> HandoffInformation:
    """
    Hand off back to the supervisor agent when the request is not beneficiary-related.
    Use this when the user asks about investments, general questions, or other non-beneficiary topics.
    """
    client_id = context.deps.client_id

    if not client_id or client_id.strip() == "":
        raise ValueError("client_id is required before handoff!")

    return HandoffInformation(next_agent="Supervisor Agent", client_id=client_id)

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
async def handoff_to_supervisor(context: RunContext[AgentDependencies]) -> HandoffInformation:
    """
    Hand off back to the supervisor agent when the request is not investment-related.
    Use this when the user asks about beneficiaries, general questions, or other non-investment topics.
    """
    client_id = context.deps.client_id

    if not client_id or client_id.strip() == "":
        raise ValueError("client_id is required before handoff!")

    return HandoffInformation(next_agent="Supervisor Agent", client_id=client_id)

async def main():
    print("Welcome to ABC Wealth Management. How can I help you?")
    agent_deps = AgentDependencies()
    message_history : List[ModelMessage] = []
    current_agent = supervisor_agent
    current_agent_name = "Supervisor Agent"
    pending_input: str | None = None # What to feed into the agent this iteration

    while True:
        # Get user input with current agent displayed on same line
        if pending_input is None:
            user_input = input(f"\n[{current_agent_name}] Enter your message: ")
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

        # Pre-check: Force handoff for cross-domain requests
        should_force_handoff = False
        if current_agent_name == INVESTMENT_AGENT_NAME and any(keyword in user_input.lower() for keyword in ['beneficiary', 'beneficiaries']):
            debug_print(f"\n>>> Forced handoff detected: Investment agent cannot handle beneficiary requests")
            should_force_handoff = True
            handoff = HandoffInformation(next_agent="Supervisor Agent", client_id=agent_deps.client_id)
        elif current_agent_name == BENEFICIARY_AGENT_NAME and any(keyword in user_input.lower() for keyword in ['investment', 'account']):
            debug_print(f"\n>>> Forced handoff detected: Beneficiary agent cannot handle investment requests")
            should_force_handoff = True
            handoff = HandoffInformation(next_agent="Supervisor Agent", client_id=agent_deps.client_id)
        else:
            result = await current_agent.run(user_input, deps=agent_deps,
                message_history=message_history)
            # Append new messages to history instead of replacing
            new_messages = result.new_messages()
            message_history.extend(new_messages)
            handoff = getattr(result.output, "handoff", None)

        if handoff and handoff.next_agent:
            if not should_force_handoff:
                debug_print(f"\n>>> Handoff detected: Switching from {current_agent_name} to {handoff.next_agent}")

            if handoff.next_agent == BENEFICIARY_AGENT_NAME:
                agent_deps = AgentDependencies(client_id=handoff.client_id)
                current_agent = beneficiary_agent
                current_agent_name = BENEFICIARY_AGENT_NAME
                trigger_message = "Process the user's beneficiary request from the conversation history. CRITICAL: You do NOT have access to investment data. If the user asks about investments, you MUST call handoff_to_supervisor() with NO response text."

            elif handoff.next_agent == INVESTMENT_AGENT_NAME:
                agent_deps = AgentDependencies(client_id=handoff.client_id)
                current_agent = investment_agent
                current_agent_name = INVESTMENT_AGENT_NAME
                trigger_message = "Process the user's investment request from the conversation history. CRITICAL: You do NOT have access to beneficiary data. If the user asks about beneficiaries, you MUST call handoff_to_supervisor() with NO response text."

            elif handoff.next_agent == "Supervisor Agent":
                # Handoff back to supervisor - keep client_id in deps
                agent_deps = AgentDependencies(client_id=handoff.client_id)
                current_agent = supervisor_agent
                current_agent_name = "Supervisor Agent"
                # Look at the most recent user message to understand what they want
                trigger_message = "The user has a new request. Check the most recent user message in the conversation history and route it to the appropriate agent."

            else:
                raise ValueError(f"unknown next agent type {handoff.next_agent}")

            # Loop to handle chain handoffs
            while True:
                result = await current_agent.run(trigger_message, deps=agent_deps, message_history=message_history)

                # Append new messages from agent
                new_messages = result.new_messages()
                message_history.extend(new_messages)

                # Check if there's another handoff (chain routing)
                handoff = getattr(result.output, "handoff", None)
                if handoff and handoff.next_agent:
                    # There's a chain handoff! Continue routing without printing
                    debug_print(f"\n>>> Chain handoff detected: Continuing to {handoff.next_agent}")

                    # Set up the next agent in the chain
                    if handoff.next_agent == BENEFICIARY_AGENT_NAME:
                        agent_deps = AgentDependencies(client_id=handoff.client_id)
                        current_agent = beneficiary_agent
                        current_agent_name = BENEFICIARY_AGENT_NAME
                        trigger_message = "Process the user's beneficiary request from the conversation history. CRITICAL: You do NOT have access to investment data. If the user asks about investments, you MUST call handoff_to_supervisor() with NO response text."
                    elif handoff.next_agent == INVESTMENT_AGENT_NAME:
                        agent_deps = AgentDependencies(client_id=handoff.client_id)
                        current_agent = investment_agent
                        current_agent_name = INVESTMENT_AGENT_NAME
                        trigger_message = "Process the user's investment request from the conversation history. CRITICAL: You do NOT have access to beneficiary data. If the user asks about beneficiaries, you MUST call handoff_to_supervisor() with NO response text."
                    elif handoff.next_agent == "Supervisor Agent":
                        agent_deps = AgentDependencies(client_id=handoff.client_id)
                        current_agent = supervisor_agent
                        current_agent_name = "Supervisor Agent"
                        trigger_message = "The user has a new request. Check the most recent user message in the conversation history and route it to the appropriate agent."
                    else:
                        raise ValueError(f"unknown next agent type {handoff.next_agent}")

                    # Continue the loop to process the next agent
                else:
                    # No more handoffs, print the final response and break
                    if result.output.response:
                        # Validate and correct response based on agent type
                        validated_response = result.output.response
                        if current_agent_name == BENEFICIARY_AGENT_NAME:
                            validated_response = validate_beneficiary_response(validated_response)
                        elif current_agent_name == INVESTMENT_AGENT_NAME:
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
                if current_agent_name == BENEFICIARY_AGENT_NAME:
                    validated_response = validate_beneficiary_response(validated_response)
                elif current_agent_name == INVESTMENT_AGENT_NAME:
                    validated_response = validate_investment_response(validated_response)
                print(validated_response)

        

        
        
if __name__ == "__main__":
     asyncio.run(main())
