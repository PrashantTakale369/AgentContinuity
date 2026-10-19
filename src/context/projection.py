"""Conservative task-state projection from retained evidence."""

from ..storage.ledger import Ledger


def assertion(text: str, evidence: list[str], fingerprint: str, *, status: str = "proposed", origin: str = "user_prompt") -> dict:
    return {"text": text, "evidence": evidence, "fingerprint": fingerprint, "status": status, "origin": origin}


def project(ledger: Ledger, snapshot: dict) -> dict:
    """Conservative projection: preserve complete prompts; do not invent semantic summaries."""
    state = ledger.task()
    events = ledger.events()
    users = [event for event in events if event["kind"] == "user" and event["text"].strip()]
    if state["objective"] is None and users:
        state["objective"] = assertion(users[0]["text"], [users[0]["id"]], snapshot["fingerprint"])
    # Explicit checkpoints describe progress; model prose remains unverified prose.
    seen = set()
    declarations = []
    for event in reversed(events):
        if event["kind"] == "assistant" and event["text"].strip() and event["text"] not in seen:
            seen.add(event["text"])
            declarations.append(assertion(event["text"], [event["id"]], snapshot["fingerprint"],
                                          status="implemented_unverified", origin="model_declaration"))
            if len(declarations) == 8:
                break
    state["progress"] = list(reversed(declarations))
    with ledger.db:
        ledger.save_state(state)
    return state
