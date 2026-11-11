"""Conditional PRO reads keep the existing full response as the compatibility path."""
from fastapi import Response

from app.routers import canonical_pro


def unchanged_workspace(repository, response, validator, **scope):
    revision = getattr(repository, "workspace_revision", None)
    if not callable(revision) or canonical_pro.get_canonical_pro_service() is None:
        return None
    etag = revision(**scope)
    if etag is None:
        return None
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    # Only accept our explicit validator, never '*' (no cached representation).
    if validator == etag:
        return Response(status_code=304, headers=dict(response.headers))
    return None
