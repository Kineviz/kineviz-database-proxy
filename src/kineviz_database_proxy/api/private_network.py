"""
Private Network Access support.

GraphXR is usually served over HTTPS from a public origin while the proxy runs on
``http://localhost``. That combination is allowed (localhost is a
potentially-trustworthy origin, so it is not blocked as mixed content), but Chrome
additionally sends a **Private Network Access** preflight for any public -> local
request, carrying ``Access-Control-Request-Private-Network: true``. A preflight
that comes back without ``Access-Control-Allow-Private-Network: true`` is rejected,
and the page sees an opaque ``TypeError: Failed to fetch``.

Older versions of ``CORSMiddleware`` do not emit that header, so it is added here
as a fallback. Newer versions also need ``allow_private_network=True`` in the CORS
configuration. The long ``Access-Control-Max-Age`` matters too: every request
carries ``X-API-Key``, which makes it non-simple, so without caching the browser
preflights each one.

See ai/graph-db-adapter-refactoring-spec.md section 8.5 in graphxr-dev.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

PRIVATE_NETWORK_REQUEST_HEADER = "access-control-request-private-network"
PRIVATE_NETWORK_ALLOW_HEADER = "Access-Control-Allow-Private-Network"

#: 10 minutes — Chrome's cap for a preflight cache entry.
PREFLIGHT_MAX_AGE_SECONDS = 600


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """Answers Chrome's private-network preflight and caches it."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        if request.method == "OPTIONS":
            # Only claim private-network access when the browser actually asked;
            # sending it unconditionally would be a claim about every request.
            if request.headers.get(PRIVATE_NETWORK_REQUEST_HEADER) == "true":
                response.headers[PRIVATE_NETWORK_ALLOW_HEADER] = "true"
            response.headers.setdefault("Access-Control-Max-Age", str(PREFLIGHT_MAX_AGE_SECONDS))

        return response
