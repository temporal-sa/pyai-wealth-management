package io.temporal.demo.wealthmanagement.workflow;

import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.demo.wealthmanagement.activities.UpdateParentWorkflow;
import io.temporal.demo.wealthmanagement.common.ServerInfo;
import io.temporal.demo.wealthmanagement.data.WealthManagementClient;
import io.temporal.demo.wealthmanagement.model.InvestmentAccount;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountOutput;
import io.temporal.failure.ActivityFailure;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountInput;
import io.temporal.workflow.ActivityStub;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@WorkflowImpl
public class OpenInvestmentAccountWorkflowImpl implements OpenInvestmentAccountWorkflow {
    // Welcome-bonus promotion: 1% of the initial deposit, up to $500.
    // Deliberate bug for the "Bug in workflow code" demo: the promo code is "WELCOME-BONUS",
    // so the lookup in welcomeBonus() returns null. The fix is the line below.
    // private static final Map<String, Double> PROMOTION_RATES = Map.of("WELCOME-BONUS", 0.01);
    private static final Map<String, Double> PROMOTION_RATES = Map.of("WELCOME_BONUS", 0.01);
    private static final double MAX_WELCOME_BONUS = 500.0;

    private WealthManagementClient client;
    private OpenInvestmentAccountInput inputs;
    private boolean initialized = false;
    private boolean kycVerified = false;
    private boolean complianceReviewed = false;
    private String currentState = "Initializing";

    // The Python activities, on the Python worker's task queue. Retries are unlimited with
    // a short maximum interval, and the timeout bounds a single attempt, so an outage is
    // retried for as long as it lasts (same as TOOL_RETRY_POLICY in retry_policies.py).
    private final ActivityStub activityStub = Workflow.newUntypedActivityStub(
            ActivityOptions.newBuilder().
                    setTaskQueue(ServerInfo.getTaskqueue()).
                    setStartToCloseTimeout(Duration.ofSeconds(30)).
                    setRetryOptions(RetryOptions.newBuilder().
                            setInitialInterval(Duration.ofSeconds(1)).
                            setBackoffCoefficient(2).
                            setMaximumInterval(Duration.ofSeconds(5)).build()).build());

    private final UpdateParentWorkflow updateParent = Workflow.newActivityStub(
            UpdateParentWorkflow.class,
            ActivityOptions.newBuilder().
                    setTaskQueue(ServerInfo.getTaskqueueOpenAccount()).
                    setStartToCloseTimeout(Duration.ofSeconds(5)).build());

    /** A completed saga step and the activity call that undoes it. */
    private record Compensation(String description, String activityName, Object[] args) {}

    @Override
    public OpenInvestmentAccountOutput run(OpenInvestmentAccountInput inputs) {
        Logger log = Workflow.getLogger(OpenInvestmentAccountWorkflowImpl.class);
        log.info("Started workflow {}", inputs);
        this.inputs = inputs;

        log.info("Retrieving current client information. Task queue is {}", ServerInfo.getTaskqueue());
        this.client = activityStub.execute("get_client", WealthManagementClient.class, inputs.getClient_id());

        log.info("Client {} details details {}", this.inputs.getClient_id(), this.client);
        this.initialized = true;
        setState("Waiting KYC");

        // Wait until the user has validated their information is correct
        log.info("Waiting for KYC Verification");
        Workflow.await( () -> this.kycVerified );

        setState("Waiting Compliance Review");

        log.info("Waiting for Compliance Review");
        Workflow.await( () -> this.complianceReviewed );

        setState("Compliance review has been approved. Creating Investment Account");

        return openAccount();
    }

    /**
     * Saga: create, fund, and register the account.
     *
     * Each completed step records how to undo it. If a later step fails with a non-retryable
     * error, the recorded compensations run in reverse order. Transient failures never get
     * here: activities retry until they succeed.
     */
    private OpenInvestmentAccountOutput openAccount() {
        double amount = inputs.getInitial_amount();
        double bonus = inputs.getPromo_code() != null ? welcomeBonus() : 0.0;
        // Idempotency key: stable across activity retries and worker restarts. It
        // includes the run ID because the workflow ID is reused when the customer
        // opens an account with the same name again (e.g. after a rollback).
        String transferId = Workflow.getInfo().getWorkflowId() + "-" + Workflow.getInfo().getRunId() + "-initial-deposit";
        List<Compensation> compensations = new ArrayList<>();
        try {
            setState("Creating the account record.");
            InvestmentAccount newAccount = new InvestmentAccount(inputs.getClient_id(),
                    inputs.getAccount_name(), amount + bonus);
            Map<?, ?> account = activityStub.execute("open_investment", HashMap.class, newAccount);
            String investmentId = (String) account.get("investment_id");
            compensations.add(new Compensation("removing the account record", "remove_investment",
                    new Object[] {inputs.getClient_id(), investmentId}));

            setState(String.format("Transferring $%,.2f from the linked bank account.", amount));
            activityStub.execute("debit_linked_account", HashMap.class, inputs.getClient_id(), amount, transferId);
            compensations.add(new Compensation(
                    String.format("refunding $%,.2f to the linked bank account", amount),
                    "credit_linked_account",
                    new Object[] {inputs.getClient_id(), amount, transferId + "-refund"}));

            setState("Registering the account with the custodian.");
            activityStub.execute("register_account", String.class, inputs.getClient_id(), investmentId, amount);
        } catch (ActivityFailure e) {
            String reason = e.getCause() instanceof ApplicationFailure applicationFailure
                    ? applicationFailure.getOriginalMessage()
                    : String.valueOf(e.getCause() != null ? e.getCause().getMessage() : e.getMessage());
            setState("Failed: " + reason + ". Rolling back.");
            for (int i = compensations.size() - 1; i >= 0; i--) {
                Compensation compensation = compensations.get(i);
                setState("Rolling back: " + compensation.description() + ".");
                activityStub.execute(compensation.activityName(), Object.class, compensation.args());
            }
            setState("Rolled back. The account was not opened: " + reason + ".");
            OpenInvestmentAccountOutput result = new OpenInvestmentAccountOutput();
            result.setAccount_created(false);
            result.setMessage("The account was not opened: " + reason);
            return result;
        }

        setState("Complete");
        OpenInvestmentAccountOutput result = new OpenInvestmentAccountOutput();
        result.setAccount_created(true);
        result.setMessage("investment account created");
        return result;
    }

    private double welcomeBonus() {
        double rate = PROMOTION_RATES.get(inputs.getPromo_code());
        return Math.min(inputs.getInitial_amount() * rate, MAX_WELCOME_BONUS);
    }

    @Override
    public String get_current_state() {
        return this.currentState;
    }

    @Override
    public WealthManagementClient get_client_details() {
        // wait until we're initialized
        Workflow.await(() -> this.initialized);
        return this.client;
    }

    @Override
    public String update_client_details(Map<String, Object> clientDetails) {
        Logger log = Workflow.getLogger(OpenInvestmentAccountWorkflowImpl.class);
        log.info("Updating client information for {}", this.inputs.getClient_id());
        String result = activityStub.execute("update_client", String.class,
                this.inputs.getClient_id(), clientDetails);
        this.kycVerified = true;
        return result;
    }

    @Override
    public void verify_kyc() {
        Workflow.await(() -> this.initialized);
        this.kycVerified = true;
    }

    @Override
    public void compliance_approved() {
        Workflow.await(() -> this.initialized && this.kycVerified);
        this.complianceReviewed = true;
    }

    private void setState(String state) {
        this.currentState = state;
        updateParentState(state);
    }

    private void updateParentState(String state) {
        Optional<String> parentWfId = Workflow.getInfo().getParentWorkflowId();
        if (parentWfId.isEmpty()) {
            return;
        }
        // use an activity because the parent is on a different Task Queue
        updateParent.changeState(parentWfId.get(), this.inputs.getAccount_name(), state,
                Workflow.getInfo().getWorkflowId());
    }
}
