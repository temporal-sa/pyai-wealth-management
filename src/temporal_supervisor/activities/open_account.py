from dataclasses import asdict

from temporalio import activity
from temporalio.client import WorkflowHandle, Client

from common.client_helper import ClientHelper
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow
from temporal_supervisor.activities.clients import WealthManagementClient

from temporal_supervisor.claim_check.claim_check_plugin import ClaimCheckPlugin

class OpenAccount:

    @staticmethod
    async def get_workflow_handle(workflow_id) -> WorkflowHandle:
        client_helper = ClientHelper()
        print(f"(OpenAccount.get_temporal_client) address is {client_helper.address}")
        the_client = await Client.connect(**client_helper.client_config,
                                          plugins=[
                                             # OpenAIAgentsPlugin(),
                                             ClaimCheckPlugin()
                                         ])
        return the_client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)

    @staticmethod
    @activity.defn
    async def get_current_client_info(workflow_id: str) -> WealthManagementClient:
        # get the handle from the workflow id
        activity.logger.info(f"Geting the current client info for {workflow_id}")
        handle = await OpenAccount.get_workflow_handle(workflow_id)
        client = await handle.execute_update("get_client_details")
        # client = await handle.execute_update(OpenInvestmentAccountWorkflow.get_client_details)
        return client

    @staticmethod
    @activity.defn
    async def approve_kyc(workflow_id: str):
        # get the handle from the workflow id
        handle = await OpenAccount.get_workflow_handle(workflow_id)
        await handle.signal(OpenInvestmentAccountWorkflow.verify_kyc)

    @staticmethod
    @activity.defn
    async def update_client_details(workflow_id: str, client_details: WealthManagementClient) -> str:
        # get the handle from the workflow id
        handle = await OpenAccount.get_workflow_handle(workflow_id)
        # convert the dataclass to a dict
        client_details_dict = asdict(client_details)
        result = await handle.execute_update(OpenInvestmentAccountWorkflow.update_client_details,
                                             args=[client_details_dict])
        return result
