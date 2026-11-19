"""Bound request bodies before multipart parsing, including chunked uploads."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

MIB = 1024 * 1024
UPLOAD_BODY_LIMITS = {
    "/convert": 10 * MIB + 128 * 1024,
    "/conversions/upload": 10 * MIB + 128 * 1024,
    "/api/conversions/upload": 10 * MIB + 128 * 1024,
    "/reconcile": 20 * MIB + 128 * 1024,
    "/contact": 2 * MIB + 128 * 1024,
}


class UploadBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limit = UPLOAD_BODY_LIMITS.get(scope.get("path", "").rstrip("/"))
        if scope["type"] != "http" or scope.get("method") != "POST" or limit is None:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > limit:
            return await JSONResponse({"detail": "Upload exceeds the maximum request size."}, status_code=413)(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise HTTPException(413, "Upload exceeds the maximum request size.")
            return message

        return await self.app(scope, bounded_receive, send)
