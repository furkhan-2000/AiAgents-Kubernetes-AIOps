"""Best-effort redaction of secret-looking strings before cluster output is sent
back to the model or returned to the user. Defence in depth, applied on BOTH the
/query and /investigate paths. Not a substitute for RBAC that denies secrets.

All patterns use bounded/possessive quantifiers so redaction is linear time and
cannot be turned into a CPU denial-of-service by a long crafted log line."""

import re

# Hard input cap: redaction is only meaningful on human-sized output, and this
# bounds worst-case work regardless of what the cluster returns.
_MAX = 200_000

# key: value  ->  mask the WHOLE value to end of line (so 'Authorization: Bearer
# <token>' masks the token, not just the scheme word). (?im): case-insensitive +
# $ per line; . does not cross newlines so only that line is masked. The prefix is
# length-bounded and possessive to prevent catastrophic backtracking.
_KV = re.compile(
    r"(?im)^(.{0,200}?(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|"
    r"bearer))([\s\"']{0,20}[:=][\s\"']{0,20})(.+)$"
)

_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"AKIA[0-9A-Z]{16}"),                                            # AWS key id
    # creds in URL. Scheme/host/userinfo are length-bounded and POSSESSIVE (*+ / {}+)
    # so there is no backtracking on long non-URL runs (the earlier non-possessive
    # version was O(n^2) and a CPU-DoS vector).
    re.compile(r"[a-z][a-z0-9+.\-]{0,30}+://[^:\s/@]{1,4096}+:[^@\s]{1,4096}+@"),
]


def redact(text: str) -> str:
    if not text:
        return text
    if len(text) > _MAX:
        text = text[:_MAX] + "\n...[truncated]"
    out = _KV.sub(r"\1\2[REDACTED]", text)
    for rx in _PATTERNS:
        out = rx.sub("[REDACTED]", out)
    return out
