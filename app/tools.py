"""The read-only tool registry for the investigate loop.

Every tool here is strictly READ / LIST / WATCH / INSPECT. There is deliberately
no tool that can patch, create, delete, apply, edit, scale, drain, cordon, exec,
cp, or port-forward. The LLM can only ever pick a tool NAME from this catalog and
supply a namespace / name; it never writes raw kubectl. The executor enforces the
read-only verb set again at the lowest level, so even a bug here cannot mutate.

To add a capability: add a builder below AND add its name to config.yaml
(investigation.tools) AND confirm the verb is covered by the cluster read-only
RBAC role. Keep all three in sync.
"""

from .schemas import ToolCall

# Human-readable catalog shown to the LLM so it knows exactly what it may call.
# Keep descriptions short and literal; the brain is told to use ONLY these.
CATALOG: dict[str, str] = {
    "list_all_pods":      "List pods across ALL namespaces (status, restarts). Use this when the question is cluster-wide or no namespace is given. Args: none.",
    "list_pods":          "List pods in a namespace. Omit namespace to search ALL namespaces. Args: namespace (optional).",
    "describe_pod":       "Full detail + recent events for one pod. Args: namespace, name.",
    "get_logs":           "Logs of one pod. Args: namespace, name, tail_lines, previous.",
    "get_events":         "Recent events, newest last. Omit namespace to search ALL namespaces. Args: namespace (optional).",
    "list_nodes":         "List cluster nodes with status and roles. Args: none.",
    "describe_node":      "Detail + conditions + pressure for one node. Args: name.",
    "top_pods":           "CPU/memory usage per pod. Omit namespace for ALL namespaces. Args: namespace (optional).",
    "top_nodes":          "CPU/memory usage per node. Args: none.",
    "list_deployments":   "List deployments (ready/available). Omit namespace for ALL namespaces. Args: namespace (optional).",
    "describe_deployment":"Detail + conditions for one deployment. Args: namespace, name.",
    "list_services":      "List services. Omit namespace to search ALL namespaces. Args: namespace (optional).",
    "describe_service":   "Detail for one service (ports, selector). Args: namespace, name.",
    "get_endpoints":      "Which pods back the services (debug 'service unreachable'). Omit namespace for ALL namespaces. Args: namespace (optional).",
    "get_ingresses":      "List ingresses (external routes/URLs). Omit namespace for ALL namespaces. Args: namespace (optional).",
    "get_pvcs":           "List persistent volume claims (storage). Omit namespace for ALL namespaces. Args: namespace (optional).",
    "list_namespaces":    "List all namespaces. Args: none.",
}


def _needs_name(call: ToolCall) -> str:
    if not call.name:
        raise ValueError("this tool requires a resource 'name'")
    return call.name


def _ns_scope(call: ToolCall) -> list[str]:
    """Namespace selector for a LIST/GET tool. If the model named a namespace, scope
    to it (`-n <ns>`); if it gave none, search EVERY namespace (`--all-namespaces`)
    rather than silently assuming 'default' (which gave wrong, default-only answers).
    When a namespace allowlist is configured, policy.check_tool denies the no-namespace
    case BEFORE this runs, so the cluster-wide sweep can never bypass that scope."""
    return ["-n", call.namespace] if call.namespace else ["--all-namespaces"]


def build_argv(call: ToolCall, default_ns: str) -> list[str]:
    """Translate a validated ToolCall into a read-only kubectl argument tail.

    Returns the args that come AFTER `kubectl --kubeconfig=...`. Raises ValueError
    for an unknown tool or missing required argument. Never builds a mutating verb.
    """
    # For tools that need a specific named object, a namespace must accompany it; fall
    # back to default_ns only there. LIST/GET tools instead sweep all namespaces when
    # none is named (see _ns_scope) so cluster-wide questions get complete answers.
    ns = call.namespace or default_ns
    tool = call.tool

    if tool == "list_all_pods":
        return ["get", "pods", "--all-namespaces", "-o", "wide"]
    if tool == "list_pods":
        return ["get", "pods", *_ns_scope(call), "-o", "wide"]
    if tool == "describe_pod":
        return ["describe", "pod", _needs_name(call), "-n", ns]
    if tool == "get_logs":
        argv = ["logs", _needs_name(call), "-n", ns, "--tail", str(call.tail_lines or 200),
                "--limit-bytes", "1000000"]  # bound memory from a huge log stream
        if call.container:
            argv += ["-c", call.container]
        else:
            # Multi-container pods (sidecars like istio-proxy) otherwise error with
            # "a container name must be specified". Read them all, prefixed by name.
            argv += ["--all-containers=true", "--prefix=true"]
        if call.previous:
            argv.append("--previous")
        return argv
    if tool == "get_events":
        return ["get", "events", *_ns_scope(call), "--sort-by=.lastTimestamp"]
    if tool == "list_nodes":
        return ["get", "nodes", "-o", "wide"]
    if tool == "describe_node":
        return ["describe", "node", _needs_name(call)]
    if tool == "top_pods":
        return ["top", "pods", *_ns_scope(call)]
    if tool == "top_nodes":
        return ["top", "nodes"]
    if tool == "list_deployments":
        return ["get", "deployments", *_ns_scope(call), "-o", "wide"]
    if tool == "describe_deployment":
        return ["describe", "deployment", _needs_name(call), "-n", ns]
    if tool == "list_services":
        return ["get", "services", *_ns_scope(call), "-o", "wide"]
    if tool == "describe_service":
        return ["describe", "service", _needs_name(call), "-n", ns]
    if tool == "get_endpoints":
        return ["get", "endpoints", *_ns_scope(call), "-o", "wide"]
    if tool == "get_ingresses":
        return ["get", "ingresses", *_ns_scope(call)]
    if tool == "get_pvcs":
        return ["get", "persistentvolumeclaims", *_ns_scope(call)]
    if tool == "list_namespaces":
        return ["get", "namespaces"]

    raise ValueError(f"unknown tool '{tool}'")
