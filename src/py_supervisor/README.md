# Wealth Management Multi-Agent example using Pydantic AI Framework

Demonstrates how to use Pydantic AI Framework with multiple agents. This version runs in your terminal,
without Temporal, Redis or a web application.

The Temporal version of this example is located [here](../temporal_supervisor/README.md)

Scenarios currently implemented include
* Add Beneficiary - add a new beneficiary to your account
* List Beneficiaries - shows a list of beneficiaries and their relationship to the account owner
* Delete Beneficiary - delete a beneficiary from your account. The agent asks you to confirm first
* Open Investment Account - opens a new investment account with the name and initial amount you give.
  In this version the account is added directly: there is no KYC, compliance approval or saga (see the
  Temporal version for those)
* List Investments - shows a list of accounts and their current balances
* Close Investment Account - closes an investment account. The agent asks you to confirm first
* Off-topic questions - questions that aren't about beneficiaries or investment accounts are politely
  declined by the supervisor agent (handled in the agents' prompts)

## Prerequisites
* [uv](https://docs.astral.sh/uv/) - Python package and project manager
* Python 3.14 or later (`uv sync` can install it for you)
* [OpenAI API Key](https://platform.openai.com/api-keys) - Your key to accessing OpenAI's LLM

## Set up Python Environment
From the repository root:

```bash
uv sync
```

## Set up your OpenAI API Key
From the repository root:

```bash
cp setoaikey.example setoaikey.sh
chmod +x setoaikey.sh
```

Now edit the setoaikey.sh file and paste in your OpenAI API Key.
It should look something like this:
```text
export OPENAI_API_KEY=sk-proj-....
```

## Running the agent
```bash
cd src/py_supervisor
./startchat.sh
```

`startchat.sh` sources `../../setoaikey.sh` and runs `python -m py_supervisor.main` with `src` on the
`PYTHONPATH`. Run it from `src/py_supervisor`: the data files are found relative to that folder.

Type `end`, `exit` or `quit` to stop.

Example Output
```
Welcome to ABC Wealth Management. How can I help you?

[Supervisor Agent] Enter your message: Who are my beneficiaries?
To help you with your beneficiaries, could you please provide your client ID?

[Supervisor Agent] Enter your message: 123
Here are your current beneficiaries:

1. John Doe (son)
2. Jane Doe (daughter)
3. Joan Doe (spouse)

Would you like to add, delete or list your beneficiaries?

[Beneficiary Agent] Enter your message: What investments do I have?
Here are your current investment accounts:

1. Checking: $1,000.00
2. Savings: $2,312.08
3. 401K: $11,070.89

Would you like to open, close or list your investment accounts?

[Investment Agent] Enter your message: Open a Vacation account with $500
Your Vacation account has been opened with $500.00.

[Investment Agent] Enter your message: end
Agent loop complete.
```

Opening and closing accounts and changing beneficiaries update the JSON files in `data/`. To reset them,
run `git checkout data/` from the repository root.
