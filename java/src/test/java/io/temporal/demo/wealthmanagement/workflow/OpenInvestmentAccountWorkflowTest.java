package io.temporal.demo.wealthmanagement.workflow;

import io.temporal.activity.Activity;
import io.temporal.activity.DynamicActivity;
import io.temporal.api.enums.v1.EventType;
import io.temporal.api.enums.v1.WorkflowExecutionStatus;
import io.temporal.api.history.v1.HistoryEvent;
import io.temporal.api.workflowservice.v1.DescribeWorkflowExecutionRequest;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.client.WorkflowStub;
import io.temporal.common.converter.EncodedValues;
import io.temporal.demo.wealthmanagement.activities.UpdateParentWorkflow;
import io.temporal.demo.wealthmanagement.common.ServerInfo;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountInput;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountOutput;
import io.temporal.failure.ApplicationFailure;
import io.temporal.testing.TestWorkflowEnvironment;
import io.temporal.worker.Worker;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Runs the Java OpenInvestmentAccountWorkflow against stubs of the Python activities it calls
 * by name, mirroring tests/test_failure_scenarios.py.
 */
class OpenInvestmentAccountWorkflowTest {
    private static final String CLIENT_ID = "C-1";
    private static final String INVESTMENT_ID = "i-test";

    private final List<String> calls = Collections.synchronizedList(new ArrayList<>());
    // Activity name -> "reject" (non-retryable failure) or a number of transient failures.
    private final Map<String, Object> failures = new HashMap<>();

    private TestWorkflowEnvironment env;
    private WorkflowClient client;

    /** Stands in for the Python activities, which the workflow calls through an untyped stub. */
    private class StubPythonActivities implements DynamicActivity {
        @Override
        public Object execute(EncodedValues args) {
            String name = Activity.getExecutionContext().getInfo().getActivityType();
            int attempt = Activity.getExecutionContext().getInfo().getAttempt();
            switch (name) {
                case "get_client":
                    calls.add(name);
                    return Map.of("client_id", CLIENT_ID, "first_name", "Sam", "last_name", "Doe");
                case "open_investment":
                    calls.add(name + "#" + attempt);
                    maybeFail(name, attempt);
                    return Map.of("investment_id", INVESTMENT_ID, "name", "Test", "balance", 500.0);
                case "debit_linked_account":
                    calls.add("debit:" + args.get(1, Double.class));
                    maybeFail(name, attempt);
                    return Map.of();
                case "credit_linked_account":
                    calls.add("credit:" + args.get(1, Double.class));
                    assertTrue(args.get(2, String.class).endsWith("-refund"));
                    return Map.of();
                case "register_account":
                    calls.add("register");
                    maybeFail(name, attempt);
                    return "CUST-1";
                case "remove_investment":
                    calls.add("remove_investment:" + args.get(1, String.class));
                    return true;
                default:
                    throw new IllegalArgumentException("Unexpected activity " + name);
            }
        }

        private void maybeFail(String name, int attempt) {
            Object behavior = failures.get(name);
            if ("reject".equals(behavior)) {
                throw ApplicationFailure.newNonRetryableFailure(name + " rejected", "Rejected");
            }
            if (behavior instanceof Integer transientFailures && attempt <= transientFailures) {
                throw ApplicationFailure.newFailure(name + " unavailable", "ConnectionError");
            }
        }
    }

    /** The workflow has no parent in these tests, so there is nothing to signal. */
    private static class NoParent implements UpdateParentWorkflow {
        @Override
        public void changeState(String parentWorkflowId, String accountName, String state) {
        }
    }

    @BeforeEach
    void setUp() {
        env = TestWorkflowEnvironment.newInstance();
        // The workflow and the Python activities share a task queue unless the env vars say otherwise.
        Worker worker = env.newWorker(ServerInfo.getTaskqueue());
        worker.registerWorkflowImplementationTypes(OpenInvestmentAccountWorkflowImpl.class);
        worker.registerActivitiesImplementations(new StubPythonActivities(), new NoParent());
        env.start();
        client = env.getWorkflowClient();
    }

    @AfterEach
    void tearDown() {
        env.close();
    }

    private OpenInvestmentAccountWorkflow startAndApprove(String promoCode) {
        OpenInvestmentAccountWorkflow workflow = client.newWorkflowStub(
                OpenInvestmentAccountWorkflow.class,
                WorkflowOptions.newBuilder()
                        .setWorkflowId("open-account-test-" + UUID.randomUUID())
                        .setTaskQueue(ServerInfo.getTaskqueue())
                        .build());
        OpenInvestmentAccountInput input = new OpenInvestmentAccountInput();
        input.setClient_id(CLIENT_ID);
        input.setAccount_name("Test");
        input.setInitial_amount(500.0f);
        input.setPromo_code(promoCode);
        WorkflowClient.start(workflow::run, input);
        workflow.verify_kyc();
        workflow.compliance_approved();
        return workflow;
    }

    private OpenInvestmentAccountOutput openAccount() {
        OpenInvestmentAccountWorkflow workflow = startAndApprove(null);
        return WorkflowStub.fromTyped(workflow).getResult(OpenInvestmentAccountOutput.class);
    }

    @Test
    void sagaHappyPath() {
        OpenInvestmentAccountOutput result = openAccount();
        assertTrue(result.isAccount_created());
        assertEquals(List.of("get_client", "open_investment#1", "debit:500.0", "register"), calls);
    }

    @Test
    void sagaRollsBackInReverseWhenCustodianRejects() {
        failures.put("register_account", "reject");
        OpenInvestmentAccountOutput result = openAccount();
        assertFalse(result.isAccount_created());
        assertTrue(result.getMessage().contains("register_account rejected"), result.getMessage());
        assertEquals(List.of("get_client", "open_investment#1", "debit:500.0", "register",
                "credit:500.0", "remove_investment:" + INVESTMENT_ID), calls);
    }

    @Test
    void sagaUndoesOnlyCompletedSteps() {
        failures.put("debit_linked_account", "reject");
        OpenInvestmentAccountOutput result = openAccount();
        assertFalse(result.isAccount_created());
        // The debit never happened, so there is nothing to refund.
        assertEquals(List.of("get_client", "open_investment#1", "debit:500.0",
                "remove_investment:" + INVESTMENT_ID), calls);
    }

    @Test
    void transientFailuresAreRetriedNotRolledBack() {
        failures.put("open_investment", 2);
        OpenInvestmentAccountOutput result = openAccount();
        assertTrue(result.isAccount_created());
        assertEquals(List.of("get_client", "open_investment#1", "open_investment#2", "open_investment#3",
                "debit:500.0", "register"), calls);
    }

    @Test
    void promoCodePathBugPausesWorkflowInsteadOfFailingIt() throws InterruptedException {
        OpenInvestmentAccountWorkflow workflow = startAndApprove("WELCOME-BONUS");
        String workflowId = WorkflowStub.fromTyped(workflow).getExecution().getWorkflowId();

        String failure = null;
        for (int i = 0; i < 40 && failure == null; i++) {
            for (HistoryEvent event : client.fetchHistory(workflowId).getEvents()) {
                if (event.getEventType() == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED) {
                    // The NullPointerException is in the cause, under a generic "Failure handling event" message.
                    failure = event.getWorkflowTaskFailedEventAttributes().getFailure().toString();
                }
            }
            Thread.sleep(250);
        }

        assertNotNull(failure, "the demo bug should fail the workflow task");
        assertTrue(failure.contains("NullPointerException"), failure);
        // Paused, not failed: still running, and the saga never started.
        WorkflowExecutionStatus status = env.getWorkflowServiceStubs().blockingStub()
                .describeWorkflowExecution(DescribeWorkflowExecutionRequest.newBuilder()
                        .setNamespace(env.getNamespace())
                        .setExecution(WorkflowStub.fromTyped(workflow).getExecution())
                        .build())
                .getWorkflowExecutionInfo().getStatus();
        assertEquals(WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING, status);
        assertFalse(calls.contains("open_investment#1"));
        WorkflowStub.fromTyped(workflow).terminate("test finished");
    }
}
