"""
Order-agent example — demonstrates lookup_order (low-risk) and issue_refund (high-risk).

Run locally:
    agentshield dev --agent agent:agent

Or directly:
    python agent.py
"""
from agentshield_sdk import Agent, Runner, tool


@tool(risk="low")
def lookup_order(order_id: str) -> dict:
    """Look up the status of an order."""
    # Mock implementation — replace with real order system call.
    return {
        "order_id": order_id,
        "status": "delivered",
        "delivered_at": "2026-06-20T14:30:00Z",
        "customer": "Jane Smith",
        "items": ["Widget A x2"],
        "total": 49.99,
    }


@tool(risk="high")
def issue_refund(order_id: str, amount: float) -> dict:
    """Issue a refund for an order. Requires human approval."""
    # Mock implementation — replace with real payment processor call.
    return {
        "refund_id": f"ref_{order_id}",
        "status": "processed",
        "amount": amount,
        "order_id": order_id,
        "estimated_days": 3,
    }


agent = Agent(
    name="order-agent",
    instructions=(
        "You are a helpful order management assistant for an e-commerce platform. "
        "Use lookup_order to check order status. "
        "Use issue_refund to process refunds — these require human approval before executing. "
        "Always look up the order before issuing a refund to confirm it exists."
    ),
    tools=[lookup_order, issue_refund],
)


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        runner = Runner(agent)
        await runner.setup()

        print("--- Lookup order ---")
        result = await runner.run("What is the status of order 12345?")
        print(result["response"])
        print(f"Thread: {result['thread_id']}")

    asyncio.run(main())
