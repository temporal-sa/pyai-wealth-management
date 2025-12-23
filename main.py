from pydantic_ai import Agent

def main():
    print("Hello from py-wealth-management!")

    agent = Agent(  
        'openai:gpt-5-mini-2025-08-07',
        instructions='Be concise, reply with one sentence.',  
    )

    result = agent.run_sync('Where does "hello world" come from?')  
    print(result.output)

if __name__ == "__main__":
    main()
