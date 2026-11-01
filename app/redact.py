"""Best-effort redaction of secret-looking strings before cluster output is sent
back to the model or returned to the user. Defence in depth, applied on BOTH the
/query and /investigate paths. Not a substitute for RBAC that denies secrets."""

import re

# key: value  ->  redact the WHOLE value to end of line (so 'Authorization: Bearer
# <token>' masks the token, not just the scheme word). (?im): case-insensitive +
# $ anchors per line; . does not cross newlines, so only the one line is masked.
_KV = re.compile(
    r"(?im)^(.*?(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|"
    r"bearer))(\s*[:=]\s*)(.+)$"
)

_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"AKIA[0-9A-Z]{16}"),                                            # AWS key id
    re.compile(r"[a-z][a-z0-9+.\-]*://[^:\s/@]+:[^@\s]+@"),                     # creds in URL
]


def redact(text: str) -> str:
    if not text:
        return text
    out = _KV.sub(r"\1\2[REDACTED]", text)
    for rx in _PATTERNS:
        out = rx.sub("[REDACTED]", out)
    return out
