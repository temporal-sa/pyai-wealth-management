# Java Open Investment Account Workflow

An optional Java implementation of the `OpenInvestmentAccountWorkflow` child workflow. When it is used,
the Python worker runs the agents and the supervisor workflow, and this Java worker runs the account
opening workflow on its own task queue.

The Java workflow calls the Python activities (`get_client`, `update_client` and `open_investment`) on the
Python worker's task queue, and signals status changes back to the parent workflow, so the Python worker
must be running too.

## Prerequisites
* [Temporal CLI](https://docs.temporal.io/cli#install)
* Java 17 or 21. The project uses Gradle 8.5, which does not support newer JDKs. If your default JDK is
  newer, point `JAVA_HOME` at a 17 or 21 install before running `./gradlew`.
* The Python worker, API and UX set up as described in the
  [Temporal version README](../src/temporal_supervisor/README.md)
* Optionally a [Temporal Cloud namespace](https://pages.temporal.io/get-cloud)

## Build

```shell
./gradlew clean build
```

## Running Locally
Start Temporal Server locally:

```shell
temporal server start-dev
```

Start the Python worker with a task queue name for the account opening workflow:

```shell
cd src/temporal_supervisor
./startlocalworker.sh Open-Account-Java
```

Then start the Java worker from this folder with the same task queue name:

```shell
cd java
./startlocalworker.sh Open-Account-Java
```

The task queue name is passed in as `TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT`. The Python worker's task queue
defaults to `PY-AI-Supervisor`; if you changed it with `TEMPORAL_TASK_QUEUE`, set the same value before
starting the Java worker.

Start the API and the UX, and open an investment account from the chat. The child workflow will run on
the Java worker. Send the compliance approval from `src/temporal_supervisor` as usual:

```shell
./localsendcomplianceapproval.sh "<Child Workflow ID>"
```

## Running Using Temporal Cloud

The Java worker reads its connection settings from these environment variables. They have different names
from the ones the Python worker uses in `setcloudenv.sh`:

```shell
export TEMPORAL_ADDRESS=<namespace>.<accountId>.tmprl.cloud:7233
export TEMPORAL_NAMESPACE=<namespace>.<accountId>
export TEMPORAL_CERT_PATH="/path/to/your/cert.pem"
# the key must be in PKCS8 format
export TEMPORAL_KEY_PATH="/path/to/your/key.pkcs8"
# if set, TEMPORAL_KEY_PKCS8_PATH is used instead of TEMPORAL_KEY_PATH, so you can keep
# TEMPORAL_KEY_PATH pointing at a PKCS1 key for other tools
# if claim check is enabled for the Python worker:
# export USE_CLAIM_CHECK=true
```

Start the Python worker with `./startcloudworker.sh Open-Account-Java`, then start the Java worker:

```shell
cd java
./startcloudworker.sh Open-Account-Java
```

## Run the Worker using Docker

Build the docker container:

```shell
./gradlew clean build
./buildcontainer.sh
```

`runlocal.sh` runs the container against a Temporal Server on the host. `runontc.sh` runs it against
Temporal Cloud and reads `TEMPORAL_NAMESPACE`, `TEMPORAL_ENDPOINT`, `TEMPORAL_CLIENT_CERT` and
`TEMPORAL_CLIENT_KEY` (and optionally `TEMPORAL_INSECURE_TRUST_MANAGER`).
