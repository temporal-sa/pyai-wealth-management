# Wealth Management Agent Example using Pydantic AI Framework

This project demonstrates how to use [Pydantic AI Framework](https://ai.pydantic.dev/) 
with multiple agents working together. It leverages the supervisor pattern to "hand off" work to agents that have specific knowledge of a certain domain. 

You will find a version of just using the Pydantic AI Framework and another version
that leverages [Temporal](https://temporal.io) to wrap the agentic flow with Temporal.

TODO: include an architecture image

The vanilla Pydantic AI framework version of this example is located [here](src/py_supervisor/README.md).

The Temporal version of this example is located [here](src/temporal_supervisor/README.md)

Scenarios currently implmeneted include:
* Add Beneficiary - add a new beneficiary to your account
* List Beneficiaries - shows a list of beneficiaries and their relationship to the account owner
* Delete Beneficiary - delete a beneficiary from your account
* Open Investment Account - opens a new investment account 
* List Investments - shows a list of accounts and their current balances
* Close Investment Account - closes an investment account

You can run through the scenarios with the Temporal version using a [Web Application](src/frontend/README.md) 

## Prerequisities
* [uv](https://docs.astral.sh/uv/) - Python package and project manager
* [OpenAI API Key] (https://platform.openai.com/api-keys) - Your key to accessing OpenAI's LLM
* [Temporal CLI](https://docs.temporal.io/cli#install) - Local Temporal service
* [Redis](https://redis.io/downloads/) - Stores conversation history

## Set up Python Environment
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
```text
export OPENAI_API_KEY=sk-proj-....
```

## Getting Started

See the Pydantic AI Framework version [here](src/py_supervisor/README.md)
And the Temporal version of this example [here](src/temporal_supervisor/README.md)


