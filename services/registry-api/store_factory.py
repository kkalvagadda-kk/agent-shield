"""Single construction choke point for the ConversationStore (§4.1).

`get_conversation_store()` is the ONLY place that picks a transcript backend.
Callers depend on the `ConversationStore` Protocol, never on a concrete adapter.
A future backend ships as a new class + a `CONVERSATION_STORE` value here.
"""
import os

from conversation_store import ConversationStore, PostgresConversationStore


def get_conversation_store() -> ConversationStore:
    backend = os.getenv("CONVERSATION_STORE", "postgres")
    if backend == "postgres":
        return PostgresConversationStore()
    raise ValueError(f"Unknown CONVERSATION_STORE={backend!r}")
