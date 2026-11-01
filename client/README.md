# `ask` - bastion client for the Kubernetes AIOps agent

A simple prompt box. The user types a plain-English question, the agent
investigates (read-only) and answers in plain English. This client holds no
cluster access and makes no security decisions. It only sends the question to the
agent and prints the reply.

## Why it is safe

- No new network port, no web server, no second login system.
- It rides on the bastion's existing SSH + MFA + session recording.
- The token is read from a per-user 600 file at runtime, never hardcoded, never in
  git.
- All real security stays upstream (bastion) and downstream (agent token, policy
  allowlist, read-only RBAC). This script is a dumb front end.

## Install on the bastion

```bash
# 1. place the client
sudo install -D -m 0755 ask.py /opt/ai-agent/ask.py

# 2. give each user their token (one file per user, locked down).
#    Use `read -s` so the token is NOT written to shell history or the screen.
mkdir -p ~/.config/ai-agent && chmod 700 ~/.config/ai-agent
( umask 077; read -rs -p 'Paste agent token: ' T && printf '%s' "$T" > ~/.config/ai-agent/token ); echo

# 3. make it a one-word command
sudo ln -sf /opt/ai-agent/ask.py /usr/local/bin/ask
```

Now a user just runs:

```bash
ask
```

## Start modes (pick one)

| Mode | How | Best for |
|------|-----|----------|
| On-demand (default) | User types `ask` when they want it. | Users who also need the shell. |
| Auto-start on login | Add `ask` to the user's `~/.bash_profile`. | Users who should only ask questions. |
| Kiosk / locked | Set `ForceCommand /usr/local/bin/ask` in `sshd_config` for that user/group. | Non-technical users locked to just this tool. |

## Configuration (optional)

Environment variables, with defaults:

| Variable | Default | Meaning |
|----------|---------|---------|
| `AGENT_URL` | `http://192.168.255.30:443` | Agent endpoint (HTTP today; HTTPS after TLS hardening). |
| `AGENT_TOKEN_FILE` | `~/.config/ai-agent/token` | Where the per-user token is read from. |
| `AGENT_CLIENT_TIMEOUT` | `120` | Seconds to wait for an answer. |

## Example

```
ask> why is the web pod restarting in prod
  ...looking at the cluster...

What is happening: the pod web-7c9f in namespace prod is in CrashLoopBackOff
with 12 restarts.

Most likely cause (likely, not confirmed): the container logs show it cannot
connect to the database at db:5432, which usually means the database service is
down or the connection settings are wrong.

Suggested next step for a human: check that the 'db' service and its pods are
healthy in the prod namespace, and confirm the database host and port settings.
```
