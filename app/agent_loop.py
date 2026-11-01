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
from .redact import redact as _redact


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

    def _remaining(self, start: float) -> int:
        """Seconds left in the loop budget, floored at 1 so calls still get a chance."""
        return max(1, int(self._deadline_s - (time.monotonic() - start)))

    def run(self, question: str, caller: str) -> InvestigateResponse:
        steps: list[Step] = []
        history_parts: list[str] = []
        notes: list[str] = []           # denied/failed attempts, for a useful no-evidence reply
        seen: set[str] = set()          # (tool,args) signatures already executed
        start = time.monotonic()

        for i in range(self._max_steps):
            if time.monotonic() - start > self._deadline_s:
                audit("loop.timeout", caller=caller, steps=len(steps))
                break

            # 1. Brain picks the next tool (or decides to stop). A transient parse
            # hiccup wastes one step (continue), it does not kill the investigation.
            try:
                call: ToolCall = self._llm.next_step(
                    question, toolmod.CATALOG, "\n\n".join(history_parts),
                    timeout=self._remaining(start),
                )
            except LLMError as exc:
                audit("loop.plan_error", caller=caller, error=str(exc))
                notes.append("the planner returned an unreadable step")
                continue

            if call.tool in ("final", "done"):
                audit("loop.final_requested", caller=caller, steps=len(steps))
                break

            # 2. Policy gate: tool must be on the allowlist and args must be safe.
            allowed, reason = self._policy.check_tool(call)
            if not allowed:
                audit("loop.denied", caller=caller, tool=call.tool, reason=reason)
                history_parts.append(f"[step {i+1}] tool '{call.tool}' was denied: {reason}")
                notes.append(f"{call.tool} was not permitted ({reason})")
                continue

            # 3. Build the read-only argv and run it (executor enforces read-only again).
            try:
                argv_tail = toolmod.build_argv(call, self._default_ns)
            except ValueError as exc:
                history_parts.append(f"[step {i+1}] could not run '{call.tool}': {exc}")
                notes.append(f"{call.tool} could not run ({exc})")
                continue

            # Skip a tool+args we already ran: a small model tends to repeat the
            # same call and waste the step budget. Nudge it toward a new angle.
            args = call.model_dump(exclude_none=True, exclude={"tool", "reason"})
            sig = f"{call.tool}:{sorted(args.items())}"
            if sig in seen:
                audit("loop.duplicate", caller=caller, tool=call.tool, args=args)
                history_parts.append(
                    f"[step {i+1}] you already ran {call.tool} {args}. Do NOT repeat it. "
                    "Either call a different tool or answer with {\"tool\":\"final\"}."
                )
                continue
            seen.add(sig)

            raw = self._executor.run_readonly(argv_tail, timeout=self._remaining(start))
            output = self._truncate(_redact(raw))
            audit("loop.tool", caller=caller, tool=call.tool, args=args)

            steps.append(Step(tool=call.tool, args=args, observation=output))
            history_parts.append(f"[step {i+1}] {call.tool} {args}\n{output}")

        # 4. Synthesize the final grounded answer. No evidence -> honest refusal,
        # including what was attempted so the user has a useful signal.
        if not steps:
            msg = ("I could not gather any evidence for this question. Please rephrase "
                   "it in terms of the cluster (for example a namespace or a pod name).")
            if notes:
                msg += " I attempted: " + "; ".join(dict.fromkeys(notes)) + "."
            return InvestigateResponse(allowed=True, answer=msg, steps=steps)

        try:
            answer = self._llm.synthesize(
                question, "\n\n".join(history_parts), timeout=self._remaining(start))
        except LLMError as exc:
            audit("loop.synth_error", caller=caller, error=str(exc))
            return InvestigateResponse(
                allowed=True,
                answer="I gathered data but could not summarise it. The raw findings are "
                       "in the steps below.",
                steps=steps,
            )

        # A small model sometimes emits a tool-call JSON instead of prose. Detect
        # that and fall back to a plain summary of what was observed, rather than
        # showing the user a raw {"tool":"final"} blob.
        stripped = answer.strip()
        if not stripped or (stripped.startswith("{") and '"tool"' in stripped):
            audit("loop.synth_nonprose", caller=caller)
            answer = self._fallback_summary(steps)

        audit("loop.answer", caller=caller, steps=len(steps))
        return InvestigateResponse(allowed=True, answer=answer, steps=steps)

    @staticmethod
    def _fallback_summary(steps: list[Step]) -> str:
        """Plain summary built directly from observations when the model fails to
        write prose. Honest and grounded: it only restates what was gathered."""
        lines = ["I gathered the data but could not phrase a summary. Raw findings:"]
        for s in steps:
            obs = (s.observation or "(no output)").strip()
            head = obs.splitlines()[0] if obs else "(no output)"
            lines.append(f"- {s.tool} {s.args}: {head}")
        return "\n".join(lines)
