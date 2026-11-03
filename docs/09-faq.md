# 9. FAQ & Troubleshooting

"I see X → do Y." The one page to check when something breaks.

## Agent

| Problem | Why | Fix |
|---------|-----|-----|
| `invalid token` / 403 | token mismatch | same token in `.env` (AI server) and `~/.config/ai-agent/token` (bastion) |
| `422` error | question empty or too long | shorten it |
| Bastion can't reach agent | bound to localhost | publish on the host IP `192.168.255.30:443` |
| `/investigate` "took too long" | CPU model is slow / cold | warm the model; raise `AGENT_CLIENT_TIMEOUT` |
| `loop.plan_error: timed out` | one model call too slow on CPU | warm model; it still answers |
| Answer in another language | model drift | brain prompt forces English; redeploy latest |
| kubeconfig "permission denied" | wrong owner | `chown 1000:1000` the kubeconfig, mode 600 |
| "not a question about the cluster" | correct refusal (off-topic) | not a bug — ask a cluster question |
| Attack prompt gets a normal read | correct — it's read-only | the walls held; nothing was changed |

## Monitoring

| Problem | Why | Fix |
|---------|-----|-----|
| All VMs' metrics merged, no host label | `prometheusremotewrite` drops resource attrs | add `resource_to_telemetry_conversion: enabled: true` |
| Collector runs the demo "debug" config | restart didn't apply the new file | `validate` then `systemctl restart`; log should show your receivers |
| `permission denied /var/log/syslog` | collector is non-root | `sudo usermod -aG adm otelcol-contrib` |
| `container_*` metrics empty | `docker_stats` not added / no socket access | add the receiver + `sudo usermod -aG docker otelcol-contrib` |
| `localhost:19090` empty | ports bound to the LAN IP | query via `192.168.255.79:19090` |
| firewalld "drop" but port still open | Docker bypasses firewalld | use `DOCKER-USER` iptables rules |
| Loki `connection refused` / crash | monitoring disk full | grow the VM disk to ≥40 GB |
| Grafana log panel empty | data source still Prometheus | switch the panel's data source to **loki** |
| Image pull fails on air-gapped VM | no internet | pull once with internet, then disconnect |

## Quick checks

```bash
# agent healthy?
docker compose ps && docker compose logs --tail=20 agent

# collector healthy (any VM)?
systemctl is-active otelcol-contrib
journalctl -u otelcol-contrib -n 20 --no-pager | grep -iE "error|denied"

# are all VMs reporting? (monitoring VM)
curl -s 'http://192.168.255.79:19090/api/v1/query?query=system_memory_usage_bytes' \
  | grep -o '"host_name":"[^"]*"' | sort -u

# monitoring stack healthy?
curl -s http://192.168.255.79:19090/-/healthy   # Healthy
curl -s http://192.168.255.79:13100/ready        # ready
```

## Golden rules (never break)
- Never add a tool/verb that can **change** the cluster — read-only only.
- Never commit secrets (token, kubeconfig, certs, `.env`).
- Always **back up + validate** a config before restarting a service.
- On CPU, the LLM is **slow by design** — that's the price of offline/private.
