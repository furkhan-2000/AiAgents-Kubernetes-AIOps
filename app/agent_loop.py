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
from .counts import table_facts


class InvestigateLoop:
    def __init__(self, llm, policy, executor, default_ns: str, limits: dict, memory=None):
        self._llm = llm
        self._policy = policy
        self._executor = executor
        self._default_ns = default_ns
        self._memory = memory          # optional RagMemory; None = RAG off
        self._max_steps = int(limits.get("max_steps", 6))
        self._max_output = int(limits.get("max_output_bytes", 8000))
        self._deadline_s = int(limits.get("loop_timeout_seconds", 90))
        # Time reserved for the final synthesis call so it is never starved to a
        # 1-second timeout when the step loop consumes the budget.
        self._synth_reserve = int(limits.get("synth_reserve_seconds",
                                             min(60, max(10, self._deadline_s // 4))))
        # Upper bound for any single model/kubectl call (keeps per-call timeouts from
        # exceeding the Ollama budget). Defaults to the Ollama per-call timeout.
        self._call_cap = int(limits.get("call_cap_seconds", 300))

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_output:
            return text
        return text[: self._max_output] + "\n...[truncated]"

    def _remaining(self, start: float) -> int:
        """Seconds left in the loop budget, floored at 1 so calls still get a chance,
        capped at the per-call ceiling."""
        return max(1, min(self._call_cap, int(self._deadline_s - (time.monotonic() - start))))

    def _step_timeout(self, start: float) -> int:
        """Per-call timeout for a STEP: bounded by the step phase (deadline minus the
        synthesis reserve) so the reserve is actually preserved, and by the per-call
        ceiling. Floored at 1."""
        step_budget = (self._deadline_s - self._synth_reserve) - (time.monotonic() - start)
        return max(1, min(self._call_cap, int(step_budget)))

    def run(self, question: str, caller: str) -> InvestigateResponse:
        steps: list[Step] = []
        history_parts: list[str] = []
        notes: list[str] = []           # denied/failed attempts, for a useful no-evidence reply
        seen: set[str] = set()          # (tool,args) signatures already executed
        listed_all = False              # list_all_pods already ran (covers every namespace)
        used_tools: list[str] = []      # tools that actually ran (for RAG memory)
        start = time.monotonic()

        # RAG hint: tool NAMES that worked for similar past questions. Only names,
        # never past free-text, so a poisoned memory cannot inject a prompt. The
        # policy gate still validates every tool, so this can never escalate. The
        # hint is given ONLY to the planner (next_step), never to synthesis or the
        # no-evidence path, so it never masquerades as gathered evidence.
        hint_line = ""
        if self._memory is not None:
            hint = self._memory.recall(question)
            if hint:
                hint_line = ("[hint] similar past questions were answered using these "
                             "read-only tools (use only if relevant): " + ", ".join(hint))

        for i in range(self._max_steps):
            # Stop stepping once only the synthesis reserve is left, so the final
            # answer always has time to run (not a starved 1s timeout).
            if time.monotonic() - start > (self._deadline_s - self._synth_reserve):
                audit("loop.timeout", caller=caller, steps=len(steps))
                break

            # 1. Brain picks the next tool (or decides to stop). A transient parse
            # hiccup wastes one step (continue), it does not kill the investigation.
            try:
                # Prepend the RAG hint to the planner's view only (not stored).
                plan_history = "\n\n".join(([hint_line] if hint_line else []) + history_parts)
                call: ToolCall = self._llm.next_step(
                    question, toolmod.CATALOG, plan_history,
                    timeout=self._step_timeout(start),
                )
            except LLMError as exc:
                audit("loop.plan_error", caller=caller, error=str(exc))
                notes.append("the planner returned an unreadable step")
                continue

            if call.tool in ("final", "done"):
                audit("loop.final_requested", caller=caller, steps=len(steps))
                break

            # Redundancy guard: list_all_pods already returned every namespace's pods,
            # so a later plain list_pods adds nothing. Skip it (the model sometimes
            # re-lists each namespace, wasting slow CPU calls).
            if listed_all and call.tool == "list_pods":
                audit("loop.redundant", caller=caller, tool=call.tool)
                history_parts.append(
                    f"[step {i+1}] list_pods is redundant: list_all_pods already showed "
                    "every namespace. Inspect a specific pod or answer {\"tool\":\"final\"}."
                )
                continue

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
            # same call and waste the step budget. Nudge it toward a new angle. The
            # signature uses the EFFECTIVE namespace so "no namespace" and an
            # explicit "default" are treated as the same call.
            args = call.model_dump(exclude_none=True, exclude={"tool", "reason"})
            eff_ns = call.namespace or self._default_ns
            sig = f"{call.tool}|{eff_ns}|{call.name}|{call.container}|{call.tail_lines}|{call.previous}"
            if sig in seen:
                audit("loop.duplicate", caller=caller, tool=call.tool, args=args)
                history_parts.append(
                    f"[step {i+1}] you already ran {call.tool} {args}. Do NOT repeat it. "
                    "Either call a different tool or answer with {\"tool\":\"final\"}."
                )
                continue
            seen.add(sig)

            # kubectl is quick; cap it at 30s but never above the remaining step budget.
            raw = self._executor.run_readonly(argv_tail, timeout=min(30, self._step_timeout(start)))
            output = self._truncate(_redact(raw))
            # Ground the model with an EXACT count computed in code (it cannot count
            # rows reliably). Prepended so the answer cites real numbers.
            fact = table_facts(output)
            if fact:
                output = f"[EXACT COUNT (computed, authoritative): {fact}]\n{output}"
            audit("loop.tool", caller=caller, tool=call.tool, args=args)

            if call.tool == "list_all_pods":
                listed_all = True
            used_tools.append(call.tool)
            steps.append(Step(tool=call.tool, args=args, observation=output))
            history_parts.append(f"[step {i+1}] {call.tool} {args}\n{output}")

        # 4a. Zero steps gathered. This is the correct shape for a read-only REFUSAL
        # of an action request, or an out-of-scope question. Let the model write the
        # proper answer (refuse per its rules, or explain) instead of a canned line.
        if not steps:
            canned = ("I could not gather any evidence for this question. Please rephrase "
                      "it in terms of the cluster (for example a namespace or a pod name).")
            if notes:
                canned += " I attempted: " + "; ".join(dict.fromkeys(notes)) + "."
            history = "\n".join(history_parts) or "(no cluster data was gathered)"
            try:
                answer = _redact(self._llm.synthesize(
                    question, history, timeout=min(self._call_cap, self._synth_reserve)))
            except LLMError:
                answer = canned
            if not answer.strip() or (answer.lstrip().startswith("{") and '"tool"' in answer):
                answer = canned
            audit("loop.answer", caller=caller, steps=0)
            return InvestigateResponse(allowed=True, answer=answer, steps=steps)

        try:
            # Give synthesis at least its reserved budget even if the loop ran long,
            # but never exceed the per-call ceiling.
            synth_timeout = min(self._call_cap, max(self._synth_reserve, self._remaining(start)))
            answer = self._llm.synthesize(
                question, "\n\n".join(history_parts), timeout=synth_timeout)
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

        # Redact the final answer too: the model could echo a secret-looking string
        # from the evidence into its prose.
        answer = _redact(answer)

        # Remember this VERIFIED-GOOD case: the question + the tools that produced a
        # real answer. Stores tool names only (no data), fail-open.
        if self._memory is not None and used_tools:
            self._memory.remember(question, used_tools)

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
