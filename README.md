# K8s AI Agent

An offline agent that turns a plain question ("how many pods are running?") into a
safe, read-only kubectl call. The LLM (Ollama) only suggests an action; the policy
allowlist decides what is allowed to run. Writes and deletes are never permitted.

## Flow
```
bastion -> /query -> auth -> llm (Ollama) -> policy (allowlist, read-only)
       -> allowed -> executor (kubectl) -> audit -> answer
       -> denied  -> audit -> "not allowed"
```

## Structure
```
ai-agent/
  Dockerfile
  requirements.txt
  config.yaml          # ollama url, cluster, the allowlist
  app/
    main.py            # FastAPI /query endpoint
    auth.py            # bearer token check (bastion only)
    llm.py             # calls Ollama, text -> JSON action
    policy.py          # allowlist + validation (the safety core)
    executor.py        # runs the approved kubectl
    audit.py           # JSON audit log to stdout
    schemas.py         # request/response/action shapes
```

## Build
```
docker build -t agent:1.0 ./ai-agent
```

## Run (two NICs, kubeconfig, hardened)
```
docker run -d --name agent --network AI-Internal \
  -e OLLAMA_URL=http://ollama:11434 \
  -e OLLAMA_MODEL=qwen2.5:7b-instruct \
  -e AGENT_TOKEN="$(cat /path/to/token)" \
  -v /path/to/kubeconfig:/app/kubeconfig:ro \
  -p 127.0.0.1:8080:8080 \
  --user 1000:1000 --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 256 --memory 1g --cpus 1 \
  --restart unless-stopped \
  agent:1.0

# add the second network so it can reach the cluster
docker network connect AI-Agent agent

# confirm both networks are attached
docker inspect agent --format '{{json .NetworkSettings.Networks}}'
```

## Test (from the bastion, or locally)
```
curl -s http://127.0.0.1:8080/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"q":"how many pods are running in stg"}'
```

## Firewalld (precise allow-list, Docker cannot do this)
```
# Agent: accept only from the bastion IP on 8080
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=<BASTION_IP> port port=8080 protocol=tcp accept'
# Egress: allow only Ollama (11434) and the cluster API (6443), deny the rest
# (implement as direct/egress rules per your firewalld setup)
```

## Notes
- The LLM never executes anything. policy.py is the gate.
- kubeconfig must map to a least-privilege, read-only RBAC service account.
- Audit lines go to stdout -> collected by the host (Falco / Wazuh / SIEM).
- For production: pin the base image and kubectl by digest, scan with Trivy.
