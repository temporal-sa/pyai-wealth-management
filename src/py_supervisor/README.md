# Wealth Management Multi-Agent example using Pydantic AI Framework

Demonstrates how to use Pydantic AI Framework with multiple agents

The Temporal version of this example is located [here](src/temporal_supervisor/README.md)

Scenarios current implemented include
* Add Beneficiary - add a new beneficiary to your account
* List Beneficiaries - shows a list of beneficiaries and their relationship to the account owner
* Delete Beneficiary - delete a beneficiary from your account
* Open Investment Account - opens a new investment account
* List Investments - shows a list of accounts and their current balances
* Close Investment Account - closes an investment account

## Prerequisites
* [uv](https://docs.astral.sh/uv/) - Python package and project manager

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

## Running the agent
```bash
cd src/py_supervisor
source ../../setoaikey.sh
uv run python -m py_supervisor.main
```

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

Would you like to add, remove or list your beneficiaries?

[Beneficiary Agent] Enter your message: What investments do I have?
Here are your current investment accounts:

1. Checking: $1,000.00
2. Savings: $2,312.08
3. 401K: $11,070.89

Would you like to open, close or list your investment accounts?

[Investment Agent] Enter your message: end
Agent loop complete.
```