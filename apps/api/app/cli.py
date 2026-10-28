"""Talk to the receptionist in the terminal (text only), showing its tool calls.

python -m app.cli
"""

import asyncio
import json

from app.agent import Conversation, Receptionist
from app.db import init_db
from app.llm import get_chat_model
from app.prompts import GREETING
from app.seed import seed_if_empty

DIM, RESET = "\033[2m", "\033[0m"


async def main() -> None:
    init_db()
    seed_if_empty()
    receptionist = Receptionist(get_chat_model())
    conversation = Conversation([{"role": "assistant", "content": GREETING}])
    print(f"Sofia: {GREETING}\n(Ctrl+C or an empty line to quit)\n")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        print("Sofia: ", end="", flush=True)
        async for event in receptionist.respond(conversation, text):
            if event["type"] == "text":
                print(event["delta"], end="", flush=True)
            elif event["type"] == "tool_call":
                print(f"\n{DIM}  -> {event['name']}({json.dumps(event['arguments'], ensure_ascii=False)}){RESET}")
            elif event["type"] == "tool_result":
                print(f"{DIM}  <- {json.dumps(event['result'], ensure_ascii=False)[:300]}{RESET}")
            elif event["type"] == "error":
                print(f"\n[error] {event['message']}")
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
