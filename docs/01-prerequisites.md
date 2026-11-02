# 1. Prerequisites

What you need before setup. Read, then go to [02-setup.md](02-setup.md).

## The machines (VMs, subnet 192.168.255.0/24)

| Role | Host | IP | What runs on it |
|------|------|-----|-----------------|
| VPN server | proton | .56 | WireGuard (the edge gate) |
| Bastion | bastion | .132 | the only door in; runs the `ask` client |
| AI server | ai-dev | .30 | Docker: the agent + Ollama (the brain) |
| Reverse proxy | proxy | .135 | nginx L4 passthrough to the cluster |
| Cluster | cluster | .134 | Kind Kubernetes (k8s 1.34) |

Each VM: 2 vCPU / 4 GB is fine, **except the AI server: 6 vCPU / 12 GB** (the LLM
is heavy). Static IPs. Ubuntu 24.04.

## Tools to install

**AI server (ai-dev):**
```bash
# Docker + compose plugin (runs the agent and Ollama)
curl -fsSL https://get.docker.com | sh
```

**Cluster VM:**
```bash
# kubectl — talks to Kubernetes
curl -fsSL https://dl.k8s.io/release/v1.34.12/bin/linux/amd64/kubectl -o /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl
# kind — runs Kubernetes inside Docker
curl -fsSL https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64 -o /usr/local/bin/kind && chmod +x /usr/local/bin/kind
```

**Bastion:** just `python3` (for the `ask` client) — already on Ubuntu.

## Network rules (firewalld, default-deny)
- Bastion → AI server: allow TCP **443** only.
- AI server → proxy: allow TCP **6443** only.
- Proxy → cluster: allow TCP **6443** only.
- Everything else: denied.

## One secret you will create
`AGENT_TOKEN` — a long random string the bastion sends and the agent checks.
```bash
openssl rand -hex 32     # generates a strong token; keep it safe
```
