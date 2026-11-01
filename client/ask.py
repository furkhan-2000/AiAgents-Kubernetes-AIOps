#!/usr/bin/env python3
"""ask - a simple prompt box for the Kubernetes AIOps agent.

Runs on the bastion. A user types a plain-English question and gets a plain-English
answer back from the agent. This is a thin, dumb front end: it holds no cluster
access and makes no security decisions. All trust lives upstream (SSH + MFA on the
bastion) and downstream (agent token, policy allowlist, read-only RBAC).

Setup (see client/README.md):
  1. copy this to /opt/ai-agent/ask.py
  2. put the token in ~/.config/ai-agent/token  (chmod 600)
  3. symlink /usr/local/bin/ask -> /opt/ai-agent/ask.py

Config via environment (sensible defaults):
  AGENT_URL        default http://192.168.255.30:443
  AGENT_TOKEN_FILE default ~/.config/ai-agent/token
"""

import json
import os
import socket
import sys
import urllib.request
import urllib.error

AGENT_URL = os.environ.get("AGENT_URL", "http://192.168.255.30:443").rstrip("/")
TOKEN_FILE = os.path.expanduser(
    os.environ.get("AGENT_TOKEN_FILE", "~/.config/ai-agent/token")
)
# Investigations run several LLM calls on a CPU model, so allow generous time.
TIMEOUT = int(os.environ.get("AGENT_CLIENT_TIMEOUT", "420"))

BANNER = (
    "Kubernetes assistant. Ask about the cluster in plain English.\n"
    "Examples: 'why is the api pod restarting in prod', 'how many pods in kube-system'.\n"
    "Type 'exit' or press Ctrl-D to quit.\n"
)


def _load_token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
    except FileNotFoundError:
        sys.exit(f"No token found at {TOKEN_FILE}. Ask an admin to provision it (chmod 600).")
    except PermissionError:
        sys.exit(f"Cannot read {TOKEN_FILE}. Check file permissions (should be 600, owned by you).")
    if not token:
        sys.exit(f"Token file {TOKEN_FILE} is empty.")
    return token


# Questions that need multi-step reasoning (logs/events/why) go to /investigate;
# simple factual lookups go to /query (faster, exact).
_INVESTIGATE_HINTS = (
    "why", "what went wrong", "went wrong", "fail", "failed", "crash", "crashing",
    "broken", "error", "debug", "investigate", "diagnose", "root cause", "logs",
    "restart", "not working", "issue", "problem",
)


def _route(question: str) -> str:
    q = question.lower()
    return "/investigate" if any(h in q for h in _INVESTIGATE_HINTS) else "/query"


def _ask(question: str, token: str) -> None:
    endpoint = _route(question)
    body = json.dumps({"q": question}).encode("utf-8")
    req = urllib.request.Request(
        f"{AGENT_URL}{endpoint}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print("  Access denied. Your token is missing or invalid.\n")
        elif exc.code == 429:
            print("  Too many questions in a short time. Please wait a moment.\n")
        elif exc.code == 422:
            print("  Your question was empty or too long. Please shorten it.\n")
        else:
            print(f"  The agent returned an error (HTTP {exc.code}).\n")
        return
    except socket.timeout:
        print("  Still thinking... this question took too long on the CPU model. "
              "Try a simpler/narrower question, or increase AGENT_CLIENT_TIMEOUT.\n")
        return
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), socket.timeout):
            print("  Still thinking... it took too long on the CPU model. "
                  "Try a narrower question, or raise AGENT_CLIENT_TIMEOUT.\n")
        else:
            print("  Could not reach the agent. Check that you are on the bastion and it is up.\n")
        return
    except KeyboardInterrupt:
        # Ctrl-C while waiting: abandon this question, return to the prompt cleanly.
        print("\n  Cancelled.\n")
        return
    except (ValueError, OSError):
        print("  The agent sent an unreadable response. Please try again.\n")
        return

    if not isinstance(data, dict):
        print("  The agent sent an unexpected response. Please try again.\n")
        return
    # /investigate returns "answer"; /query returns "output"; errors use "message".
    answer = data.get("answer") or data.get("output") or data.get("message") or "(no answer returned)"
    print(f"\n{answer}\n")


def main() -> None:
    token = _load_token()
    print(BANNER)
    while True:
        try:
            question = input("ask> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            break
        print("  ...looking at the cluster...")
        _ask(question, token)


if __name__ == "__main__":
    main()
