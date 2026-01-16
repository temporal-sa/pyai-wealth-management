import asyncio
import logging
import datetime

from typing import List

from pydantic_ai import Agent, ModelMessage
from pydantic_ai.messages import ModelRequest, UserPromptPart

from common.agent_constants import SUPERVISOR_AGENT_NAME, BENE_AGENT_NAME, INVEST_AGENT_NAME
from common.agents import (
    AgentDependencies,
    supervisor_agent,
    beneficiary_agent,
    investment_agent
)

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


class PydanticAIWealthManagement:
    def __init__(self):
        self.agent_deps = AgentDependencies()
        self.message_history: List[ModelMessage] = []

    async def run_agent_loop(self):
        print("Welcome to ABC Wealth Management. How can I help you?")

        while True:
            user_input = input(f"\n[{self.agent_deps.current_agent_name}] Enter your message: ")

            if user_input.lower() in {"exit", "end", "quit"}:
                break

            await self._process_user_message(user_input)

        print("Agent loop complete.")

    async def _process_user_message(self, user_input: str):
        """Process a user message through the agent system with routing."""
        debug_print(f"Processing user message: {user_input}")

        # Add user message to history
        user_message = ModelRequest(
            parts=[UserPromptPart(
                content=user_input,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )]
        )
        self.message_history.append(user_message)

        # Start with supervisor agent
        current_agent = self._get_current_agent()
        current_input = user_input

        # Loop to handle chain routing
        while True:
            # Sync message history to deps before running agent (for confirmation checking)
            self.agent_deps.message_history = self.message_history

            # Run the current agent
            result = await current_agent.run(
                current_input,
                deps=self.agent_deps,
                message_history=self.message_history
            )

            # Add agent's new messages to history
            self.message_history.extend(result.new_messages())

            # Check if output function signaled a route
            if self.agent_deps.next_agent:
                # Routing detected - switch to next agent
                debug_print(f"\n>>> Routing: {self.agent_deps.current_agent_name} → {self.agent_deps.next_agent}")

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
                    print(result.output)
                break

    def _get_current_agent(self) -> Agent:
        """Get the agent instance based on current_agent_name."""
        if self.agent_deps.current_agent_name == BENE_AGENT_NAME:
            return beneficiary_agent
        elif self.agent_deps.current_agent_name == INVEST_AGENT_NAME:
            return investment_agent
        else:
            return supervisor_agent

async def main():
    wealth_management_flow = PydanticAIWealthManagement()
    await wealth_management_flow.run_agent_loop()

if __name__ == "__main__":
     asyncio.run(main())
