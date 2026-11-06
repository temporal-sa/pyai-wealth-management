import asyncio
import logging
from typing import Sequence, Type

from temporalio import worker
from temporalio.client import Client
from temporalio.worker import Worker

from pydantic_ai.durable_exec.temporal import (
    PydanticAIPlugin,
    PydanticAIWorkflow,
    TemporalAgent,
)

from common.client_helper import ClientHelper
from temporal_supervisor.activities.event_stream_activities import EventStreamActivities
from temporal_supervisor.activities.local_activities import LocalActivities
from temporal_supervisor.activities.clients import ClientActivities
from temporal_supervisor.activities.open_account import OpenAccount
from temporal_supervisor.activities.investments import Investments
from temporal_supervisor.activities.funding import Funding
from temporal_supervisor.activities.custodian import Custodian
from temporal_supervisor.model_config import LLM_MAX_CALLS_PER_SECOND, LLM_TASK_QUEUE
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow

from temporal_supervisor.claim_check.claim_check_plugin import ClaimCheckPlugin

from temporalio.envconfig import ClientConfig

def choose_workflows(client_helper: ClientHelper, supervisor_workflow: Type | None) -> Sequence[Type]:
    # returns the workflows depending if we have one or two task queues,
    # and whether the agents (the supervisor workflow) are skipped
    workflows = [supervisor_workflow] if supervisor_workflow else []
    if client_helper.taskQueue == client_helper.taskQueueOpenAccount:
        workflows.append(OpenInvestmentAccountWorkflow)
    return workflows

async def main():
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)s | %(message)s")
    
    client_helper = ClientHelper()
    # Normally the worker runs the supervisor workflow and its agents. To just test the
    # open account workflow (startlocalopenaccount.sh), SKIP_AGENTS leaves them out: the
    # agents build their OpenAI model on import, which needs a key. PydanticAIPlugin stays
    # for its data converter, so payloads are encoded as they are for the chat.
    if client_helper.skipAgents:
        WealthManagementWorkflow = None
        print("******* Skipping the agents (SKIP_AGENTS=True): no supervisor workflow or model calls *******")
    else:
        from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow
    plugins = [ PydanticAIPlugin(), ClaimCheckPlugin() ]
    print(f"address is {client_helper.address} and plugins are {plugins}")
    client = await Client.connect(**client_helper.client_config,
                                  plugins=plugins)

    # for the demo, we're using the same task queue as
    # the agents and the child workflow. In a production
    # situation, this would likely be a different task queue
    worker = Worker(
        client,
        task_queue=client_helper.taskQueue, 
        workflows=choose_workflows(client_helper, WealthManagementWorkflow),
        activities=[
            Investments.list_investments,
            Investments.validate_new_account,
            Investments.find_promotion,
            Investments.open_investment,
            Investments.remove_investment,
            Investments.close_investment,
            Funding.debit_linked_account,
            Funding.credit_linked_account,
            Custodian.register_account,
            ClientActivities.get_client,
            ClientActivities.add_client,
            ClientActivities.update_client,
            OpenAccount.get_current_client_info,
            OpenAccount.update_client_details,
            OpenAccount.approve_kyc,
            EventStreamActivities.append_chat_interaction,
            EventStreamActivities.append_status_update,
            EventStreamActivities.delete_conversation,
            LocalActivities.get_task_queue_open_account,
        ],
    )

    if WealthManagementWorkflow is None:
        print(f"Running worker on {client_helper.address}, task queue {client_helper.taskQueue}")
        await worker.run()
        return

    # Model calls run on their own task queue so they can be rate limited without
    # slowing down the tools. The limit is enforced by the Temporal service across
    # all workers polling the queue. PydanticAIPlugin only registers an agent's
    # activities on workers that run its workflow, so register them here directly.
    llm_worker = Worker(
        client,
        task_queue=LLM_TASK_QUEUE,
        activities=[
            agent_activity
            for agent in WealthManagementWorkflow.__pydantic_ai_agents__
            for agent_activity in agent.temporal_activities
        ],
        max_task_queue_activities_per_second=LLM_MAX_CALLS_PER_SECOND,
    )

    limit = f"{LLM_MAX_CALLS_PER_SECOND}/s" if LLM_MAX_CALLS_PER_SECOND else "unlimited"
    print(f"Running worker on {client_helper.address}, task queues {client_helper.taskQueue} "
          f"and {LLM_TASK_QUEUE} (model calls: {limit})")
    await asyncio.gather(worker.run(), llm_worker.run())

if __name__ == '__main__':
    asyncio.run(main())