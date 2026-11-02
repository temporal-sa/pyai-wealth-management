import logging
from datetime import timedelta
from typing import Optional
from dataclasses import dataclass, asdict

from pydantic_ai import Agent, RunContext, ModelRetry

from common.agent_constants import (
    AGENT_MODEL,
    SUPERVISOR_AGENT_NAME, SUPERVISOR_INSTRUCTIONS,
    BENE_AGENT_NAME, BENE_INSTRUCTIONS,
    INVEST_AGENT_NAME, INVEST_INSTRUCTIONS,
    OPEN_ACCOUNT_AGENT_NAME, OPEN_ACCOUNT_INSTRUCTIONS,
)
from common.beneficiaries_manager import BeneficiariesManager
from common.investment_manager import InvestmentManager, InvestmentAccount
from common.domain_classes import OpenInvestmentAccountInput, WealthManagementClient

from temporalio import workflow
from temporalio.workflow import ParentClosePolicy
from temporalio.client import Client, WorkflowHandle

from common.client_helper import ClientHelper
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow
from temporal_supervisor.activities.open_account import OpenAccount
from temporal_supervisor.activities.investments import Investments

logger = logging.getLogger(__name__)

### Debug Configuration
DEBUG_MODE = False  # Set to True to see handoff routing debug messages

def debug_print(message: str):
    """Print debug messages only when DEBUG_MODE is enabled"""
    if DEBUG_MODE:
        print(message)

# Import Temporal plugins for the helper function
try:
    from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
    from temporal_supervisor.claim_check.claim_check_plugin import ClaimCheckPlugin
except ImportError as e:
    logger.error(f"Unable to initialize plugins: {e}")
    PydanticAIPlugin = None
    ClaimCheckPlugin = None

### Dependencies
@dataclass
class AgentDependencies:
    client_id: Optional[str] = None
    next_agent: Optional[str] = None  # Signals routing to another agent
    trigger_message: Optional[str] = None  # Message for next agent
    current_agent_name: str = SUPERVISOR_AGENT_NAME  # For debugging/logging
    message_history: list = None  # Message history for confirmation checking
    pending_account_info: Optional[OpenInvestmentAccountInput] = None  # Account info being passed between agents
    task_queue_open_account: Optional[str] = None  # Task queue for opening account child workflows
    active_account_workflow_id: Optional[str] = None  # Workflow ID for active account opening process
    current_client_info: Optional['WealthManagementClient'] = None  # Original client info for KYC updates

    def __post_init__(self):
        if self.message_history is None:
            self.message_history = []

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

    IMPORTANT: This function ALWAYS fetches fresh investment data via activity
    to ensure the agent has current information, regardless of request type or routing path.

    Args:
        client_id: The client's ID (must be provided)
    """
    try:
        if not client_id or client_id.strip() == "":
            raise ValueError("client_id is required for routing to investment agent")

        debug_print(f"[{ctx.deps.current_agent_name}] Routing to {INVEST_AGENT_NAME}")

        ctx.deps.client_id = client_id
        ctx.deps.next_agent = INVEST_AGENT_NAME

        # ALWAYS fetch fresh investment data to ensure agent has current information
        # This prevents stale data issues regardless of routing path
        logger.info(f"Fetching fresh investment data for client {client_id}")

        # Call the activity to fetch fresh data (cannot do file I/O in workflow context)
        investments_list = await workflow.execute_activity(
            Investments.list_investments,
            args=[client_id],
            schedule_to_close_timeout=timedelta(seconds=30)
        )

        # Format the data for the agent
        if investments_list:
            formatted_data = "CURRENT INVESTMENT ACCOUNTS (fetched fresh from database):\n"
            for inv in investments_list:
                formatted_data += f"- {inv['name']}: ${inv['balance']:.2f} (ID: {inv['investment_id']})\n"

            ctx.deps.trigger_message = f"Here is the current investment data for client {client_id}:\n\n{formatted_data}\nRespond to the user's request using this data."
        else:
            ctx.deps.trigger_message = f"Client {client_id} has NO investment accounts in the system. Handle the user's request accordingly."

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
        # (contains "beneficiar" and has numbered list format or mentions relationships)
        is_list_response = (
            'beneficiar' in response.lower() and
            (
                ('(' in response and ')' in response) or  # Has relationship in parentheses like "(Son)"
                (' - ' in response) or  # Has relationship with dash like "- Son"
                any(line.strip().startswith(('1.', '2.', '3.', '4.')) for line in response.split('\n'))  # Has numbered list
            ) and
            not is_confirmation_request  # Don't validate confirmation requests
        )

        if is_list_response:
            # VALIDATION DISABLED - Per MEMORY.md: Strict validation causes workflow crashes
            # Instead of validating/retrying, we FIX the response programmatically

            # Fix literal \n characters (LLM sometimes generates these instead of actual newlines)
            if '\\n' in response:
                response = response.replace('\\n', '\n')

            # Ensure the required ending question is present
            required_ending = "Would you like to add, remove or list your beneficiaries?"
            if required_ending not in response:
                # Add it if missing (non-crashing fallback)
                if not response.endswith('\n'):
                    response += '\n'
                response += f"\n{required_ending}"

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

        # VALIDATION: If trying to list investments, ensure tool was called
        # NOTE: Tool call validation disabled - it caused workflow crashes.
        # The LLM strongly prefers responding from memory over calling tools.
        # Even with ModelRetry telling it to call the tool, it retries without calling,
        # leading to "Exceeded maximum retries" and workflow failure.
        #
        # Known limitation: First "list investments" after opening account may show stale data.
        # Instructions remain to encourage tool calls, but no enforcement.

        if is_list_response:
            # VALIDATION DISABLED - Per MEMORY.md: Strict validation causes workflow crashes
            # Instead of validating/retrying, we FIX the response programmatically

            # Fix literal \n characters (LLM sometimes generates these instead of actual newlines)
            if '\\n' in response:
                response = response.replace('\\n', '\n')

            # Ensure the required ending question is present
            required_ending = "Would you like to open, close or list your investment accounts?"
            if required_ending not in response:
                # Add it if missing (non-crashing fallback)
                if not response.endswith('\n'):
                    response += '\n'
                response += f"\n{required_ending}"

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

async def route_from_investment_to_open_account(ctx: RunContext[AgentDependencies], client_id: str, account_info: OpenInvestmentAccountInput) -> str:
    """
    Route to Open Account Agent when the request is for opening a new investment account.

    Args:
        client_id: The client's ID
        account_info: The account details (account_name, initial_amount)
    """
    try:
        if not client_id or client_id.strip() == "":
            raise ValueError("client_id is required for routing")

        # Clean the account name - remove " account" or " Account" suffix if present
        cleaned_name = account_info.account_name.strip()
        if cleaned_name.lower().endswith(" account"):
            cleaned_name = cleaned_name[:-8].strip()

        # Create cleaned account_info
        cleaned_account_info = OpenInvestmentAccountInput(
            client_id=account_info.client_id,
            account_name=cleaned_name,
            initial_amount=account_info.initial_amount
        )

        debug_print(f"[{ctx.deps.current_agent_name}] Routing to {OPEN_ACCOUNT_AGENT_NAME} with account info: {cleaned_account_info}")

        # Store the cleaned account info so the Open Account Agent can access it
        ctx.deps.client_id = client_id
        ctx.deps.pending_account_info = cleaned_account_info
        ctx.deps.next_agent = OPEN_ACCOUNT_AGENT_NAME
        ctx.deps.trigger_message = f"The user wants to open a new account: {cleaned_name} with ${account_info.initial_amount}"

        return ""  # Empty response - routing happens in main loop

    except Exception as e:
        logger.error(f"Error in route_from_investment_to_open_account: {e}")
        return f"I encountered a problem with the system. Please try again. (Debug: {str(e)})" 

# Open Account Output Functions

async def respond_about_account_opening(ctx: RunContext[AgentDependencies], response: str) -> str:
    """
    Respond to the user about account opening matters.

    AUTOMATIC PROCESSING: If pending_account_info exists, this function will automatically:
    1. Start the account opening workflow
    2. Retrieve client KYC information
    3. Format and return a response with the client details

    This removes the need for the LLM to orchestrate the tool calls.

    Args:
        response: Your response about the account opening process (used if no pending_account_info)
    """
    debug_print(f"[{ctx.deps.current_agent_name}] Responding about account opening")

    # VALIDATION: Detect if agent is trying to handle non-KYC requests
    # These keywords indicate the user is asking about something OTHER than the current KYC process
    forbidden_keywords = [
        'list', 'show', 'display', 'what investments', 'what accounts',
        'my investments', 'my accounts', 'beneficiaries', 'close',
        'checking', 'savings', '401k', 'existing'
    ]

    response_lower = response.lower()
    for keyword in forbidden_keywords:
        if keyword in response_lower:
            workflow.logger.warning(
                f"⚠️ VALIDATION FAILED: respond_about_account_opening called with forbidden keyword '{keyword}'. "
                f"Agent should use route_from_open_account_to_supervisor instead!"
            )
            raise ModelRetry(
                "respond_about_account_opening is ONLY for KYC approval/update responses. "
                "For requests about listing accounts, viewing investments, or other operations, "
                "you MUST use route_from_open_account_to_supervisor(client_id) instead."
            )

    # AUTO-HANDLE: If we have pending_account_info, automatically process the account opening
    if ctx.deps.pending_account_info is not None:
        account_info = ctx.deps.pending_account_info
        workflow.logger.info(f"Auto-processing account opening for {account_info.account_name}")

        try:
            # 1. Start the child workflow automatically by calling the tool function directly
            workflow_id = await start_account_opening_workflow(ctx, account_info)
            workflow.logger.info(f"Started workflow: {workflow_id}")

            # Store workflow_id in deps so agent can access it for subsequent operations
            ctx.deps.active_account_workflow_id = workflow_id

            # 2. Get client KYC info automatically
            client = await workflow.execute_activity(
                OpenAccount.get_current_client_info,
                args=[workflow_id],
                schedule_to_close_timeout=timedelta(seconds=30) ## TODO: remove hard coding
            )

            workflow.logger.info(f"Retrieved client info for {client.first_name} {client.last_name}")

            # Store client info in deps so agent can use it for updates
            ctx.deps.current_client_info = client

            # 3. Format response with actual KYC info
            response = f"""I've started opening your {account_info.account_name} account. Let me verify your information:

- First Name: {client.first_name}
- Last Name: {client.last_name}
- Address: {client.address}
- Phone: {client.phone}
- Email: {client.email}
- Marital Status: {client.marital_status}

Is this information correct and up to date? Please confirm.

(Workflow ID: {workflow_id})"""

            # Clear pending_account_info since we've processed it
            ctx.deps.pending_account_info = None
            workflow.logger.info("Cleared pending_account_info")

        except Exception as e:
            logger.error(f"Error auto-processing account opening: {e}", exc_info=True)
            return f"I encountered an error starting the account opening process: {str(e)}"

    return response

# helper function
async def get_workflow_handle(workflow_id) -> WorkflowHandle:
    client_helper = ClientHelper()
    print(f"(OpenAccount.get_temporal_client) address is {client_helper.address}")
    the_client = await Client.connect(**client_helper.client_config,
                                      plugins=[ PydanticAIPlugin(), ClaimCheckPlugin() ])
    return the_client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)

async def open_new_investment_account(ctx: RunContext[AgentDependencies], account_input: OpenInvestmentAccountInput) -> str:
    """
    Open a new investment account by starting a child workflow.

    When running inside a Temporal workflow, this directly starts a child workflow
    for the account opening process.

    Args:
        account_input: The account details (client_id, account_name, initial_amount)

    Returns:
        Success message with child workflow ID or error message
    """
    try:
        # When running inside a Temporal workflow context, we can access workflow functions
        if workflow.in_workflow():
            # Get the task queue from deps (set by the workflow)
            task_queue = ctx.deps.task_queue_open_account
            if not task_queue:
                raise ValueError("task_queue_open_account not set in AgentDependencies")

            # Get the current workflow's ID to create a unique child workflow ID
            current_workflow_id = workflow.info().workflow_id
            child_workflow_id = f"OpenAccount-{current_workflow_id}-{account_input.client_id}-{account_input.account_name}"

            workflow.logger.info(f"Starting open account child workflow with ID: {child_workflow_id} on task queue: {task_queue}")

            # Start the child workflow directly - runs at workflow level!
            await workflow.start_child_workflow(
                OpenInvestmentAccountWorkflow.run,
                args=[account_input],
                id=child_workflow_id,
                parent_close_policy=ParentClosePolicy.TERMINATE,
                task_queue=task_queue
            )

            return f"Your account opening request has been submitted. Workflow ID: {child_workflow_id}"
        else:
            # Fallback for non-workflow contexts (testing, etc.)
            logger.warning("open_new_investment_account called outside workflow context")
            return "Account opening is only available when running in a workflow context."

    except Exception as e:
        logger.error(f"Error starting account opening workflow: {e}")
        return f"I encountered a problem starting the account opening process. Please try again. (Debug: {str(e)})"

async def get_current_client_info(workflow_id: str) -> WealthManagementClient:
    # get the handle from the workflow id
    logger.info(f"Retrieving current client info for {workflow_id}")
    handle = await get_workflow_handle(workflow_id)
    client = await handle.execute_update("get_client_details")
    return client; 

async def approve_kyc(workflow_id: str):
    handle = await get_workflow_handle(workflow_id)
    await handle.signal(OpenInvestmentAccountWorkflow.verify_kyc)
        
async def update_client_details(workflow_id: str, client_details: WealthManagementClient) -> str: 
    handle = await get_workflow_handle(workflow_id)
    # convert the data class to a dict
    client_details_dict = asdict(client_details)
    result = await handle.execute_update(OpenInvestmentAccountWorkflow.update_client_details,
        args=[client_details_dict])
    return result


async def route_from_open_account_to_supervisor(ctx: RunContext[AgentDependencies], client_id: str) -> str:
    """
    Route back to supervisor when the request is not related to opening investments,
    or checking the status of a newly opened investment account.
    Use this immediately if the user asks about beneficiaries or other topics.

    Args:
        ctx: The run context with dependencies
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
        logger.error(f"Error in route_from_open_account_to_supervisor: {e}")
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

    # Get message history from deps (works in both Temporal and non-Temporal contexts)
    message_history = context.deps.message_history

    debug_print(f"Checking confirmation in history. Total messages: {len(message_history)}")

    # Get recent messages (last 3 user messages)
    recent_messages = []
    for idx, msg in enumerate(reversed(message_history)):
        debug_print(f"Message {idx}: type={type(msg).__name__}, has_parts={hasattr(msg, 'parts')}")
        if hasattr(msg, 'parts'):
            debug_print(f"  Parts count: {len(msg.parts)}")
            for part_idx, part in enumerate(msg.parts):
                debug_print(f"  Part {part_idx}: type={type(part).__name__}, has_content={hasattr(part, 'content')}, has_part_kind={hasattr(part, 'part_kind')}")
                if hasattr(part, 'part_kind'):
                    debug_print(f"    part_kind='{part.part_kind}'")
                if hasattr(part, 'content') and isinstance(part.content, str):
                    debug_print(f"    content='{part.content[:50]}'")
                    # Check if this is a user message (not system/model)
                    if part.part_kind == 'user-prompt':
                        recent_messages.append(part.content.lower())
                        debug_print(f"Found user message: '{part.content}'")
                        if len(recent_messages) >= 3:
                            break
        if len(recent_messages) >= 3:
            break

    debug_print(f"Recent user messages: {recent_messages}")

    # Check if any recent message contains confirmation
    for msg in recent_messages:
        if any(keyword in msg for keyword in confirmation_keywords):
            debug_print(f"Found confirmation keyword in message: '{msg}'")
            return True

    debug_print("No confirmation found in recent messages")
    return False

### Managers

beneficiaries_mgr = BeneficiariesManager()
investment_mgr = InvestmentManager()

### Agents

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
        route_from_investment_to_supervisor,
        route_from_investment_to_open_account,
    ],
    system_prompt=INVEST_INSTRUCTIONS,
)

open_account_agent = Agent(
    AGENT_MODEL,
    name=OPEN_ACCOUNT_AGENT_NAME,
    deps_type=AgentDependencies,
    output_type=[
        respond_about_account_opening,
        route_from_open_account_to_supervisor,
    ],
    system_prompt=OPEN_ACCOUNT_INSTRUCTIONS,
    end_strategy='exhaustive',
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
        context: RunContext[AgentDependencies],
        first_name: str,
        last_name: str,
        user_confirmed: bool = False):
        """
        Delete a beneficiary by their name. REQUIRES user confirmation before calling this.

        CRITICAL: You MUST call this tool after the user confirms deletion.
        Do NOT just say "I will proceed to remove" - actually call this tool!

        IMPORTANT: When calling this tool after user confirmation, you MUST set user_confirmed=True.
        Example: delete_beneficiaries(first_name="Junior", last_name="Doe", user_confirmed=True)

        Args:
            first_name: The first name of the beneficiary to delete (e.g., "Junior")
            last_name: The last name of the beneficiary to delete (e.g., "Doe")
            user_confirmed: Set to True when the user has explicitly confirmed the deletion (default: False)

        Returns:
            Success message or error if confirmation not provided or beneficiary not found
        """
        # Check for confirmation parameter
        if not user_confirmed:
            raise ModelRetry(
                "CRITICAL ERROR: You attempted to delete a beneficiary WITHOUT user confirmation. "
                "You MUST:\n"
                "1. Ask: 'Are you sure you want to remove [Name]? Please confirm.'\n"
                "2. Wait for user response\n"
                "3. When user confirms (says 'yes', 'confirm', etc.), call this tool with user_confirmed=True\n\n"
                "Example: delete_beneficiaries(first_name=\"Junior\", last_name=\"Doe\", user_confirmed=True)\n\n"
                "Do NOT call this tool again until the user has confirmed."
            )

        # Double-check: Validate that the most recent user message is actually a confirmation
        # and not the initial "remove X" request
        message_history = context.deps.message_history
        if message_history:
            # Get the most recent message
            for msg in reversed(message_history):
                if hasattr(msg, 'parts'):
                    for part in msg.parts:
                        if hasattr(part, 'part_kind') and part.part_kind == 'user-prompt':
                            if hasattr(part, 'content') and isinstance(part.content, str):
                                last_user_msg = part.content.lower()

                                # Check if this looks like a "remove X" command rather than a confirmation
                                if 'remove' in last_user_msg and not any(kw in last_user_msg for kw in ['yes', 'confirm', 'sure', 'ok', 'proceed']):
                                    raise ModelRetry(
                                        "CRITICAL ERROR: The user's last message was a remove REQUEST, not a confirmation. "
                                        f"Last message: '{part.content}'\n\n"
                                        "This is Step 1, not Step 2! You MUST:\n"
                                        "1. First ASK: 'Are you sure you want to remove [Name]? Please confirm.'\n"
                                        "2. WAIT for user to respond with 'yes', 'confirm', etc.\n"
                                        "3. ONLY THEN call this tool with user_confirmed=True\n\n"
                                        "Do NOT call this tool until the user explicitly confirms!"
                                    )
                                break
                        break
                    break

        # Look up the beneficiary by name to get the ID
        beneficiaries = beneficiaries_mgr.list_beneficiaries(context.deps.client_id)
        full_name = f"{first_name} {last_name}".lower()

        matching_beneficiary = None
        for bene in beneficiaries:
            bene_full_name = f"{bene['first_name']} {bene['last_name']}".lower()
            if bene_full_name == full_name:
                matching_beneficiary = bene
                break

        if not matching_beneficiary:
            return f"ERROR: Could not find beneficiary named '{first_name} {last_name}'"

        beneficiary_id = matching_beneficiary['beneficiary_id']
        debug_print(f"Tool: Deleting beneficiary {first_name} {last_name} (ID: {beneficiary_id}) from account {context.deps.client_id}")
        beneficiaries_mgr.delete_beneficiary(context.deps.client_id, beneficiary_id)
        return f"Successfully deleted {first_name} {last_name}"


@investment_agent.tool
async def list_investments(
    context: RunContext[AgentDependencies],
    get_fresh_data: bool = True
) -> list:
    """
    Get the current list of investment accounts from the system.
    Call this whenever the user asks to see, list, or show their investments.
    This returns fresh data directly from the database.

    Args:
        get_fresh_data: Always set to True to retrieve current account data (default: True)

    Returns:
        list: Current investment accounts for the client
    """
    # Check message history to see if this is actually an open request
    if context.deps.message_history:
        # Get the most recent user message
        for msg in reversed(context.deps.message_history):
            if hasattr(msg, 'parts'):
                for part in msg.parts:
                    if hasattr(part, 'content') and isinstance(part.content, str):
                        content_lower = part.content.lower()
                        # Check if this is an open request with details
                        if 'open' in content_lower:
                            # Look for account name and amount patterns
                            import re
                            # Pattern: "open [word] with $[number]" or similar
                            has_amount = bool(re.search(r'\$\s*\d+|\d+\s*dollars?', content_lower))
                            # If they said "open" and there's a dollar amount, they should be routed not listed
                            if has_amount:
                                raise ModelRetry(
                                    "ERROR: You called list_investments, but the user asked to OPEN an account with specific details! "
                                    "The user's request contains 'open' and a dollar amount. "
                                    "You should NOT list accounts - instead, extract the account name and amount, then call "
                                    "route_from_investment_to_open_account(client_id, account_info) immediately. "
                                    "Re-read the user's request and route to Open Account Agent!"
                                )
                        break
                break

    return investment_mgr.list_investment_accounts(context.deps.client_id)


# @investment_agent.tool
# async def open_investment(context: RunContext[AgentDependencies],
#     name: str, balance: float):
#     """
#     Adds a new investment account for the given information
#     """
#     investment_account = InvestmentAccount(
#         client_id=context.deps.client_id,
#         name=name,
#         balance=balance)

#     return investment_mgr.add_investment_account(investment_account)

@investment_agent.tool
async def close_investment(context: RunContext[AgentDependencies],
    investment_id: str,
    user_confirmed: bool = False):
    """
    Close an investment account. REQUIRES user confirmation before calling this.

    CRITICAL: You MUST call this tool after the user confirms closing the account.
    Do NOT just say "I will proceed to close" - actually call this tool!

    IMPORTANT: When calling this tool after user confirmation, you MUST set user_confirmed=True.
    Example: close_investment(investment_id="12345", user_confirmed=True)

    Args:
        investment_id: The ID of the investment account to close
        user_confirmed: Set to True when the user has explicitly confirmed closing the account (default: False)

    Returns:
        Success message or error if confirmation not provided or account not found
    """
    # Check for confirmation parameter
    if not user_confirmed:
        raise ModelRetry(
            "CRITICAL ERROR: You attempted to close an investment account WITHOUT user confirmation. "
            "You MUST:\n"
            "1. Ask: 'Are you sure you want to close [Account Name]? Please confirm.'\n"
            "2. Wait for user response\n"
            "3. When user confirms (says 'yes', 'confirm', etc.), call this tool with user_confirmed=True\n\n"
            "Example: close_investment(investment_id=\"12345\", user_confirmed=True)\n\n"
            "Do NOT call this tool again until the user has confirmed."
        )

    # Double-check: Validate that the most recent user message is actually a confirmation
    # and not the initial "close X" request
    message_history = context.deps.message_history
    if message_history:
        # Get the most recent message
        for msg in reversed(message_history):
            if hasattr(msg, 'parts'):
                for part in msg.parts:
                    if hasattr(part, 'part_kind') and part.part_kind == 'user-prompt':
                        if hasattr(part, 'content') and isinstance(part.content, str):
                            last_user_msg = part.content.lower()

                            # Check if this looks like a "close X" command rather than a confirmation
                            if 'close' in last_user_msg and not any(kw in last_user_msg for kw in ['yes', 'confirm', 'sure', 'ok', 'proceed']):
                                raise ModelRetry(
                                    "CRITICAL ERROR: The user's last message was a close REQUEST, not a confirmation. "
                                    f"Last message: '{part.content}'\n\n"
                                    "This is Step 1, not Step 2! You MUST:\n"
                                    "1. First ASK: 'Are you sure you want to close [Account Name]? Please confirm.'\n"
                                    "2. WAIT for user to respond with 'yes', 'confirm', etc.\n"
                                    "3. ONLY THEN call this tool with user_confirmed=True\n\n"
                                    "Do NOT call this tool until the user explicitly confirms!"
                                )
                            break
                    break
                break

    return investment_mgr.delete_investment_account(
        client_id=context.deps.client_id,
        investment_id=investment_id)


# Open Account Agent Tools

# Called directly from respond_about_account_opening rather than registered as a tool:
# TemporalAgent runs tools as activities, where workflow.start_child_workflow is unavailable.
async def start_account_opening_workflow(ctx: RunContext[AgentDependencies], account_input: OpenInvestmentAccountInput) -> str:
    """
    Start a new investment account opening workflow.
    Returns the workflow ID which you'll need for subsequent operations.

    Args:
        account_input: The account details (client_id, account_name, initial_amount)

    Returns:
        The workflow ID (format: "OpenAccount-{parent_workflow_id}-{client_id}-{account_name}")
    """
    try:
        if not workflow.in_workflow():
            return "Account opening is only available when running in a workflow context."

        task_queue = ctx.deps.task_queue_open_account
        if not task_queue:
            raise ValueError("task_queue_open_account not set in AgentDependencies")

        current_workflow_id = workflow.info().workflow_id
        child_workflow_id = f"OpenAccount-{current_workflow_id}-{account_input.client_id}-{account_input.account_name}"

        workflow.logger.info(f"Starting open account child workflow with ID: {child_workflow_id}")

        await workflow.start_child_workflow(
            OpenInvestmentAccountWorkflow.run,
            args=[account_input],
            id=child_workflow_id,
            parent_close_policy=ParentClosePolicy.TERMINATE,
            task_queue=task_queue
        )

        return child_workflow_id
    except Exception as e:
        logger.error(f"Error starting account opening workflow: {e}")
        return f"Error starting workflow: {str(e)}"

@open_account_agent.tool
async def approve_client_kyc(ctx: RunContext[AgentDependencies]) -> str:
    """
    Approve the client's KYC information and advance the account opening workflow.
    Call this whenever the user confirms their information (says "yes", "confirm", "correct", etc.).
    The workflow_id is automatically retrieved.

    Returns:
        Success message
    """
    workflow_id = ctx.deps.active_account_workflow_id
    logger.info(f"approve_client_kyc: Retrieved workflow_id from deps: {workflow_id}")

    if not workflow_id:
        return "Error: No active account opening workflow found. Please start the account opening process first."

    logger.info(f"approve_client_kyc: Calling approve_kyc with workflow_id: {workflow_id}")
    await approve_kyc(workflow_id)
    return "KYC information approved. The account is now pending compliance review."


@open_account_agent.tool
async def update_kyc_details(ctx: RunContext[AgentDependencies], client_details: WealthManagementClient) -> str:
    """
    Update the client's KYC information after collecting updated values from the user.

    CRITICAL: Only call this tool AFTER you have collected the updated values from the user!
    - If user just said "no" or "incorrect", DO NOT call this tool yet
    - First ask which fields need updating using respond_about_account_opening()
    - Then collect the new values through conversation
    - Finally, call this tool with the updated values

    AUTOMATIC MERGING: You only need to provide the fields that changed!
    - The tool automatically merges your updates with the original client info
    - Fields set to None will be filled from ctx.deps.current_client_info
    - Example: If only address changed, provide WealthManagementClient with address="456 Oak St" and other fields as None

    Args:
        client_details: WealthManagementClient object with the UPDATED fields.
                       Unchanged fields can be None - they'll be automatically filled from the original client info.

    Returns:
        Success or error message
    """
    workflow_id = ctx.deps.active_account_workflow_id
    if not workflow_id:
        return "Error: No active account opening workflow found. Please start the account opening process first."

    # Check if original client info is available for merging
    if not ctx.deps.current_client_info:
        return "Error: Original client information not found. Please restart the account opening process."

    # Auto-merge: If any fields are None, fill them from the original client info
    # This allows the agent to provide just the updated fields
    original = ctx.deps.current_client_info
    merged_client = WealthManagementClient(
        client_id=client_details.client_id if client_details and client_details.client_id else original.client_id,
        first_name=client_details.first_name if client_details and client_details.first_name else original.first_name,
        last_name=client_details.last_name if client_details and client_details.last_name else original.last_name,
        address=client_details.address if client_details and client_details.address else original.address,
        phone=client_details.phone if client_details and client_details.phone else original.phone,
        email=client_details.email if client_details and client_details.email else original.email,
        marital_status=client_details.marital_status if client_details and client_details.marital_status else original.marital_status,
    )

    logger.info(f"Merged client details - Original: {original}, Updates: {client_details}, Merged: {merged_client}")

    return await update_client_details(workflow_id, merged_client)

