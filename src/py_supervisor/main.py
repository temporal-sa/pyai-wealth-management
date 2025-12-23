import asyncio
import uuid
import logging

from typing import Optional, TypedDict

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, ModelMessage
from dataclasses import dataclass

from common.agent_constants import RECOMMENDED_PROMPT_PREFIX
from common.beneficiaries_manager import BeneficiariesManager

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
class BeneficiaryDependencies:
    client_id: Optional[str] = None

### Output classes
class BeneficiaryAgentOutput(BaseModel):
    response: str 
    """ Response to the client """
    client_id: str
    """ Set if we have been given one """
    need_more_info: bool
    """ Set if more information is needed from the client """

### Agents
AGENT_MODEL = 'openai:gpt-4.1'

beneficiary_agent = Agent(
    AGENT_MODEL,
    name="Beneficiaries Agent",
    deps_type=BeneficiaryDependencies,
    output_type=BeneficiaryAgentOutput,
    system_prompt=f"""{RECOMMENDED_PROMPT_PREFIX}
    You are a beneficiary agent. 
    You are responsible for handling all aspects of beneficiaries. This includes adding, listing and deleting beneficiaries.
    # Routine
    1. Display a list of their beneficiaries using the `list_beneficiaries` tool. 
       Remember the beneficiary id but don't display it.
       Ask what they would like to do next: add a beneficiary, delete a beneficiary or list their beneficiaries. 
    2. Ask if they would like to add, delete or list their beneficiaries. 
       Use the `add_beneficiaries` tool for adding a beneficiary. Prompt for the information necessary to perform the add operation. 
       Use the `delete_beneficiaries` tool for deleting a beneficiary. Prompt for the information necessary to peform the delete operation. 
       If they want to delete a beneficiary, use the beneficiary id that is mapped to their choice. 
       Ask for confirmation before deleting the beneficiary.
    3. If there isn't a tool available state that the operation cannot be completed at this time.     """
)

### Managers 

beneficiaries_mgr = BeneficiariesManager()

### Tools

@beneficiary_agent.tool
async def add_beneficiaries(
        context: RunContext[BeneficiaryDependencies],
        first_name: str, last_name: str, relationship: str
) -> None:
    beneficiaries_mgr.add_beneficiary(context.deps.client_id, first_name, last_name, relationship)
    print("beneficiary")

@beneficiary_agent.tool
async def list_beneficiaries(
        context: RunContext[BeneficiaryDependencies]
) -> list:
    """
    List the beneficiaries for the given client id.

    Args:
        client_id: The customer's client id
    """
    return beneficiaries_mgr.list_beneficiaries(context.deps.client_id)

@beneficiary_agent.tool
async def delete_beneficiaries(
        context: RunContext[BeneficiaryDependencies], beneficiary_id: str):
        logger.info(f"Tool: Deleting beneficiary {beneficiary_id} from account {context.deps.client_id}")
        beneficiaries_mgr.delete_beneficiary(context.deps.client_id, beneficiary_id)

async def main():
    print("Welcome to ABC Wealth Management. How can I help you?")
    deps = BeneficiaryDependencies(client_id="123")
    chat_messages : List[ChatMessage] = []
    message_history : List[ModelMessage] = []
    while True:
        user_input = input("Enter your message: ")
        lower_input = user_input.lower() if user_input is not None else ""
        if lower_input == "exit" or lower_input == "end" or lower_input == "quit":
            break;

        result = await beneficiary_agent.run(user_input, deps=deps, 
            message_history=message_history)

        print(f"Message history is {message_history}")
        
        # save this to have the context for the next call. 
        message_history=result.all_messages()

        print(result.output.response)
        
if __name__ == "__main__":
     asyncio.run(main())
