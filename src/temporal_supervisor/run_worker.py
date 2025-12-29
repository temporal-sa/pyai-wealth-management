import asyncio
import logging

from temporalio import worker
from temporalio.client import Client
from temporalio.worker import Worker

from pydantic_ai.durable_exec.temporal import (
    PydanticAIPlugin,
    PydanticAIWorkflow,
    TemporalAgent,
)

from common.client_helper import ClientHelper
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow

async def main():
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)s | %(message)s")
    
    client_helper = ClientHelper()
    plugins = [ PydanticAIPlugin()]
    print(f"address is {client_helper.address} and plugins are {plugins}")
    client = await Client.connect(target_host=client_helper.address,
                                  namespace=client_helper.namespace,
                                  tls=client_helper.get_tls_config(),
                                  plugins=plugins)

    worker = Worker(
        client,
        task_queue=client_helper.taskQueue, 
        workflows=[
            WealthManagementWorkflow,
        ],
    )

    print(f"Running worker on {client_helper.address}")
    await worker.run()

if __name__ == '__main__':
    asyncio.run(main())