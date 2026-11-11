"""The allowlist, the security core. Only (verb, resource) pairs loaded from
config are permitted, and all of them are read-only. Everything else is denied.
The LLM can suggest anything, but nothing runs unless it passes this gate."""

from .schemas import Action, ToolCall

# Verbs that can ever be allowed. Mutating verbs are hard-blocked here regardless
# of what the config says, as a second safety net.
_READ_ONLY_VERBS = {"get", "describe", "logs", "top"}

# Tools / resources that are cluster-scoped (no namespace). The namespace allowlist
# does not apply to these; they must not be wrongly denied when it is configured.
_CLUSTER_SCOPED_TOOLS = {"list_all_pods", "list_nodes", "describe_node",
                         "top_nodes", "list_namespaces",
                         # cluster-scoped metadata/storage tools: they take NO namespace,
                         # so a configured namespace allowlist must not deny them.
                         "get_version", "cluster_info", "get_api_resources", "get_crds",
                         "get_component_statuses", "list_storageclasses"}
_CLUSTER_SCOPED_RESOURCES = {"nodes", "namespaces", "persistentvolumes",
                             "storageclasses", "componentstatuses",
                             "customresourcedefinitions"}


class Policy:
    def __init__(self, allowlist: list[dict], tools: list[str] | None = None,
                 allowed_namespaces: list[str] | None = None):
        # Build a set of (verb, normalized-resource) that are explicitly allowed.
        self._allowed: set[tuple[str, str]] = set()
        for item in allowlist:
            verb = str(item.get("verb", "")).lower()
            resource = _norm_resource(str(item.get("resource", "")).lower())
            if verb in _READ_ONLY_VERBS and verb and resource:
                self._allowed.add((verb, resource))

        # Allowlist of tool NAMES for the investigate loop (read-only tools only).
        self._tools: set[str] = {str(t).strip() for t in (tools or []) if str(t).strip()}

        # Optional namespace allowlist. Empty = any namespace is permitted. When
        # set, callers can only touch these namespaces (a least-privilege scope that
        # also blunts prompt-injection that tries to pivot to kube-system etc.).
        self._namespaces: set[str] = {str(n).strip() for n in (allowed_namespaces or []) if str(n).strip()}

    def _ns_ok(self, namespace: str | None) -> bool:
        if not self._namespaces:      # no allowlist configured => all allowed
            return True
        if not namespace:             # list-all with a namespace allowlist is not allowed
            return False
        return namespace in self._namespaces

    def check_tool(self, call: ToolCall) -> tuple[bool, str]:
        """Gate for the investigate loop. The tool name must be on the allowlist and
        every argument must be a safe k8s-style value. 'final' is always allowed."""
        name = (call.tool or "").strip()
        if name in ("final", "done"):
            return True, "stop"
        if name not in self._tools:
            return False, f"tool '{name}' is not on the allowlist"
        for field in (call.namespace, call.name, call.container):
            if field and not _is_safe(field):
                return False, "invalid characters in argument"
        # Cluster-scoped tools ignore namespace scope; namespaced tools are checked.
        if name not in _CLUSTER_SCOPED_TOOLS and not self._ns_ok(call.namespace):
            return False, f"namespace '{call.namespace}' is not permitted"
        return True, "allowed"

    def check(self, action: Action) -> tuple[bool, str]:
        """Return (allowed, reason)."""
        verb = (action.verb or "").lower()
        resource = _norm_resource((action.resource or "").lower())

        if verb == "none" or resource == "none":
            return False, "not a question about the cluster"

        if verb not in _READ_ONLY_VERBS:
            return False, f"verb '{verb}' is not read-only"

        if (verb, resource) not in self._allowed:
            return False, f"action '{verb} {resource}' is not on the allowlist"

        # Basic argument sanity: reject anything that looks like injection.
        for field in (action.namespace, action.name):
            if field and not _is_safe(field):
                return False, "invalid characters in argument"

        # Namespace scope only applies to namespaced resources. For namespaced
        # resources with no namespace given, a configured allowlist would otherwise
        # be bypassed by the executor's --all-namespaces fallback, so deny those too.
        if resource not in _CLUSTER_SCOPED_RESOURCES and not self._ns_ok(action.namespace):
            return False, f"namespace '{action.namespace or 'all'}' is not permitted"

        return True, "allowed"


def _norm_resource(resource: str) -> str:
    """Normalise plural/singular so 'pod' and 'pods' match the same allowlist entry.
    kubectl accepts both; the model is inconsistent, so we canonicalise to plural."""
    if resource and not resource.endswith("s"):
        return resource + "s"
    return resource


def _is_safe(value: str) -> bool:
    """Allow only simple k8s-style names: letters, digits, dot, dash.

    A leading dash is rejected: even though argv has no shell, a value like '-rf'
    or '--all-namespaces' passed as a name/namespace would be parsed by kubectl as
    a FLAG (argument injection). Real k8s names never start with a dash."""
    if not value:
        return False
    if value.startswith("-"):
        return False
    return all(c.isalnum() or c in ".-" for c in value) and len(value) <= 253
