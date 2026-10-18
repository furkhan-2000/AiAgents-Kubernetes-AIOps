"""The allowlist, the security core. Only (verb, resource) pairs loaded from
config are permitted, and all of them are read-only. Everything else is denied.
The LLM can suggest anything, but nothing runs unless it passes this gate."""

from .schemas import Action

# Verbs that can ever be allowed. Mutating verbs are hard-blocked here regardless
# of what the config says, as a second safety net.
_READ_ONLY_VERBS = {"get", "describe", "logs", "top"}


class Policy:
    def __init__(self, allowlist: list[dict]):
        # Build a set of (verb, resource) that are explicitly allowed and read-only.
        self._allowed: set[tuple[str, str]] = set()
        for item in allowlist:
            verb = str(item.get("verb", "")).lower()
            resource = str(item.get("resource", "")).lower()
            if verb in _READ_ONLY_VERBS and verb and resource:
                self._allowed.add((verb, resource))

    def check(self, action: Action) -> tuple[bool, str]:
        """Return (allowed, reason)."""
        verb = (action.verb or "").lower()
        resource = (action.resource or "").lower()

        if verb not in _READ_ONLY_VERBS:
            return False, f"verb '{verb}' is not read-only"

        if (verb, resource) not in self._allowed:
            return False, f"action '{verb} {resource}' is not on the allowlist"

        # Basic argument sanity: reject anything that looks like injection.
        for field in (action.namespace, action.name):
            if field and not _is_safe(field):
                return False, "invalid characters in argument"

        return True, "allowed"


def _is_safe(value: str) -> bool:
    """Allow only simple k8s-style names: letters, digits, dot, dash."""
    return all(c.isalnum() or c in ".-" for c in value) and len(value) <= 253
