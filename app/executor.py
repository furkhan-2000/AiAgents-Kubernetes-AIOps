"""Runs the approved action as a kubectl command. Arguments are passed as a list
(never a shell string), so there is no shell injection. The kubeconfig points to
a least-privilege, read-only service account."""

import subprocess
from .schemas import Action


class Executor:
    def __init__(self, kubeconfig: str, default_namespace: str):
        self._kubeconfig = kubeconfig
        self._default_ns = default_namespace

    def run(self, action: Action) -> str:
        ns = action.namespace or self._default_ns

        # Build argv explicitly. No shell, no string concatenation of user input.
        argv = ["kubectl", f"--kubeconfig={self._kubeconfig}", action.verb]

        if action.verb in ("get", "top"):
            argv.append(action.resource)
            if action.name:
                argv.append(action.name)
            argv += ["-n", ns]
        elif action.verb == "describe":
            argv += [action.resource]
            if action.name:
                argv.append(action.name)
            argv += ["-n", ns]
        elif action.verb == "logs":
            if action.name:
                argv.append(action.name)
            argv += ["-n", ns]

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "error: command timed out"

        if result.returncode != 0:
            return f"error: {result.stderr.strip()}"
        return result.stdout.strip()
