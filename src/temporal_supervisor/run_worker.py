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
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow

from temporal_supervisor.claim_check.claim_check_plugin import ClaimCheckPlugin

from temporalio.envconfig import ClientConfig

def choose_workflows(client_helper: ClientHelper) -> Sequence[Type]:
    # returns the workflows depending if we have one or two task queues
    if client_helper.taskQueue == client_helper.taskQueueOpenAccount:
        return [
            WealthManagementWorkflow,
            OpenInvestmentAccountWorkflow,
        ]
    else:
        return [
            WealthManagementWorkflow,
        ]

async def main():
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)s | %(message)s")
    
    client_helper = ClientHelper()
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
        workflows=choose_workflows(client_helper),
        activities=[
            Investments.list_investments,
            Investments.open_investment,
            Investments.close_investment,
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

    print(f"Running worker on {client_helper.address}")
    await worker.run()

if __name__ == '__main__':
    asyncio.run(main())