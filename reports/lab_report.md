# Day 08 Lab Report

## 1. Team / student

- Name: Đinh Huy Mạnh
- Student ID: 2A202601677
- Repository: https://github.com/huymanh222004/phase2-k3-4-track3-day8-langgraph-agent-2A202601677-DinhHuyManh
- Baseline commit: `6d8252d3c3499a9540dc4c24570b7197d6c12694`
- Date: 2026-08-25

## 2. Architecture

The graph registers eleven nodes. `START -> intake -> classify` is followed by conditional
routing for simple, tool, missing-information, risky, and error tickets. Tool output is evaluated
and can enter a bounded retry loop. Risky work is only prepared before the approval gate. Every
terminal branch passes through `finalize -> END`.

## 3. State schema

| Field | Reducer | Why |
|---|---|---|
| messages/tool_results/errors/events | append (`operator.add`) | Preserve ordered audit history |
| route, risk_level | overwrite | Preserve the current classified decision |
| attempt, max_attempts, evaluation_result | overwrite | Enforce deterministic retry gating |
| pending_question/action/approval/final_answer | overwrite | Hold the latest output/decision |

## 4. Scenario results

| Metric | Value |
|---|---:|
| Total scenarios | 7 |
| Success rate | 100.0% |
| Average events/nodes visited | 6.43 |
| Total retries | 3 |
| Approval-node visits | 2 |
| Verified resume | No |

| Scenario | Expected route | Actual route | Success | Retries | Approval visits |
|---|---|---|---:|---:|---:|
| S01_simple | simple | simple | Yes | 0 | 0 |
| S02_tool | tool | tool | Yes | 0 | 0 |
| S03_missing | missing_info | missing_info | Yes | 0 | 0 |
| S04_risky | risky | risky | Yes | 0 | 1 |
| S05_error | error | error | Yes | 2 | 0 |
| S06_delete | risky | risky | Yes | 0 | 1 |
| S07_dead_letter | error | error | Yes | 1 | 0 |

`latency_ms` remains zero because the starter metric helper does not receive wall-clock duration.
Approval visits use a deterministic mock gate and therefore are not claims of real interrupts.

## 5. Failure analysis

1. **Transient tool failure:** an `ERROR` tool result is detected by `evaluate`, recorded by
   `retry`, and retried only while `attempt < max_attempts`. At the boundary, `dead_letter`
   produces an escalation answer and can only continue to `finalize`. Residual risk: the base
   evaluator uses a string signal rather than a semantic judge.
2. **Risky action without approval:** `risky_action` only stores a proposal. `approval` must be
   observed before the approved route reaches `tool`; rejection routes to `clarify`, never to the
   tool. Residual risk: core CI uses mock approval rather than a real reviewer interrupt.
3. **Provider failure:** classifier and answer failures are visible in `errors` and fallback audit
   events. Classification falls back conservatively and answer generation returns a transparent
   service-unavailable response instead of silently claiming model success.

## 6. Persistence / recovery evidence

The CLI constructs one `MemorySaver`, passes it to `build_graph`, and invokes each scenario with
`{"configurable": {"thread_id": state["thread_id"]}}`. This preserves per-thread checkpoint
history within the process. A local probe for `thread-persistence-proof` returned six history
states and the terminal event trail `intake,classify,clarify,finalize`. `resume_success` remains
false because durable restart/resume was not implemented or claimed.

## 7. Extension work

No optional SQLite/Postgres or real-interrupt extension is claimed. The submission keeps the core
workflow deterministic and CI-safe.

## 8. Improvement plan

First add durable SQLite persistence plus a restart/resume integration test. This would turn the
current in-process checkpoint evidence into verified recovery while keeping the same state and
thread contract.
