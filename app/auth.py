"""Caller authentication. Only requests carrying the shared token (issued to the
bastion) are accepted. In production, replace or combine this with mTLS."""

import os
import hmac
from fastapi import Header, HTTPException

# The token is injected via environment variable from a vault at runtime.
_EXPECTED = os.environ.get("AGENT_TOKEN", "")


def verify(authorization: str = Header(default="")) -> str:
    """Validate 'Authorization: Bearer <token>'. Returns a caller id on success."""
    if not _EXPECTED:
        # Fail closed: if no token is configured, reject everything.
        raise HTTPException(status_code=503, detail="agent not configured")

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization[len(prefix):]
    # Constant-time compare to avoid timing attacks.
    if not hmac.compare_digest(token, _EXPECTED):
        raise HTTPException(status_code=403, detail="invalid token")

    return "bastion"
