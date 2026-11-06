# Wealth Management API

A [FastAPI](https://fastapi.tiangolo.com/) application that the [React web app](../frontend/README.md)
uses to talk to Temporal. It starts the chat workflow (`WealthManagementWorkflow`), sends it the
customer's messages as signals, reads the conversation from Redis, and turns the chaos panel's
failure-injection flags on and off.

## Prerequisites

* The Python environment, `setoaikey.sh` and Redis set up as described in the
  [Temporal version README](../temporal_supervisor/README.md)
* A Temporal service (`temporal server start-dev` locally, or Temporal Cloud)
* Redis running (`redis-server`)
* The worker running (`cd src/temporal_supervisor && ./startlocalworker.sh`), otherwise chats are
  started but never answered

## Running the API

Locally:

```bash
cd src/api
./startlocalapi.sh
```

Against Temporal Cloud, after creating `setcloudenv.sh` in the repository root (see
[Running the Demo in Temporal Cloud](../temporal_supervisor/README.md#running-the-demo-in-temporal-cloud)):

```bash
cd src/api
./startcloudapi.sh
```

Both scripts source `../../setclaimcheck.sh` (if it exists) and `../../setoaikey.sh` (and `startcloudapi.sh` also
`../../setcloudenv.sh`), then run `uv run uvicorn api.main:app --reload` with `src` on the `PYTHONPATH`.
Claim check is optional: without `setclaimcheck.sh`, the scripts run without it.

The API listens on http://127.0.0.1:8000 (the uvicorn default). It accepts browser requests from
`http://localhost:3000` and `http://127.0.0.1:3000` (CORS), where the web app runs. Interactive docs are
at http://127.0.0.1:8000/docs.

## Endpoints

All parameters are query parameters.

| Method | Path | Parameters | Description |
|---|---|---|---|
| GET | `/` | | Health check message. |
| POST | `/start-workflow` | `workflow_id` | Starts a `WealthManagementWorkflow` with that ID on the `TEMPORAL_TASK_QUEUE` task queue. |
| POST | `/send-prompt` | `workflow_id`, `prompt` | Sends the customer's message to the workflow with the `process_user_message` signal. |
| GET | `/get-chat-history` | `workflow_id`, `from_index` (default `0`) | Returns the conversation events (chat messages and account opening status updates) stored in Redis, starting at `from_index`. |
| POST | `/end-chat` | `workflow_id` | Sends the `end_workflow` signal. |
| GET | `/chaos` | | Returns the chaos flags (name, label, description, on/off) and the failures they recently caused. |
| POST | `/chaos` | `flag`, `enabled` | Turns a chaos flag on or off. `flag` is one of `tool_down`, `llm_down`, `llm_rate_limited`, `custodian_rejects`, `slow_saga`, `workflow_bug`. |
| POST | `/chaos/reset` | | Turns every chaos flag off and clears the failure log. |

For example:

```bash
curl -X POST "http://127.0.0.1:8000/start-workflow?workflow_id=my-chat"
curl -X POST "http://127.0.0.1:8000/send-prompt?workflow_id=my-chat&prompt=Who%20are%20my%20beneficiaries%3F"
curl "http://127.0.0.1:8000/get-chat-history?workflow_id=my-chat&from_index=0"
curl -X POST "http://127.0.0.1:8000/end-chat?workflow_id=my-chat"
```

## Configuration

The API reads the same environment variables as the worker: `TEMPORAL_TASK_QUEUE` (default
`PY-AI-Supervisor`, must match the worker), `REDIS_HOST` / `REDIS_PORT` (default `localhost` / `6379`),
`USE_CLAIM_CHECK`, and the Temporal connection variables (`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`,
`TEMPORAL_TLS_CLIENT_CERT_PATH`, `TEMPORAL_TLS_CLIENT_KEY_PATH`). See the
[Temporal version README](../temporal_supervisor/README.md#environment-variables).
