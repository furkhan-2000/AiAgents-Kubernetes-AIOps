# mTLS front-door (nginx) for the agent

Adds an nginx container in front of the agent that terminates TLS and **requires a
client certificate** signed by our CA. The bastion presents its client cert; nothing
without a valid cert reaches the agent. The agent stays plain HTTP on the internal
network; Ollama is untouched (already caged, no internet).

```
bastion --mTLS--> [nginx tls-proxy] --http--> agent --> ollama (internal)
```

Files here:
- `nginx.conf` — the proxy config (TLS + `ssl_verify_client on` = mTLS).
- `docker-compose.override.yml` — the nginx service; copy to repo root on ai-dev.

Certificates are NOT in git (`.gitignore` blocks `*.key`/`*.crt`). Generate them on
the server as below.

## 1. Generate certs (ON ai-dev, one time)
```bash
mkdir -p ~/certs && cd ~/certs
# CA (the trust anchor; ca.key never leaves this box)
openssl req -x509 -newkey rsa:4096 -keyout ca.key -out ca.crt -days 3650 -nodes -subj "/CN=ai-CA"
# Server cert for nginx — SAN MUST be the private IP the bastion dials
openssl req -newkey rsa:4096 -keyout server.key -out server.csr -nodes -subj "/CN=ai-dev" \
  -addext "subjectAltName=IP:192.168.255.30"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 825 \
  -extfile <(printf "subjectAltName=IP:192.168.255.30")
# Client cert for the bastion
openssl req -newkey rsa:4096 -keyout client.key -out client.csr -nodes -subj "/CN=bastion"
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt -days 825
chmod 600 *.key
```

## 2. Bring up the proxy (ON ai-dev)
```bash
cd ~/AiAgents-Kubernetes-AIOps
git pull
cp infra/tls/docker-compose.override.yml ./docker-compose.override.yml
# certs default to /home/dev/certs; override with: export CERT_DIR=~/certs
docker compose up -d
docker ps | grep tls-proxy        # Up
```

## 3. Copy the client set to the bastion
```bash
scp ~/certs/{client.crt,client.key,ca.crt} bastion@192.168.255.132:~/certs/
```

## 4. Point the client at mTLS (ON the bastion)
```bash
chmod 600 ~/certs/client.key
export AGENT_URL="https://192.168.255.30:443"
export AGENT_CLIENT_CERT=~/certs/client.crt
export AGENT_CLIENT_KEY=~/certs/client.key
export AGENT_CA_CERT=~/certs/ca.crt
# (persist these in ~/.bashrc or the ask wrapper)
```
`client/ask.py` auto-detects the https URL + cert env vars and connects over mTLS.
Without them it stays plain HTTP (unchanged).

## 5. Verify
```bash
# no cert -> rejected (proves mTLS):
curl -sk https://192.168.255.30/healthz; echo        # 400 No required SSL certificate
# with cert -> ok:
curl -s --cert ~/certs/client.crt --key ~/certs/client.key --cacert ~/certs/ca.crt \
  https://192.168.255.30/healthz; echo               # {"status":"ok"}
# then:
ask    # should work over mTLS
```

## Rules
- `ca.key` and `server.key` NEVER leave ai-dev. Only `client.crt`, `client.key`,
  `ca.crt` go to the bastion.
- Never commit certs (`.gitignore` already blocks them).
- Certs expire in 825 days — set a renewal reminder. To renew, re-run the client/
  server `x509 -req` steps against the same CA (no need to regenerate the CA).
- The agent token stays too: mTLS authenticates the *machine*; the Bearer token still
  authenticates the *request*. Defense in depth — keep both.
