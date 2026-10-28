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
    "list_pods":          "List pods in a namespace (status, restarts, node). Args: namespace.",
    "describe_pod":       "Full detail + recent events for one pod. Args: namespace, name.",
    "get_logs":           "Logs of one pod. Args: namespace, name, tail_lines, previous.",
    "get_events":         "Recent events in a namespace, newest last. Args: namespace.",
    "list_nodes":         "List cluster nodes with status and roles. Args: none.",
    "describe_node":      "Detail + conditions + pressure for one node. Args: name.",
    "top_pods":           "CPU/memory usage per pod in a namespace. Args: namespace.",
    "top_nodes":          "CPU/memory usage per node. Args: none.",
    "list_deployments":   "List deployments in a namespace (ready/available). Args: namespace.",
    "describe_deployment":"Detail + conditions for one deployment. Args: namespace, name.",
    "list_services":      "List services in a namespace. Args: namespace.",
    "list_namespaces":    "List all namespaces. Args: none.",
}


def _needs_name(call: ToolCall) -> str:
    if not call.name:
        raise ValueError("this tool requires a resource 'name'")
    return call.name


def build_argv(call: ToolCall, default_ns: str) -> list[str]:
    """Translate a validated ToolCall into a read-only kubectl argument tail.

    Returns the args that come AFTER `kubectl --kubeconfig=...`. Raises ValueError
    for an unknown tool or missing required argument. Never builds a mutating verb.
    """
    ns = call.namespace or default_ns
    tool = call.tool

    if tool == "list_pods":
        return ["get", "pods", "-n", ns, "-o", "wide"]
    if tool == "describe_pod":
        return ["describe", "pod", _needs_name(call), "-n", ns]
    if tool == "get_logs":
        argv = ["logs", _needs_name(call), "-n", ns, "--tail", str(call.tail_lines or 200)]
        if call.previous:
            argv.append("--previous")
        return argv
    if tool == "get_events":
        return ["get", "events", "-n", ns, "--sort-by=.lastTimestamp"]
    if tool == "list_nodes":
        return ["get", "nodes", "-o", "wide"]
    if tool == "describe_node":
        return ["describe", "node", _needs_name(call)]
    if tool == "top_pods":
        return ["top", "pods", "-n", ns]
    if tool == "top_nodes":
        return ["top", "nodes"]
    if tool == "list_deployments":
        return ["get", "deployments", "-n", ns, "-o", "wide"]
    if tool == "describe_deployment":
        return ["describe", "deployment", _needs_name(call), "-n", ns]
    if tool == "list_services":
        return ["get", "services", "-n", ns, "-o", "wide"]
    if tool == "list_namespaces":
        return ["get", "namespaces"]

    raise ValueError(f"unknown tool '{tool}'")
