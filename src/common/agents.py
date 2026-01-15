import logging
from typing import Optional
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext, ModelRetry

from common.agent_constants import (
    SUPERVISOR_AGENT_NAME, SUPERVISOR_INSTRUCTIONS,
    BENE_AGENT_NAME, BENE_INSTRUCTIONS,
    INVEST_AGENT_NAME, INVEST_INSTRUCTIONS
)
from common.beneficiaries_manager import BeneficiariesManager
from common.investment_manager import InvestmentManager, InvestmentAccount

logger = logging.getLogger(__name__)

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
    next_agent: Optional[str] = None  # Signals routing to another agent
    trigger_message: Optional[str] = None  # Message for next agent
    current_agent_name: str = SUPERVISOR_AGENT_NAME  # For debugging/logging

### Output Functions

# Supervisor Agent Output Functions
async def respond_to_user(ctx: RunContext[AgentDependencies], response: str) -> str:
    """
    Respond directly to the user when no specialized agent is needed.
    Use this for greetings, general questions, or when asking for client_id.

    Args:
        response: Your response to the user
    """
    debug_print(f"[{ctx.deps.current_agent_name}] Responding to user")
    return response

async def route_to_beneficiary_agent(ctx: RunContext[AgentDependencies], client_id: str) -> str:
    """
    Route to the beneficiary agent for beneficiary-related requests.
    This function signals the handoff - the main loop will execute it.

    Args:
        client_id: The client's ID (must be provided)
    """
    try:
        if not client_id or client_id.strip() == "":
            raise ValueError("client_id is required for routing to beneficiary agent")

        debug_print(f"[{ctx.deps.current_agent_name}] Routing to {BENE_AGENT_NAME}")

        ctx.deps.client_id = client_id
        ctx.deps.next_agent = BENE_AGENT_NAME
        ctx.deps.trigger_message = "Process the user's beneficiary request from the conversation history."

        return ""  # Empty response - routing happens in main loop
    except Exception as e:
        logger.error(f"Error in route_to_beneficiary_agent: {e}")
        return f"I encountered a problem with the system. Please try again. (Debug: {str(e)})"

async def route_to_investment_agent(ctx: RunContext[AgentDependencies], client_id: str) -> str:
    """
    Route to the investment agent for investment-related requests.
    This function signals the handoff - the main loop will execute it.

    Args:
        client_id: The client's ID (must be provided)
    """
    try:
        if not client_id or client_id.strip() == "":
            raise ValueError("client_id is required for routing to investment agent")

        debug_print(f"[{ctx.deps.current_agent_name}] Routing to {INVEST_AGENT_NAME}")

        ctx.deps.client_id = client_id
        ctx.deps.next_agent = INVEST_AGENT_NAME
        ctx.deps.trigger_message = "Process the user's investment request from the conversation history."

        return ""  # Empty response - routing happens in main loop
    except Exception as e:
        logger.error(f"Error in route_to_investment_agent: {e}")
        return f"I encountered a problem with the system. Please try again. (Debug: {str(e)})"

# Beneficiary Agent Output Functions
async def respond_about_beneficiaries(ctx: RunContext[AgentDependencies], response: str) -> str:
    """
    Respond to the user about beneficiary matters.
    Only use this for beneficiary-related responses.
    Keep responses concise and professional.

    Args:
        response: Your response about beneficiaries
    """
    try:
        debug_print(f"[{ctx.deps.current_agent_name}] Responding about beneficiaries")

        # Check if this is a confirmation request (should not validate format)
        is_confirmation_request = (
            'are you sure' in response.lower() and
            'confirm' in response.lower()
        )

        # Check if this looks like a beneficiary list response
        # (contains "beneficiar" and mentions names/relationships)
        is_list_response = (
            'beneficiar' in response.lower() and
            ('(' in response and ')' in response) and  # Has relationship in parentheses
            not is_confirmation_request  # Don't validate confirmation requests
        )

        if is_list_response:
            # Validate format - MUST use numbered list
            if not any(line.strip().startswith(('1.', '2.', '3.', '4.')) for line in response.split('\n')):
                raise ModelRetry(
                    "CRITICAL FORMAT ERROR: You are listing beneficiaries but NOT using numbered format. "
                    "You MUST use this EXACT format:\n\n"
                    "Here are your current beneficiaries:\n\n"
                    "1. [Name] ([Relationship])\n"
                    "2. [Name] ([Relationship])\n\n"
                    "Would you like to add, remove or list your beneficiaries?\n\n"
                    "DO NOT use comma-separated format like 'John Doe (son), Jane Doe (daughter)'"
                )

            # Check for forbidden words
            forbidden_words = ['update', 'edit', 'modify', 'change', 'manage', 'further', 'let me know', 'if you need']
            response_lower = response.lower()

            for word in forbidden_words:
                if word in response_lower:
                    raise ModelRetry(
                        f"Response contains forbidden word '{word}'. You MUST use EXACTLY this ending: "
                        "'Would you like to add, remove or list your beneficiaries?' "
                        "Do NOT use: update, edit, modify, change, manage, or phrases like 'let me know'."
                    )

            # Check for required exact question
            if "Would you like to add, remove or list your beneficiaries?" not in response:
                raise ModelRetry(
                    "Response must end with EXACTLY: 'Would you like to add, remove or list your beneficiaries?' "
                    "Copy this text precisely. This question MUST be included after every beneficiary list."
                )

        return response
    except ModelRetry:
        raise  # Re-raise ModelRetry so the model tries again
    except Exception as e:
        logger.error(f"Error in respond_about_beneficiaries: {e}")
        return f"I encountered a problem with the system. Please try again. (Debug: {str(e)})"

async def route_from_beneficiary_to_supervisor(ctx: RunContext[AgentDependencies], client_id: str) -> str:
    """
    Route back to supervisor when the request is not beneficiary-related.
    Use this immediately if the user asks about investments or other topics.

    Args:
        client_id: The client's ID
    """
    try:
        if not client_id or client_id.strip() == "":
            raise ValueError("client_id is required for routing")

        debug_print(f"[{ctx.deps.current_agent_name}] Routing back to {SUPERVISOR_AGENT_NAME}")

        ctx.deps.client_id = client_id
        ctx.deps.next_agent = SUPERVISOR_AGENT_NAME
        ctx.deps.trigger_message = "The user has a new request. Route it to the appropriate agent."

        return ""  # Empty response - routing happens in main loop
    except Exception as e:
        logger.error(f"Error in route_from_beneficiary_to_supervisor: {e}")
        return f"I encountered a problem with the system. Please try again. (Debug: {str(e)})"

# Investment Agent Output Functions
async def respond_about_investments(ctx: RunContext[AgentDependencies], response: str) -> str:
    """
    Respond to the user about investment matters.
    Only use this for investment-related responses.
    Keep responses concise and professional.

    Args:
        response: Your response about investments
    """
    try:
        debug_print(f"[{ctx.deps.current_agent_name}] Responding about investments")

        # Check if this is a confirmation request (should not validate format)
        is_confirmation_request = (
            'are you sure' in response.lower() and
            'confirm' in response.lower()
        )

        # Check if this looks like an investment list response
        # (contains "investment" or "account" and mentions money/balance)
        is_list_response = (
            ('investment' in response.lower() or 'account' in response.lower()) and
            ('$' in response or 'balance' in response.lower()) and
            not is_confirmation_request  # Don't validate confirmation requests
        )

        if is_list_response:
            # Validate format - MUST use numbered list
            if not any(line.strip().startswith(('1.', '2.', '3.', '4.')) for line in response.split('\n')):
                raise ModelRetry(
                    "CRITICAL FORMAT ERROR: You are listing investments but NOT using numbered format. "
                    "You MUST use this EXACT format:\n\n"
                    "Here are your investment accounts:\n\n"
                    "1. [Account Name] - Balance: $[amount]\n"
                    "2. [Account Name] - Balance: $[amount]\n\n"
                    "Would you like to open, close or list your investment accounts?\n\n"
                    "DO NOT use comma-separated format or prose descriptions"
                )

            # Check for forbidden words
            forbidden_words = ['update', 'edit', 'modify', 'change', 'manage', 'details', 'make changes', 'further', 'let me know', 'if you need', 'wish to']
            response_lower = response.lower()

            for word in forbidden_words:
                if word in response_lower:
                    raise ModelRetry(
                        f"Response contains forbidden word/phrase '{word}'. You MUST use EXACTLY this ending: "
                        "'Would you like to open, close or list your investment accounts?' "
                        "Do NOT use: update, edit, modify, change, manage, details, make changes, or phrases like 'let me know' or 'wish to'."
                    )

            # Check for required exact question
            if "Would you like to open, close or list your investment accounts?" not in response:
                raise ModelRetry(
                    "Response must end with EXACTLY: 'Would you like to open, close or list your investment accounts?' "
                    "Copy this text precisely. This question MUST be included after every investment list."
                )

        return response
    except ModelRetry:
        raise  # Re-raise ModelRetry so the model tries again
    except Exception as e:
        logger.error(f"Error in respond_about_investments: {e}")
        return f"I encountered a problem with the system. Please try again. (Debug: {str(e)})"

async def route_from_investment_to_supervisor(ctx: RunContext[AgentDependencies], client_id: str) -> str:
    """
    Route back to supervisor when the request is not investment-related.
    Use this immediately if the user asks about beneficiaries or other topics.

    Args:
        client_id: The client's ID
    """
    try:
        if not client_id or client_id.strip() == "":
            raise ValueError("client_id is required for routing")

        debug_print(f"[{ctx.deps.current_agent_name}] Routing back to {SUPERVISOR_AGENT_NAME}")

        ctx.deps.client_id = client_id
        ctx.deps.next_agent = SUPERVISOR_AGENT_NAME
        ctx.deps.trigger_message = "The user has a new request. Route it to the appropriate agent."

        return ""  # Empty response - routing happens in main loop
    except Exception as e:
        logger.error(f"Error in route_from_investment_to_supervisor: {e}")
        return f"I encountered a problem with the system. Please try again. (Debug: {str(e)})"

### Confirmation Validation Helper

def check_for_confirmation_in_history(context: RunContext[AgentDependencies], action_type: str) -> bool:
    """
    Check if the user has provided confirmation in recent message history.

    Args:
        context: The run context with message history
        action_type: Type of action ('delete' or 'close')

    Returns:
        True if confirmation found, False otherwise
    """
    # Look at the last few messages for confirmation keywords
    confirmation_keywords = ['yes', 'confirm', 'sure', 'ok', 'proceed', 'go ahead', 'correct', 'affirmative']

    # Get recent messages (last 3 user messages)
    recent_messages = []
    for msg in reversed(context.messages):
        if hasattr(msg, 'parts'):
            for part in msg.parts:
                if hasattr(part, 'content') and isinstance(part.content, str):
                    # Check if this is a user message (not system/model)
                    if part.part_kind == 'user-prompt':
                        recent_messages.append(part.content.lower())
                        if len(recent_messages) >= 3:
                            break
        if len(recent_messages) >= 3:
            break

    # Check if any recent message contains confirmation
    for msg in recent_messages:
        if any(keyword in msg for keyword in confirmation_keywords):
            return True

    return False

### Managers

beneficiaries_mgr = BeneficiariesManager()
investment_mgr = InvestmentManager()

### Agents

AGENT_MODEL = 'openai:gpt-4.1'

supervisor_agent = Agent(
    AGENT_MODEL,
    name=SUPERVISOR_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=[
        respond_to_user,
        route_to_beneficiary_agent,
        route_to_investment_agent
    ],
    system_prompt=SUPERVISOR_INSTRUCTIONS,
)

beneficiary_agent = Agent(
    AGENT_MODEL,
    name=BENE_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=[
        respond_about_beneficiaries,
        route_from_beneficiary_to_supervisor
    ],
    system_prompt=BENE_INSTRUCTIONS,
)

investment_agent = Agent(
    AGENT_MODEL,
    name=INVEST_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=[
        respond_about_investments,
        route_from_investment_to_supervisor
    ],
    system_prompt=INVEST_INSTRUCTIONS,
)

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
        """
        Delete a beneficiary. REQUIRES user confirmation before calling this.

        Args:
            beneficiary_id: The ID of the beneficiary to delete

        Returns:
            Success message or error if confirmation not found
        """
        # Check for confirmation in recent history
        if not check_for_confirmation_in_history(context, 'delete'):
            raise ModelRetry(
                "CRITICAL ERROR: You attempted to delete a beneficiary WITHOUT asking for user confirmation first. "
                "You MUST:\n"
                "1. Ask: 'Are you sure you want to remove [Name]? Please confirm.'\n"
                "2. Wait for user response\n"
                "3. ONLY THEN call delete_beneficiaries\n\n"
                "Do NOT call this tool again until the user has confirmed."
            )

        logger.info(f"Tool: Deleting beneficiary {beneficiary_id} from account {context.deps.client_id}")
        beneficiaries_mgr.delete_beneficiary(context.deps.client_id, beneficiary_id)
        return "Beneficiary deleted successfully"


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
    Close an investment account. REQUIRES user confirmation before calling this.

    Args:
        investment_id: The ID of the investment account to close

    Returns:
        Success message or error if confirmation not found
    """
    # Check for confirmation in recent history
    if not check_for_confirmation_in_history(context, 'close'):
        raise ModelRetry(
            "CRITICAL ERROR: You attempted to close an investment account WITHOUT asking for user confirmation first. "
            "You MUST:\n"
            "1. Ask: 'Are you sure you want to close [Account Name]? Please confirm.'\n"
            "2. Wait for user response\n"
            "3. ONLY THEN call close_investment\n\n"
            "Do NOT call this tool again until the user has confirmed."
        )

    return investment_mgr.delete_investment_account(
        client_id=context.deps.client_id,
        investment_id=investment_id)
