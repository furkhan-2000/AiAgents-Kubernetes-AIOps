# Kubernetes AIOps Agent

An offline, air-gapped AI assistant for Kubernetes. A user asks a plain-English
question like "how many pods are running in kube-system?" and gets a real answer
back. Behind the scenes an on-prem LLM reads the question, a strict policy decides
whether the matching kubectl command is safe, and only read-only commands ever run.

Nothing is sent to the internet. The model runs locally. The agent can never
create, change, or delete anything in the cluster. This is built to fintech-grade
standards: air-gapped, least-privilege, defence-in-depth, low blast radius.

> **New here? Start with [docs/00-START-HERE.md](docs/00-START-HERE.md)** — a guided,
> step-by-step setup (prerequisites → setup → config → testing → operations → security),
> every command explained in one line.
>
> **Deep dive:** see **[ARCHITECTURE.md](ARCHITECTURE.md)** for the full request
> flow, what every file does, the `/ask` router (fast keyword match + fallback) and the two handlers (`/query` vs
> `/investigate`), the three security walls, capabilities & limitations, and
> plain-language explanations of "fintech-grade" and "air-gapped".

---

## Table of contents

1. [What it does (in one paragraph)](#1-what-it-does-in-one-paragraph)
2. [The big picture (network flow)](#2-the-big-picture-network-flow)
3. [The request flow (inside the agent)](#3-the-request-flow-inside-the-agent)
4. [Security gates (every wall a request must pass)](#4-security-gates-every-wall-a-request-must-pass)
5. [The three-wall safety model](#5-the-three-wall-safety-model)
6. [Infrastructure map (every VM)](#6-infrastructure-map-every-vm)
7. [Repository layout](#7-repository-layout)
8. [Setup from scratch](#8-setup-from-scratch)
9. [Build and run the agent](#9-build-and-run-the-agent)
10. [Test it end to end](#10-test-it-end-to-end)
11. [Secrets: what never goes in git](#11-secrets-what-never-goes-in-git)
12. [Hardening backlog](#12-hardening-backlog)
13. [Troubleshooting](#13-troubleshooting)
14. [Glossary](#14-glossary)

---

## 1. What it does (in one paragraph)

A person on the internal network types a question. The question travels through a
VPN, a bastion (the single front door), and reaches the AI server. On the AI server
a small Python service (the agent) checks the caller's token, hands the question to
a local Ollama model, and gets back a structured guess at what the user wants, for
example "list pods in namespace kube-system". The agent then checks that guess
against an allowlist of read-only actions. If it is allowed, the agent runs the
real `kubectl` command through a reverse proxy to the cluster and returns the
result. If it is not allowed, it refuses and logs the attempt. The model only
*suggests*; it never *executes*. The code decides.

---

## 2. The big picture (network flow)

Each hop trusts only the hop before it. One port is open per hop. The reply comes
back on the same connection that was already established, so no extra inbound rules
are needed. If any single box is compromised, it cannot reach past its one allowed
neighbour. That is what "low blast radius" means.

```
   +-------------------+
   |  User's laptop    |   must be on the VPN (or Teleport browser ZTNA)
   |  WireGuard / ZTNA |
   +---------+---------+
             | encrypted tunnel (UDP 51820)
             v
   +-------------------+
   |  VPN server       |   proton  192.168.255.56
   |  WireGuard + PSK  |   edge gate: no tunnel, no access
   +---------+---------+
             | VPN subnet 10.98.99.0/24
             v
   +-------------------+
   |  Bastion          |   bastion  192.168.255.132
   |  the ONLY door    |   holds NO cluster credentials
   +---------+---------+
             | TCP 443 (agent) - allowed from bastion only
             v
   +------------------------------------------+
   |  AI server          ai-dev 192.168.255.30 |
   |  (Docker host, firewalld drop zone)       |
   |                                           |
   |   +-----------+        +--------------+   |
   |   |  agent    |  <-->  |   ollama     |   |   ollama has NO internet
   |   | FastAPI   |        | qwen2.5 q5   |   |   (internal-only network)
   |   +-----+-----+        +--------------+   |
   +---------+--------------------------------+
             | TCP 6443 (kube API) - allowed from AI server only
             v
   +-------------------+
   |  Reverse proxy/LB |   proxy  192.168.255.135
   |  nginx L4 stream  |   TCP passthrough, mTLS stays intact
   +---------+---------+
             | TCP 6443
             v
   +-------------------+
   |  Kind cluster     |   cluster  192.168.255.134
   |  k8s 1.34         |   read-only RBAC enforced here
   +-------------------+
```

Why the proxy is L4 (TCP passthrough) and not L7: the Kubernetes API uses client
certificate mTLS that must reach the API server untouched. If the proxy terminated
TLS (L7), it would break kubectl's certificate auth and expose plaintext in the
middle. L7 inspection is only ever used for ordinary app traffic, never for the
kube API.

---

## 2a. Two modes: simple vs investigate

The client posts every question to **`POST /ask`**, where a fast keyword check (no extra model call) routes it to one of two read-only handlers, falling back from query to investigate if a fact can't be mapped. Both handlers pass the same gates.

| Endpoint | For | What it does |
|----------|-----|--------------|
| `POST /ask` | the client's single entry point | A fast keyword check (no extra LLM call) routes to query or investigate; a fact the model can't map falls back to investigate so nothing is wrongly refused. |
| `POST /query` | technical users, one fact | One question becomes one read-only kubectl command. Returns raw output. |
| `POST /investigate` | anyone, "why did X break?" | The brain runs several read-only tools in a bounded loop, reads the results, and explains in plain English (what is happening, likely cause, suggested next step for a human). |

The investigate loop is a bounded think-act-observe cycle: the local model picks one
read-only tool at a time from a fixed catalog (`app/tools.py`), the loop runs it
through the policy gate and the read-only executor, feeds the real output back, and
repeats until the model has enough evidence or a hard cap is hit (max steps, output
size, wall-clock time in `config.yaml`). The model's final text is a suggestion for
a human; nothing it says is ever executed. Its behaviour is pinned by a strict
anti-hallucination system prompt (`app/OLLAMA_BRAIN.md`): answer only from observed
data, label guesses as guesses, never claim to have changed anything, and say "I
could not determine this" rather than invent.

The bastion client (`client/ask.py`) is a thin prompt box that posts every question to
`/ask`; the agent keyword-routes to query or investigate (with a fact->investigate fallback). The user just types a question.

## 3. The request flow (inside the agent)

This is what happens between the moment the agent receives a question and the moment
it answers. Each stage is a separate file in `app/` so each gate is small and
auditable.

```
  POST /query  {"q": "how many pods in kube-system"}
        |
        v
  [1] auth.py      check Bearer token. Fail-closed. Missing -> 401, wrong -> 403, stop.
        |
        v
  [2] llm.py       send the question to Ollama. Get back strict JSON:
        |          {"verb":"get","resource":"pods","namespace":"kube-system"}
        |          Bad/unparseable JSON -> treated as "not understood", stop.
        v
  [3] policy.py    THE GATE. Is this action on the read-only allowlist?
        |          (get / list / describe / logs / top only)
        |          Not on the list -> allowed:false, audit, "not allowed", stop.
        v
  [4] executor.py  run the real kubectl as an argv array (NO shell, no string
        |          concatenation, so nothing can be injected). Read-only only.
        v
  [5] redact.py    mask any secret-looking strings in the output (ReDoS-safe).
        v
  [6] counts.py    compute EXACT row counts in code (the LLM must not count),
        |          on the redacted output.
        v
  [7] audit.py     write a JSON line (who, question, action, allowed, result)
        |          to stdout, picked up by the host log pipeline / SIEM.
        v
  answer back to the caller
```

Key properties:

- The LLM output is never run directly. It is only data that `policy.py` inspects.
- `executor.py` builds `["kubectl", "get", "pods", "-n", "kube-system"]` as an
  argument list. There is no shell, so there is no command injection.
- Anything the policy does not explicitly allow is denied by default.
- A per-caller rate limit (slowapi) is a backstop; the real rate limiting belongs
  at nginx in front of the agent. Limits differ per endpoint: `/query` 10/minute,
  `/investigate` 6/minute (the loop is heavier).

---

## 4. Security gates (every wall a request must pass)

A request has to clear all of these, in order. Any one of them can stop it.

| #  | Gate                         | Where            | What it blocks |
|----|------------------------------|------------------|----------------|
| 1  | VPN / ZTNA                   | VPN server       | Anyone not on the tunnel. No tunnel, no route in. |
| 2  | WireGuard PresharedKey       | VPN server       | A second shared-secret key layered on top of the tunnel (defence in depth). |
| 3  | Bastion (single door)        | bastion          | Direct access to the AI server. Everything goes through one audited host. |
| 4  | firewalld drop zone          | every VM         | All inbound by default. Only named rich-rules per hop are allowed. |
| 5  | fail2ban (sshd + recidive)   | every VM         | Brute-force and repeat offenders. Admin subnet is in ignoreip. |
| 6  | Agent bearer token           | agent / auth.py  | Any caller without the correct token. Fails closed. |
| 7  | LLM JSON contract            | agent / llm.py   | Free-form or garbage input. Only strict JSON actions pass. |
| 8  | Policy allowlist             | agent / policy.py| Every verb that is not an explicit read-only action. |
| 9  | No-shell executor            | agent/executor.py| Command injection. kubectl is run as an argv array. |
| 10 | L4 proxy + conn limit        | proxy            | Direct access to the cluster node IP; caps 50 conns per source. |
| 11 | Kubernetes RBAC (read-only)  | cluster          | Every write verb and secrets/configmaps, even if gates 6-9 were bypassed. |
| 12 | Audit log                    | agent/audit.py   | Nothing. It records everything for detection and forensics. |

Host baseline present on every VM: firewalld (ingress default-deny), fail2ban,
auditd, Falco (modern eBPF kernel runtime security), SELinux enforcing, no root SSH,
key-based login with MFA.

Container hardening on the agent and ollama: non-root user (uid 1000), read-only
root filesystem, all Linux capabilities dropped, no-new-privileges, tmpfs for
`/tmp`, pids/memory/cpu limits, base image and kubectl pinned by digest/version.

---

## 5. The three-wall safety model

Three independent walls: **policy allowlist → executor read-only verb check → cluster RBAC**. All three must fail for any write, and no write-capable tool exists at all.

The Python allowlist (`policy.py`) and the Kubernetes RBAC role are two independent
walls. We keep both on purpose.

- **Wall 1, the allowlist**, stops bad requests *through the agent*. It is fast and
  specific and it catches almost everything, including most prompt-injection.
- **Wall 2, RBAC**, stops bad requests that go *around the agent*. If the kubeconfig
  leaks, if someone runs kubectl by hand, if the container is broken out of, or if
  `policy.py` is buggy or tampered with, RBAC still refuses every write.

The agent's cluster identity is granted only `get` / `list` / `watch`, and only on
the resources it needs. It can **never** read secrets, **never** read configmaps,
and **never** write anything. The RBAC role is a read-only **superset** of the
allowlist (it may permit more resources than `/query` exposes, all still get/list/
watch only); when you add a tool, confirm its resource is in the RBAC role.

Verify the RBAC wall directly:

```bash
kubectl auth can-i list pods     --as=ai-agent    # yes
kubectl auth can-i get secrets   --as=ai-agent    # no
kubectl auth can-i delete ns     --as=ai-agent    # no
```

---

## 6. Infrastructure map (every VM)

Test environment on VMware, subnet `192.168.255.0/24`.

| Role            | Host     | IP              | Key facts |
|-----------------|----------|-----------------|-----------|
| VPN server      | proton   | 192.168.255.56  | WireGuard, UDP 51820, VPN subnet 10.98.99.0/24, PresharedKey. Edge gate. |
| Bastion         | bastion  | 192.168.255.132 | WireGuard client (10.98.99.8), static IP. The only door to the AI server. Holds no cluster creds. |
| AI server       | ai-dev   | 192.168.255.30  | Docker host. Runs `agent` + `ollama`. firewalld drop zone; only SSH + 443 from bastion. |
| Reverse proxy   | proxy    | 192.168.255.135 | nginx L4 stream, 6443 passthrough to the cluster. 6443 allowed only from AI server. |
| Kind cluster    | cluster  | 192.168.255.134 | Kind v0.30, k8s 1.34.0. Read-only RBAC. API on 6443. Must be a static IP. |
| Monitoring      | monitor  | 192.168.255.79  | Prometheus + Loki + Grafana (OTel sink). ≥40 GB disk. See [docs/07-monitoring.md](docs/07-monitoring.md). |

Notes:
- The AI server is deliberately **not** a VPN client. It is firewall-locked to the
  bastion instead. Simpler and smaller attack surface.
- The ollama container is on an **internal-only** Docker network, so the model has
  no internet access at all.
- The admin kubeconfig stays on the cluster VM only and is never given to the agent.
  The agent gets its own short-lived, read-only client certificate.

Future production HA (not for dev/test): replace the single nginx proxy with
HAProxy + keepalived on a floating VIP across two proxy nodes, and run a
multi-master cluster, so neither the proxy nor the control plane is a single point
of failure.

---

## 7. Repository layout

```
AiAgents-Kubernetes-AIOps/
  README.md
  Dockerfile                 # multi-stage, non-root, kubectl pinned to v1.34.12
  docker-compose.yml         # agent + ollama, hardened, external nets/volume
  requirements.txt
  .gitignore                 # keeps kubeconfig / certs / tokens out of git
  app/
    main.py                  # FastAPI /ask (keyword router + fallback) + /query + /investigate
    auth.py                  # bearer-token check, constant-time, fail-closed
    llm.py                   # calls Ollama: route() + interpret() + loop next_step()/synthesize()
    policy.py                # THE allowlist: verb+resource AND tool-name gates
    executor.py              # runs kubectl as argv; hard read-only verb check
    tools.py                 # read-only tool registry for the investigate loop
    agent_loop.py            # bounded think-act-observe loop (hard caps)
    counts.py                # exact counts computed in code (not by the LLM)
    redact.py                # mask secret-looking output (both paths), ReDoS-safe
    memory.py                # optional RAG memory (recall/remember, fail-open)
    memory_store.py          # sqlite-vec vector store for RAG
    audit.py                 # JSON audit line to stdout
    schemas.py               # pydantic request/response/action/tool shapes
    OLLAMA_BRAIN.md          # strict brain system prompt (no hallucination)
  client/
    ask.py                   # bastion prompt box (plain-English front end)
    README.md                # how to install the client on the bastion
  k8s/
    kind-config.yaml         # 3-node Kind cluster, digest-pinned k8s 1.34
    rbac/
      serviceaccount.yaml    # ai-agent identity, no auto-mounted token
      clusterrole.yaml       # ai-readonly: get/list/watch only, never secrets
      clusterrolebinding.yaml # binds the role to the cert User "ai-agent"
  infra/
    nginx/stream.d/kube-api.conf   # L4 TCP passthrough config for the proxy VM
    monitoring/                    # Prometheus+Loki+Grafana compose + OTel config
    systemd/warm-models.service    # warm Ollama models on boot
  tests/                           # full pytest suite (incl. test_security_invariants.py)
  docs/                            # 00-START-HERE .. 11-rag-eval (step-by-step guides)
  config.yaml                      # ollama/model, allowlist, tools, investigation, rag
  CLAUDE.md  ARCHITECTURE.md  CHANGELOG.md
```

---

## 8. Setup from scratch

Order matters: cluster first, then the agent's credential, then the agent.

### 8.1 Create the Kind cluster (on the cluster VM)

Set the VM to a **static** IP first (DHCP drift breaks the Kind API bind). Then:

```bash
kind create cluster --config k8s/kind-config.yaml
kubectl get nodes          # control-plane + 2 workers, all Ready
```

### 8.2 Apply the read-only RBAC

```bash
kubectl apply -f k8s/rbac/serviceaccount.yaml
kubectl apply -f k8s/rbac/clusterrole.yaml
kubectl apply -f k8s/rbac/clusterrolebinding.yaml
```

### 8.3 Issue the agent's client certificate (mTLS, strongest option)

On the cluster VM. The agent gets a short-lived (30 day) client cert, not the admin
kubeconfig.

```bash
# 1. key + CSR (ECC P-256), identity CN=ai-agent
openssl ecparam -name prime256v1 -genkey -noout -out ai-agent.key
openssl req -new -key ai-agent.key -subj "/CN=ai-agent/O=aiops" -out ai-agent.csr

# 2. submit as a Kubernetes CertificateSigningRequest, approve, extract the cert
#    signerName: kubernetes.io/kube-apiserver-client, usages: client auth,
#    expirationSeconds: 2592000 (30d)
kubectl certificate approve ai-agent
# ... extract the signed cert into ai-agent.crt
```

The ClusterRoleBinding subject is `kind: User name: ai-agent` because certificate
auth identifies the caller as a **User**, not a ServiceAccount.

### 8.4 Build the agent kubeconfig

Point it at the **proxy**, not the node, and set `tls-server-name` to the node IP
(the cert SAN matches the node, not the proxy):

```yaml
clusters:
- cluster:
    server: https://192.168.255.135:6443      # the proxy
    tls-server-name: 192.168.255.134          # cert SAN = cluster node IP
    certificate-authority-data: <cluster CA>  # not secret; verifies the server
# users: client-certificate-data + client-key-data (the EC cert/key)
```

Test it before wiring it into the agent:

```bash
KUBECONFIG=agent-kubeconfig.yaml kubectl get pods -A     # works
KUBECONFIG=agent-kubeconfig.yaml kubectl delete ns test  # forbidden
```

### 8.5 Configure the reverse proxy (on the proxy VM)

Put `infra/nginx/stream.d/kube-api.conf` at `/etc/nginx/stream.d/kube-api.conf`,
and make sure `nginx.conf` has a **top-level** (outside `http{}`) block:

```nginx
stream { include /etc/nginx/stream.d/*.conf; }
```

Then `nginx -t && systemctl reload nginx`. Confirm it is listening:
`ss -tlpn | grep 6443`.

### 8.6 Place the kubeconfig on the AI server

```bash
sudo install -m 600 agent-kubeconfig.yaml /etc/ai-agent/kubeconfig
sudo chown 1000:1000 /etc/ai-agent/kubeconfig   # container runs as uid 1000
```

---

## 9. Build and run the agent

The recommended path is docker-compose, which runs the agent and ollama together
with all the hardening already set.

```bash
# networks and the ollama model volume are created out of band and are external
export AGENT_TOKEN="<your strong random token>"
export AGENT_KUBECONFIG=/etc/ai-agent/kubeconfig
export AGENT_BIND_IP=192.168.255.30

docker compose up -d --build
docker compose ps
```

What the compose file enforces:
- agent published on `192.168.255.30:443` so the bastion can reach it over the host
  NIC (the internal Docker networks are container-only and not reachable from the
  bastion).
- kubeconfig mounted read-only at `/app/kubeconfig`.
- `AGENT_TOKEN` is required; compose refuses to start without it.
- ollama pinned by digest, model `qwen2.5:7b-instruct-q5_K_M`, on an internal network with
  no internet.
- both containers: non-root, read-only rootfs, caps dropped, no-new-privileges,
  pids/memory/cpu limits.

Manual `docker run` is also possible (see the Dockerfile and compose file for the
exact flags) but compose is the supported way.

---

## 10. Test it end to end

From the bastion (the only host allowed to reach the agent):

```bash
curl -s http://192.168.255.30:443/query \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"q":"how many pods in kube-system"}'
```

Expected behaviour:

| You ask                              | What happens |
|--------------------------------------|--------------|
| "how many pods in kube-system"       | Returns the real count from the cluster. |
| "show logs for pod X"                | Returns logs (read-only, allowed). |
| "delete namespace test"              | `allowed:false`, refused and audited. |
| "hello"                              | `allowed:false`, "could not understand" (not a cluster question). |

The `Content-Type: application/json` header is required or FastAPI returns 422.
An empty result for "how many pods" in the default namespace is correct, the system
pods live in kube-system.

Current state: the agent speaks plain HTTP on port 443 (TLS is the next hardening
step). A bare list query with no namespace defaults to **all namespaces**
(cluster-wide); `default_namespace` applies only to named-object / describe / logs
calls. The client posts to `/ask` (keyword router + fallback); both `/query` (single command) and `/investigate` (multi-step loop) are
live.

---

## 11. Secrets: what never goes in git

These live only on the VMs and are blocked by `.gitignore`. Never commit them:

- the agent kubeconfig and any `*kubeconfig*` file
- client certificates and keys (`*.crt`, `*.key`, `*.pem`, CSRs)
- `AGENT_TOKEN` and any `.env` file
- WireGuard private keys and PresharedKeys

The admin kubeconfig never leaves the cluster VM. The CA certificate inside the
kubeconfig is **not** secret (it only verifies the server; we never use
insecure-skip-tls-verify).

Credential storage today: `/etc/ai-agent/kubeconfig`, mode 600, owned by the
container uid, read-only mount, disk encrypted at rest. Future: pull a short-lived
credential from OpenBao/Vault into memory at startup so nothing sensitive touches
disk.

---

## 12. Hardening backlog

In priority order. Items above the line are the near-term work.

1. **TLS on the agent** (it is plain HTTP today, the biggest gap), then mTLS so only
   the bastion's client cert can connect.
2. **Cut the AI server's internet** and egress-lock it to the proxy only (final
   air-gap).
3. **Block SSH on the AI server** (console access only, nobody logs in normally).
4. Wazuh agent shipping to a SIEM.
5. AIDE (file integrity) + ClamAV.
6. Rootless Docker.
7. Token/cert issued by OpenBao vault (short-lived) instead of an env var.
8. Rotate the 30-day Kubernetes client cert on schedule.
9. Resolve or formally risk-accept the HIGH CVEs in the ollama base image.
10. Make every IP static.

Client certs cannot be revoked in Kubernetes (there is no CRL), so the mitigation is
to keep them short-lived and rotate them, and to protect the key (mode 600).

Built (shipped): the investigate/analyse loop (`/investigate`) — a bounded
think-act-observe loop (read-only tool calls, size and time caps) that synthesises a
root cause and suggested fix as text only. Also shipped: deterministic counts,
secret redaction, optional RAG memory. See [CHANGELOG.md](CHANGELOG.md) for the full
list. Remaining backlog is the TLS/mTLS + Vault + SIEM items above.

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Bastion cannot reach the agent | agent bound to 127.0.0.1 | Publish on the host IP: `192.168.255.30:443:8080`. |
| `422 Unprocessable Entity` | missing content-type | Add `-H "Content-Type: application/json"`. |
| `403` from the agent | token mismatch | Same `AGENT_TOKEN` must be exported on the AI server and used by the caller. |
| kubeconfig "permission denied" | host file owned by root | `chown 1000:1000` the kubeconfig (container runs as uid 1000), keep mode 600. |
| x509 TLS verify fails to cluster | cert SAN is the node, not the proxy | Set `tls-server-name: 192.168.255.134` in the kubeconfig. |
| Kind create fails to bind | DHCP IP drift | Set the cluster VM to a static IP; `apiServerAddress` must match it. |
| nginx `unknown directive` | long comment wrapped onto a new line | Use the compact config from `infra/nginx/stream.d/kube-api.conf`. |
| fail2ban blocked your SSH | gateway/admin IP got banned | Add the admin subnet to `ignoreip` in `jail.local`. |
| Empty result for "how many pods" | default namespace has no pods | Ask about `kube-system`; this is correct behaviour. |

---

## 14. Glossary

- **AIOps**: using AI to operate infrastructure. Here, answering operational
  Kubernetes questions. (Not MLOps or LLMOps.)
- **Air-gapped**: no path to the internet. The model and all processing stay inside.
- **Allowlist**: the explicit list of actions that are permitted. Everything else is
  denied by default.
- **Bastion**: the single hardened host that is the only entry point to the
  protected network.
- **Blast radius**: how far an attacker can get if they compromise one box. We keep
  it small by letting each hop reach only its one neighbour.
- **Defence-in-depth**: many independent layers, so one failure does not mean a
  breach.
- **Kind**: Kubernetes-in-Docker, a cluster whose nodes are Docker containers.
- **L4 vs L7**: L4 forwards raw TCP (TLS stays sealed end to end); L7 reads the
  traffic (and would break kube API mTLS). We use L4 for the kube API.
- **mTLS**: both sides prove their identity with certificates.
- **RBAC**: Kubernetes role-based access control. Our role grants read-only, nothing
  more.
- **ZTNA**: zero-trust network access, for example Teleport, a browser-based way in
  with SSO and MFA and no client to install.
```
