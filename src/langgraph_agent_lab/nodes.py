"""Node functions for the LangGraph workflow.

Each function receives AgentState and returns a partial state update dict.
Do NOT mutate input state — return new values only.

LLM REQUIREMENT:
- classify_node MUST use a real LLM call (structured output for intent classification)
- answer_node MUST use a real LLM call (grounded response generation)
- evaluate_node SHOULD use LLM-as-judge (bonus points; heuristic acceptable for base score)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .llm import get_llm
from .state import AgentState, ApprovalDecision, make_event


class ClassificationDecision(BaseModel):
    route: Literal["simple", "tool", "missing_info", "risky", "error"]
    reason: str


def _message_text(response: object) -> str:
    """Extract serializable text from a LangChain model response."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def _audited_fallback_route(query: str) -> str:
    """Conservative classifier used only after an audited provider failure."""
    text = query.casefold()
    groups = (
        ("risky", ("refund", "delete", "cancel account", "charge", "transfer")),
        ("tool", ("lookup", "look up", "status", "order", "search", "check")),
        ("missing_info", ("fix it", "help me", "not working", "can you fix")),
        ("error", ("timeout", "failure", "exception", "server error")),
    )
    for route, signals in groups:
        if any(signal in text for signal in signals):
            return route
    return "simple"


# ─── EXAMPLE: working node (provided for reference) ──────────────────
def intake_node(state: AgentState) -> dict:
    """Normalize raw query. This node is provided as a working example."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── Workflow nodes ─────────────────────────────────────────────────


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using an LLM.

    *** MUST use a real LLM call — keyword-only heuristics will lose points. ***

    Use .with_structured_output() or equivalent to get reliable enum classification.
    The LLM should classify into one of: simple, tool, missing_info, risky, error.

    Hints:
    - See llm.py for the get_llm() helper
    - Use Pydantic model or TypedDict with .with_structured_output()
    - Set risk_level to "high" for risky routes, "low" otherwise
    - Priority guide: risky > tool > missing_info > error > simple

    Return: {"route": str, "risk_level": str, "events": [make_event(...)]}
    """
    query = state.get("query", "")
    prompt = f"""You classify support tickets for a workflow.
Return exactly one route and a short reason. Apply this priority when signals overlap:
risky > tool > missing_info > error > simple.
- risky: asks for a side effect requiring approval (refund, delete, cancel, charge, transfer)
- tool: needs data lookup or an external tool
- missing_info: too vague to act on safely
- error: reports a processing/system failure intended to exercise recovery
- simple: can be answered directly without a tool
Do not infer identifiers that are not present.

Ticket: {query}
"""
    try:
        raw_decision = get_llm().with_structured_output(ClassificationDecision).invoke(prompt)
        decision = ClassificationDecision.model_validate(raw_decision)
        route = decision.route
        return {
            "route": route,
            "risk_level": "high" if route == "risky" else "low",
            "events": [
                make_event(
                    "classify",
                    "completed",
                    "structured classification completed",
                    route=route,
                    reason=decision.reason,
                    structured=True,
                )
            ],
        }
    except Exception as exc:
        fallback_route = _audited_fallback_route(query)
        error = f"classifier provider failure: {type(exc).__name__}"
        return {
            "route": fallback_route,
            "risk_level": "high" if fallback_route == "risky" else "low",
            "errors": [error],
            "events": [
                make_event("classify", "fallback", error, route=fallback_route, structured=False)
            ],
        }


def tool_node(state: AgentState) -> dict:
    """Execute a mock tool call.

    Simulate transient failures for error-route scenarios to test retry loops.

    Requirements:
    - Read current attempt count from state
    - If route is "error" and attempt < 2: return error result (string containing "ERROR")
    - Otherwise: return a mock success result string
    - Append result to tool_results list

    Return: {"tool_results": [result_string], "events": [make_event(...)]}
    """
    attempt = state.get("attempt", 0)
    route = state.get("route", "")
    if route == "error" and attempt < 2:
        result = f"ERROR: transient support backend failure on attempt {attempt}"
        event_type = "failed"
    elif route == "risky":
        action = state.get("proposed_action") or "approved support action"
        result = f"Mock tool completed approved action: {action}"
        event_type = "completed"
    else:
        result = f"Mock tool result for ticket: {state.get('query', '')[:120]}"
        event_type = "completed"
    return {
        "tool_results": [result],
        "events": [make_event("tool", event_type, "mock tool execution finished", attempt=attempt)],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the retry-loop gate.

    Check whether the latest tool result is satisfactory or needs retry.

    SHOULD use LLM-as-judge for bonus points. Heuristic (e.g., check for "ERROR" substring)
    is acceptable for base score.

    Requirements:
    - Read the latest entry from tool_results
    - Set evaluation_result to "needs_retry" or "success"
    - This field drives route_after_evaluate conditional edge

    Note: You may need to add 'evaluation_result' to AgentState if not present.

    Return: {"evaluation_result": str, "events": [make_event(...)]}
    """
    results = state.get("tool_results", [])
    latest = results[-1] if results else ""
    verdict = "needs_retry" if "ERROR" in latest.upper() else "success"
    return {
        "evaluation_result": verdict,
        "events": [make_event("evaluate", "completed", "tool result evaluated", verdict=verdict)],
    }


def answer_node(state: AgentState) -> dict:
    """Generate a final response using an LLM.

    *** MUST use a real LLM call — hardcoded strings will lose points. ***

    The LLM should generate a helpful response grounded in available context:
    - tool_results (if any)
    - approval decision (if risky route)
    - original query

    Return: {"final_answer": str, "events": [make_event(...)]}
    """
    query = state.get("query", "")
    tool_results = state.get("tool_results", [])
    approval = state.get("approval")
    proposed_action = state.get("proposed_action")
    prompt = f"""Write a concise, helpful support response using only the context below.
Do not claim an action happened unless the tool result confirms it. If context is limited,
state the limitation. Do not mention internal workflow implementation.

Ticket: {query}
Tool results: {tool_results if tool_results else "none"}
Proposed action: {proposed_action or "none"}
Approval: {approval if approval is not None else "not applicable"}
"""
    try:
        answer = _message_text(get_llm(temperature=0.2).invoke(prompt))
        if not answer:
            raise ValueError("model returned an empty answer")
        return {
            "final_answer": answer,
            "events": [make_event("answer", "completed", "grounded response generated")],
        }
    except Exception as exc:
        error = f"answer provider failure: {type(exc).__name__}"
        fallback = (
            "The support request could not be answered because the response service is "
            "temporarily unavailable. Please try again or contact support."
        )
        return {
            "final_answer": fallback,
            "errors": [error],
            "events": [make_event("answer", "fallback", error)],
        }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    Generate a specific clarification question based on the vague/incomplete query.

    Note: You may need to add 'pending_question' to AgentState if not present.

    Return: {"pending_question": str, "final_answer": str, "events": [make_event(...)]}
    """
    approval = state.get("approval") or {}
    if approval and approval.get("approved") is False:
        comment = approval.get("comment") or "the proposed action was not approved"
        question = (
            f"The action was not approved ({comment}). What safer alternative would you like?"
        )
        reason = "approval rejected"
    else:
        question = (
            f"Please provide the affected account, order, or error details needed to handle: "
            f"'{state.get('query', '')[:100]}'."
        )
        reason = "missing information"
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "requested", reason)],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for human approval.

    Describe the proposed action and why it requires approval.

    Note: You may need to add 'proposed_action' to AgentState if not present.

    Return: {"proposed_action": str, "events": [make_event(...)]}
    """
    action = (
        f"Perform the requested support side effect after approval: {state.get('query', '')[:160]}"
    )
    return {
        "proposed_action": action,
        "events": [
            make_event(
                "risky_action",
                "proposed",
                "high-risk action prepared for review",
                risk_level=state.get("risk_level"),
            )
        ],
    }


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop approval step.

    Default behavior: mock approval (approved=True) so tests and CI run offline.
    Extension: if env LANGGRAPH_INTERRUPT=true, use langgraph.types.interrupt() for real HITL.

    Return approval fields plus a normalized audit event.
    """
    decision = ApprovalDecision(
        approved=True,
        reviewer="mock-reviewer",
        comment="Approved by deterministic CI-safe review gate",
    ).model_dump()
    return {
        "approval": decision,
        "events": [make_event("approval", "approved", "approval decision observed", approved=True)],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt.

    Increment the attempt counter and log the transient failure.

    Requirements:
    - Read current attempt from state, increment by 1
    - Add an error message to errors list
    - Return updated attempt count

    Return: {"attempt": int, "errors": [str], "events": [make_event(...)]}
    """
    next_attempt = state.get("attempt", 0) + 1
    max_attempts = state.get("max_attempts", 3)
    error = f"transient failure recorded; retry attempt {next_attempt}/{max_attempts}"
    return {
        "attempt": next_attempt,
        "errors": [error],
        "events": [
            make_event(
                "retry",
                "recorded",
                "retry attempt recorded",
                attempt=next_attempt,
                max_attempts=max_attempts,
            )
        ],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Handle unresolvable failures after max retries exceeded.

    This is the third layer: retry → fallback → dead letter.
    Log the failure and set a final_answer explaining that the request could not be completed.

    Return: {"final_answer": str, "events": [make_event(...)]}
    """
    attempt = state.get("attempt", 0)
    max_attempts = state.get("max_attempts", 3)
    answer = (
        f"The request could not be completed after {attempt} of {max_attempts} allowed "
        "attempts. It has been escalated for manual support review."
    )
    return {
        "final_answer": answer,
        "events": [
            make_event(
                "dead_letter",
                "exhausted",
                "retry budget exhausted",
                attempt=attempt,
                max_attempts=max_attempts,
            )
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Emit a final audit event. All routes must pass through here before END.

    Return: {"events": [make_event("finalize", "completed", "workflow finished")]}
    """
    return {"events": [make_event("finalize", "completed", "workflow finished")]}
