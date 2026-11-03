# Wealth Management Multi-Agent Example using Pydantic AI Framework 

Demonstrates how to use the Pydantic AI Framework with Temporal. It shows how to coordinate between multiple agents that deal with specific domains of expertise.

The supervisor agent is responsible for directing the actions to the appropriate
helper agents.

Scenarios currently implmeneted include
* Add Beneficiary - add a new beneficiary to your account
* List Beneficiaries - shows a list of beneficiaries and their relationship to the account owner
* Delete Beneficiary - delete a beneficiary from your account
* Open Investment Account - opens a new investment account by starting a child workflow. The agent asks you to confirm or update your details (KYC), then the account waits for a compliance approval signal before it is created. The account opening workflow can run in Python (default) or Java
* List Investments - shows a list of accounts and their current balances
* Close Investment Account - closes an investment account

## Application Architecture
The overall application architecture looks like this:

![](../../images/application-architecture.png)

There is a React UX which is where the customer interacts with the application. 
The React UX leverages an API which exposes endpoints to start a workflow, send a prompt,
retrieving the chat history, and ending the chat. The React frontend uses adaptive polling
to retrieve new events from Redis. 

The API in turn, communicates with Temporal to start workflows and send signals. Finally, 
the worker contains the supervisor workflow which contain the agents and business logic 
that drive the agentic application.

## Prerequisites
* [uv](https://docs.astral.sh/uv/) - Python package and project manager
* [OpenAI API Key] (https://platform.openai.com/api-keys) - Your key to accessing OpenAI's LLM
* [Temporal CLI](https://docs.temporal.io/cli#install) - Local Temporal service
* [Redis](https://redis.io/downloads/) - Stores conversation history
* [Java](https://adoptium.net/) 17 or 21 (optional) - Only needed to run the Java Open Investment Account Workflow. The Gradle version used by the project does not support newer JDKs

# Set up Python Environment
```bash
uv sync
```

## Set up your OpenAI API Key
 
```bash
cp setoaikey.example setoaikey.sh
chmod +x setoaikey.sh
```

Now edit the setoaikey.sh file and paste in your OpenAI API Key.
It should look something like this:
```bash
export OPENAI_API_KEY=sk-proj-....
```
## Set up Redis

Redis is used for storing conversation history. If you don't have an existing Redis server, you can run one locally after installing it. 

In a new terminal / shell run the following command:

```bash
redis-server
```

By default, the application expects to find Redis running locally. You can override the location of Redis
by setting the environment variables:

```bash
export REDIS_HOST=localhost
export REDIS_PORT=6379
```

## Set up Claim Check (optional)

An optional configuration is to substitute the data sent to Temporal (e.g. function/method parameters and return values)
with an ID. This is known as the [Claim Check Pattern](https://www.enterpriseintegrationpatterns.com/patterns/messaging/StoreInLibrary.html). 
The original data is stored in Redis. This uses a 
[Custom Payload Codec](https://docs.temporal.io/develop/python/converters-and-encryption#custom-payload-codec) 
that intercepts data going to Temporal Cloud, replaces it with a GUID. When the data is retrieved, it looks up the GUID 
replaces it with the data stored in Redis.

```bash
cp setclaimcheck.example setclaimcheck.sh
chmod +x setclaimcheck.sh
```

Now edit the setclaimcheck.sh file and fill in the location of Redis
It should look something like this:
```bash
export USE_CLAIM_CHECK=true
export REDIS_HOST=localhost
export REDIS_PORT=6379
```

Save the file and be sure that you have Redis running. For example:

```bash
redis-server
```

Note that the application assumes you only have one Redis server that is used by the application for storing the 
conversation history and is also used if Claim Check has been enabled. 

Be aware that using a claim check pattern introduces performance costs by doing remote calls to Redis for every payload. 
Also, make sure your Redis implementation is rock solid as any downtime will directly affect your workflows. 

## Run Codec Server for Claim Check (optional)
If you have decided to set up the Claim Check above, you will 
most likely want to also run a Codec Server to be able to see the actual sent to 
the workflow and activities. 

Be sure you have updated the setclaimcheck.sh as mentioned above.

To run the Codec Server:
```bash
./startcodecserver.sh
```

And of course, make sure that your Redis Server is up and running. 

Once it's up and running, the default endpoint is http://127.0.0.1:8081.

Open up your browser and navigate to the Temporal UI. Locally that is usually localhost:8233. 
For Temporal Cloud that will be https://cloud.temporal.io

In the upper right, there is an icon that looks like glasses. Click on that and you 
can configure your codec server. 

Change the drop down to "Use my browser setting and ignore Cluster-level setting".

Use the endpoint of your codec server (default is http://127.0.0.1:8081). Be sure there are no 
extra spaces at the end of the URL or it will not work. You can turn off the two options - Pass the 
user access token and Include cross-origin credentials. Click on apply and you have configured 
the codec server. 

![](../../images/config-codec-server.png)

As you run through different scenarios, you'll be able to see the decoded values in the browser.

![](../../images/decoded-input.png)

If you remove the codec server settings, you won't be able to see anything other than a BASE64 Encoded ID.

![](../../images/encoded-input.png)

## Running the Demo Locally
Start Temporal Locally.

```bash
temporal server start-dev
```

In another terminal, start the worker.

### Start the Worker using the Python Open Investment Account Workflow (default)

```bash
cd src/temporal_supervisor
./startlocalworker.sh
```

If you want to use the Java Open Investment Account Workflow,
you need to specify the name of the Task Queue when running the Python worker:

```bash
cd src/temporal_supervisor
./startlocalworker.sh Open-Account-Java
```

and then start the Java worker, making sure you pass in the same name as you did 
when you started the Python worker:

```bash
cd java
./startlocalworker.sh Open-Account-Java
```

### Start the API

In another terminal, start the REST API.

```bash
cd src/api
./startlocalapi.sh
```

And in another terminal, start the UX. 

### Start the UX
```bash
cd src/frontend
npm install
npm start
```

A new browser window opens where you can interact with the application. 

If you are opening a new investment account, in another terminal
### Send the Compliance Reviewed Signal 
```bash
cd src/temporal_supervisor
./localsendcomplianceapproval.sh "<Child Workflow ID>"
```

The child workflow ID is shown at the end of the agent's reply when it asks you to confirm your information, for example `(Workflow ID: OpenAccount-pyai-temporal-agent-abc123-123-Vacation)`. Quote it if the account name contains a space.

Here is a screenshot of the UX walking through a few scenarios:

![](../../images/ux-chat-session.png)

Here is a sample event history shown in the Temporal UX

![](../../images/temporal-ux-session.png)

## Failure scenarios (chaos panel)

Click **Chaos** in the chat header to open the chaos panel. Its switches inject
failures at runtime: activities, agent tools and the model read the flags from
Redis on every attempt, so you can turn a failure on and off mid-conversation
without restarting anything. The panel also lists each injected failure as it
happens (activity and attempt number). Open the [Temporal UI](http://localhost:8233)
alongside it to show the pending activity, its attempt count, and the last
failure. **Reset all** turns every flag off.

| Scenario | How to show it | What Temporal does |
|---|---|---|
| **Tool / API down** | Turn on *Core banking API down*, then ask to list your investments or beneficiaries (or open an account, so it fails after compliance approval). Turn it off. | The tool activity retries (1s backoff, capped at 5s) for as long as the outage lasts. The agent waits mid-turn and answers once the API is back. Nothing is lost or re-asked. |
| **LLM down** | Turn on *OpenAI API down* and send a message. Turn it off. | The model call is an activity, so it retries the same way (capped at 10s). The conversation picks up exactly where it was. |
| **LLM rate limited (reactive)** | Turn on *OpenAI rate limited (429)* and send a message. | The model raises an `ApplicationError` with `next_retry_delay` (15s, like a `Retry-After` header), which overrides the retry policy's backoff. Real OpenAI 429s are handled the same way (`ChaosAwareModel` in `model_config.py`). |
| **LLM rate limited (proactive)** | Start the worker with `LLM_MAX_CALLS_PER_SECOND=0.2`, then chat in several browser tabs at once. | Model calls run on their own task queue (`PY-AI-Supervisor-llm`). The Temporal service dispatches at most that many per second across *all* workers, so calls queue up instead of hitting the provider's limit. Tools on the main queue aren't slowed down. |
| **Business rule (non-retryable)** | Ask to open an account with $2,000,000 (or less than $100). | `validate_new_account` raises a non-retryable `ApplicationError`: one attempt, no retries. The reason goes back to the customer before any account opening starts. Compare it with *Tool / API down* in the Temporal UI. |
| **Saga** | Turn on *Custodian rejects new accounts* (and *Slow account-opening steps* so you can watch), then open an account through compliance approval. Without flags: as client `123`, open an account with $300,000 (the linked bank account has $250,000). | After compliance approval the child workflow creates the account record, transfers the deposit from the linked bank account, and registers with the custodian. When a step fails non-retryably, the completed steps are undone in reverse order (refund, then remove the account). Compensations are activities too, so they are retried until they succeed. Kill the worker mid-rollback and the rollback still finishes. Transfers are idempotent, so a retried debit or refund never moves money twice. |
| **Bug in workflow code** | Turn on *Bug in workflow code*, then open an account through compliance approval. See below. | The workflow pauses at the failing line, keeping all its state, until a worker with fixed code picks it up. |

Every scenario works with both the Python and the [Java](../../java/README.md)
account opening workflow. The failures are injected in the Python activities,
which the Java workflow calls too, and both workflows implement the same saga and
the same kind of bug.

The linked bank balances and transfers are kept in `data/funding_accounts.json`.
To reset the demo data after a few runs:

```bash
git checkout data/
```

### Bug in the workflow code

Turn on **Bug in workflow code**, then open an account and approve KYC and
compliance. While the switch is on, `find_promotion` gives new account openings a
`WELCOME-BONUS` promo code, which is passed to `OpenInvestmentAccountWorkflow` as
part of its input. The welcome-bonus code path has a real bug, and openings
without the promo code never reach it:

* **Python** (`_welcome_bonus` in `workflows/open_account_workflow.py`) reads
  `inputs.initial_deposit`, a field that doesn't exist, and raises
  `AttributeError: 'OpenInvestmentAccountInput' object has no attribute 'initial_deposit'`.
* **Java** (`OpenInvestmentAccountWorkflowImpl.java`) looks up the promotion rate
  under the wrong key (`WELCOME_BONUS` instead of `WELCOME-BONUS`), and unboxing
  the missing rate raises a `NullPointerException`.

In both SDKs an ordinary exception fails the *workflow task*, not the workflow, so
the workflow stays **Running**, paused right after compliance approval with the
approval intact. The Temporal UI shows the failing workflow task and its stack
trace. Other conversations, and openings without the promo code, keep working.

Turning the switch off doesn't help: the promo code is part of the workflow's
input, so every retry hits the same line. The fix is code:

* Python: change `inputs.initial_deposit` to `inputs.initial_amount`, the line
  commented out above it, and restart the Python worker.
* Java: swap `PROMOTION_RATES` for the commented-out line above it, and restart
  the Java worker.

The workflow resumes on the server's next workflow-task retry and opens the
account with the bonus. Workflow-task retries back off over time, up to 10
minutes. If the fix is deployed within a couple of minutes, the workflow resumes
within about a minute.

After the demo, re-arm the bug:

```bash
git checkout src/temporal_supervisor/workflows/open_account_workflow.py
git checkout java/src/main/java/io/temporal/demo/wealthmanagement/workflow/OpenInvestmentAccountWorkflowImpl.java
```

### Running the tests

The failure scenarios have tests that run against a local Temporal test server
(downloaded automatically on first run) with stub activities. They need no
OpenAI API key or Redis.

```bash
uv run pytest
```

And for the Java workflow (Java 17 or 21):

```bash
cd java
./gradlew test
```

### Running from the command line

Alternatively you can interact with workflow using the temporal CLI 

Start the worker in a new terminal:

```bash
./startlocalworker.sh
```

And in another terminal you can run the following commands:

```bash
# starts a workflow execution
temporal workflow start --workflow-id ai-chat --type WealthManagementWorkflow --task-queue PY-AI-Supervisor 

# send it a request using a signal
temporal workflow signal --workflow-id ai-chat --name process_user_message --input '{"user_input":"Who are my beneficiaries?"}'

# retrieve the chat history directly from the workflow execution
temporal workflow query --workflow-id ai-chat --type get_chat_history

# send it the client id via a signal
temporal workflow signal --workflow-id ai-chat --name process_user_message --input '{"user_input":"123"}'

# ask about investments via a signal 
temporal workflow signal --workflow-id ai-chat --name process_user_message --input '{"user_input":"what investments do I have?"}'

# ends the workflow execution
temporal workflow signal --workflow-id ai-chat --name end_workflow
```

## Running the Demo in Temporal Cloud

Copy the setcloudenv.example, located in the src/temporal_supervisor folder to the project root and name it setcloudenv.sh .

```bash
cp  src/temporal_supervisor/setcloudenv.example setcloudenv.sh
```

Edit setcloudenv.sh to match your Temporal Cloud account:
```bash
export TEMPORAL_ADDRESS=<namespace>.<accountID>.tmprl.cloud:7233
export TEMPORAL_NAMESPACE=<namespace>.<accountID>
export TEMPORAL_TLS_CLIENT_CERT_PATH="/path/to/cert.pem"
export TEMPORAL_TLS_CLIENT_KEY_PATH="/path/to/key.key"
```

### Start the Worker

In a terminal, start the worker, using Temporal Cloud:

```bash
cd src/temporal_supervisor
./startcloudworker.sh
```

If you want to use the Java Open Investment Account Workflow,
you need to specify the name of the Task Queue when running the Python worker:

```bash
cd src/temporal_supervisor
./startcloudworker.sh Open-Account-Java
```

and then start the Java worker, making sure you pass in the same name as you did 
when you started the Python worker:

```bash
cd java
./startcloudworker.sh Open-Account-Java
```

### Start the API

In another terminal, start the API using Temporal Cloud

```bash
cd src/api
./startcloudapi.sh
```

And in another terminal, start the UX:

### Start the UX
```bash
cd src/frontend
npm install
npm start
```

A new browser window opens where you can interact with the application. 

If you are opening a new investment account, in another terminal
### Send the Compliance Reviewed Signal 
```bash
cd src/temporal_supervisor
./cloudsendcomplianceapproval.sh "<Child Workflow ID>"
```

The child workflow ID is shown at the end of the agent's reply when it asks you to confirm your information, for example `(Workflow ID: OpenAccount-pyai-temporal-agent-abc123-123-Vacation)`. Quote it if the account name contains a space.
