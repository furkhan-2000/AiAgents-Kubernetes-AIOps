"""Runs approved actions as kubectl commands. Arguments are passed as a list
(never a shell string), so there is no shell injection. The kubeconfig points to
a least-privilege, read-only service account.

Defence-in-depth: this executor hard-blocks any verb that is not read-only, no
matter what the caller passes. This is the last wall, below the policy allowlist
and below the cluster RBAC role."""

import subprocess
from .schemas import Action

# The ONLY verbs kubectl may ever be invoked with. Anything mutating
# (patch/create/apply/delete/edit/scale/exec/cp/port-forward/drain/cordon/...) is
# absent on purpose and rejected below.
_HARD_READ_ONLY_VERBS = frozenset({
    "get", "describe", "logs", "top", "events",
    "api-resources", "explain", "version", "cluster-info",
})

# Resources with no namespace. These take neither -n nor --all-namespaces
# (singular and plural both listed since the model is inconsistent).
_CLUSTER_SCOPED = frozenset({
    "node", "nodes", "namespace", "namespaces",
    "persistentvolume", "persistentvolumes", "pv",
    "storageclass", "storageclasses", "sc", "componentstatus", "componentstatuses",
})


class Executor:
    def __init__(self, kubeconfig: str, default_namespace: str):
        self._kubeconfig = kubeconfig
        self._default_ns = default_namespace

    def run(self, action: Action) -> str:
        # Executor-level read-only wall for the /query path too (not just the loop).
        if action.verb not in _HARD_READ_ONLY_VERBS:
            return f"error: refused non-read-only verb '{action.verb}'"

        # Build argv explicitly. No shell, no string concatenation of user input.
        argv = ["kubectl", f"--kubeconfig={self._kubeconfig}", action.verb]

        if action.verb in ("get", "top"):
            argv.append(action.resource)
            if action.name:
                # A specific object: scope to its namespace (default if none given).
                argv += [action.name, "-n", action.namespace or self._default_ns]
            elif action.namespace:
                argv += ["-n", action.namespace]
            elif (action.resource or "").lower() in _CLUSTER_SCOPED:
                # Cluster-scoped (e.g. nodes): no namespace flag at all.
                # (kubectl rejects `top nodes --all-namespaces`.)
                pass
            else:
                # No namespace named on a namespaced list query: show the whole
                # cluster rather than silently answering for 'default' only.
                argv.append("--all-namespaces")
        elif action.verb == "describe":
            argv += [action.resource]
            if action.name:
                argv.append(action.name)
            argv += ["-n", action.namespace or self._default_ns]
        elif action.verb == "logs":
            if action.name:
                argv.append(action.name)
            argv += ["-n", action.namespace or self._default_ns]

        return self._run(argv)

    def run_readonly(self, argv_tail: list[str], timeout: int = 30) -> str:
        """Run a read-only kubectl built by tools.py. argv_tail is everything after
        `kubectl --kubeconfig=...`. The first element MUST be a read-only verb or
        this refuses to run. This is the executor-level safety net for the loop."""
        if not argv_tail:
            return "error: empty command"
        verb = argv_tail[0]
        if verb not in _HARD_READ_ONLY_VERBS:
            # Should never happen (tools.py only builds read-only verbs); refuse hard.
            return f"error: refused non-read-only verb '{verb}'"
        argv = ["kubectl", f"--kubeconfig={self._kubeconfig}", *argv_tail]
        return self._run(argv, timeout=timeout)

    def _run(self, argv: list[str], timeout: int = 30) -> str:
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "error: command timed out"
        except OSError as exc:
            # Missing/renamed kubectl, permission denied, etc. Return a clean error
            # string like every other failure path instead of crashing the request.
            return f"error: could not run kubectl: {exc}"

        if result.returncode != 0:
            return f"error: {result.stderr.strip()}"
        out = result.stdout.strip()
        if not out:
            # Success but no stdout: kubectl prints "No resources found..." to
            # stderr. Surface it so the model sees an explicit result, not a blank
            # (a blank makes it re-run the same tool pointlessly).
            return result.stderr.strip() or "(no resources found)"
        return out
