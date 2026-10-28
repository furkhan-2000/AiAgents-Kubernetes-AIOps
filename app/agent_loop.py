"""The investigate loop: a bounded think -> act -> observe cycle.

The brain picks one read-only tool at a time; the loop runs it through the policy
gate and the read-only executor, feeds the real output back, and repeats until the
brain says 'final' or a hard cap is hit. Then the brain writes a plain-English
answer grounded ONLY in what was observed.

Every call is read-only and audited. Hard caps (max steps, output size, wall-clock)
stop runaway loops. The brain's final text is a suggestion for a human; nothing it
says is ever executed.
"""

import time

from .schemas import ToolCall, Step, InvestigateResponse
from .llm import LLMError
from . import tools as toolmod
from .audit import audit


class InvestigateLoop:
    def __init__(self, llm, policy, executor, default_ns: str, limits: dict):
        self._llm = llm
        self._policy = policy
        self._executor = executor
        self._default_ns = default_ns
        self._max_steps = int(limits.get("max_steps", 6))
        self._max_output = int(limits.get("max_output_bytes", 8000))
        self._deadline_s = int(limits.get("loop_timeout_seconds", 90))

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_output:
            return text
        return text[: self._max_output] + "\n...[truncated]"

    def run(self, question: str, caller: str) -> InvestigateResponse:
        steps: list[Step] = []
        history_parts: list[str] = []
        start = time.monotonic()

        for i in range(self._max_steps):
            if time.monotonic() - start > self._deadline_s:
                audit("loop.timeout", caller=caller, steps=len(steps))
                break

            # 1. Brain picks the next tool (or decides to stop).
            try:
                call: ToolCall = self._llm.next_step(question, toolmod.CATALOG, "\n\n".join(history_parts))
            except LLMError as exc:
                audit("loop.plan_error", caller=caller, error=str(exc))
                break

            if call.tool in ("final", "done"):
                audit("loop.final_requested", caller=caller, steps=len(steps))
                break

            # 2. Policy gate: tool must be on the allowlist and args must be safe.
            allowed, reason = self._policy.check_tool(call)
            if not allowed:
                audit("loop.denied", caller=caller, tool=call.tool, reason=reason)
                history_parts.append(f"[step {i+1}] tool '{call.tool}' was denied: {reason}")
                continue

            # 3. Build the read-only argv and run it (executor enforces read-only again).
            try:
                argv_tail = toolmod.build_argv(call, self._default_ns)
            except ValueError as exc:
                history_parts.append(f"[step {i+1}] could not run '{call.tool}': {exc}")
                continue

            output = self._truncate(self._executor.run_readonly(argv_tail))
            args = call.model_dump(exclude_none=True, exclude={"tool", "reason"})
            audit("loop.tool", caller=caller, tool=call.tool, args=args)

            steps.append(Step(tool=call.tool, args=args, observation=output))
            history_parts.append(f"[step {i+1}] {call.tool} {args}\n{output}")

        # 4. Synthesize the final grounded answer. No evidence -> honest refusal.
        if not steps:
            return InvestigateResponse(
                allowed=True,
                answer="I could not gather any evidence for this question. Please rephrase "
                       "it in terms of the cluster (for example a namespace or a pod name).",
                steps=steps,
            )

        try:
            answer = self._llm.synthesize(question, "\n\n".join(history_parts))
        except LLMError as exc:
            audit("loop.synth_error", caller=caller, error=str(exc))
            return InvestigateResponse(
                allowed=True,
                answer="I gathered data but could not summarise it. The raw findings are "
                       "in the steps below.",
                steps=steps,
            )

        audit("loop.answer", caller=caller, steps=len(steps))
        return InvestigateResponse(allowed=True, answer=answer, steps=steps)
