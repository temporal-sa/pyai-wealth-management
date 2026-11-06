# Java Open Investment Account Workflow

An optional Java implementation of the `OpenInvestmentAccountWorkflow` child workflow. When it is used,
the Python worker runs the agents and the supervisor workflow, and this Java worker runs the account
opening workflow on its own task queue.

The Java workflow calls the Python activities by name on the Python worker's task queue (`get_client`,
`update_client`, and for the account opening saga `open_investment`, `debit_linked_account`,
`register_account`, and the compensations `credit_linked_account` and `remove_investment`), so the Python
worker must be running too. It reports status changes to the parent chat workflow with its own
`UpdateParentWorkflow` activity, which runs on the Java worker.

## Prerequisites
* [Temporal CLI](https://docs.temporal.io/cli#install)
* Java JDK 25. The Gradle wrapper (`./gradlew`, Gradle 9.8.2) is included, so you don't need to install
  Gradle. The project uses Spring Boot 3.5.16.
* The Python worker, API and UX set up as described in the
  [Temporal version README](../src/temporal_supervisor/README.md)

## Build

From the repository root:

```shell
cd java
./gradlew clean build
```

This builds `build/libs/oai-wealth-management-java.jar` (the project name in `settings.gradle`) and runs the tests.

## Running Locally
Start Temporal Server locally:

```shell
temporal server start-dev
```

From the repository root, start the Python worker with a task queue name for the account opening workflow:

```shell
cd src/temporal_supervisor
./startlocalworker.sh Open-Account-Java
```

Then, in another terminal from the repository root, start the Java worker with the same task queue name:

```shell
cd java
./startlocalworker.sh Open-Account-Java
```

The task queue name is passed in as `TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT`. The Python worker's task queue
defaults to `PY-AI-Supervisor`; if you changed it with `TEMPORAL_TASK_QUEUE`, set the same value before
starting the Java worker. `startlocalworker.sh` sources `../setclaimcheck.sh` if it exists (claim check is optional).

Start the API and the UX, and open an investment account from the chat. The child workflow will run on
the Java worker. Send the compliance approval as usual (see
[Finding the child workflow ID](../src/temporal_supervisor/README.md#finding-the-child-workflow-id)):

```shell
cd src/temporal_supervisor
./localsendcomplianceapproval.sh "<Child Workflow ID>"
```

To try the Java workflow without the chat or an OpenAI key, run the open-account scenarios against it
(with this worker running, and no other Python worker): `src/temporal_supervisor/startlocalopenaccount.sh Open-Account-Java`.
See the account opening section of the [Temporal README](../src/temporal_supervisor/README.md).

## Failure scenarios and tests

The chaos panel scenarios all work with the Java workflow. The Java workflow implements the same
account opening saga as the Python one, and has its own version of the "Bug in workflow code" bug in
`OpenInvestmentAccountWorkflowImpl.java`: `PROMOTION_RATES` has the key `WELCOME_BONUS` instead of
`WELCOME-BONUS`, so the rate lookup returns null and unboxing it throws a `NullPointerException`, which
fails the workflow task and pauses the workflow. The fix is to swap `PROMOTION_RATES` for the commented-out
line above it and restart the Java worker. See
[Bug in the workflow code](../src/temporal_supervisor/README.md#bug-in-the-workflow-code). To re-arm the bug,
from the repository root:

```shell
git checkout java/src/main/java/io/temporal/demo/wealthmanagement/workflow/OpenInvestmentAccountWorkflowImpl.java
```

The workflow's tests run against Temporal's in-memory test environment, with stubs for the Python
activities:

```shell
cd java
./gradlew test
```

## Running Using Temporal Cloud

Create `setcloudenv.sh` in the repository root (copy `src/temporal_supervisor/setcloudenv.example`)
as described in the [Temporal README](../src/temporal_supervisor/README.md#running-the-demo-in-temporal-cloud).
Start the Python worker with the queue name, then the Java worker with the same name:

```bash
cd src/temporal_supervisor
./startcloudworker.sh Open-Account-Java
```

```bash
cd java
./startcloudworker.sh Open-Account-Java
```

The Java cloud script can be run from any directory. It sources `../setclaimcheck.sh` (optional, skipped
if missing) and `../setcloudenv.sh`, maps `TEMPORAL_TLS_CLIENT_CERT_PATH` to the
`TEMPORAL_CERT_PATH` the Java worker reads, uses the Spring profile `tc`
(`src/main/resources/application-tc.yaml`), and runs `./gradlew runWorker`. With this profile, the
claim check Redis comes from `REDIS_HOST` / `REDIS_PORT` (default `localhost:6379`).

Java's TLS needs the client key as an unencrypted PKCS8 key (`-----BEGIN PRIVATE KEY-----`). If
`TEMPORAL_TLS_CLIENT_KEY_PATH` points to a different format, the script stops and prints the command
to convert it, for example:

```bash
openssl pkcs8 -topk8 -nocrypt -in /path/to/key.key -out /path/to/key.pkcs8.key
```

Then add the converted key to `setcloudenv.sh` (the Python components keep using
`TEMPORAL_TLS_CLIENT_KEY_PATH`):

```bash
export TEMPORAL_KEY_PKCS8_PATH="/path/to/key.pkcs8.key"
```

