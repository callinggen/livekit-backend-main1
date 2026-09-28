import time
import uuid
import logging
from typing import Dict, List, Tuple
from collections import defaultdict
from fastapi import Request, Response, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("callinggen.middleware")


class RequestIdAndTracingMiddleware(BaseHTTPMiddleware):
    """
    Enterprise request tracking and latency measurement middleware.
    Assigns or preserves a unique X-Request-ID for distributed tracing across
    the API, worker, and voice SDR agent.
    """
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                f"[ERROR] [{req_id}] {request.method} {request.url.path} failed in {duration_ms:.2f}ms: {exc}",
                exc_info=True
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "An internal server error occurred.",
                    "request_id": req_id,
                },
                headers={
                    "X-Request-ID": req_id,
                    "X-Response-Time": f"{duration_ms:.2f}ms",
                }
            )

        duration_ms = (time.perf_counter() - start_time) * 1000
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

        # Log non-healthcheck requests or slow requests (>500ms)
        if request.url.path not in ("/api/health", "/") or duration_ms > 500:
            logger.info(
                f"[ACCESS] [{req_id}] {request.method} {request.url.path} -> {response.status_code} ({duration_ms:.2f}ms)"
            )

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    OWASP Enterprise Security Headers Middleware.
    Enforces browser defense against MIME sniffing, clickjacking, and XSS.
    """
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        # 1. Block MIME sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # 2. Block clickjacking / embedding in malicious iframes
        response.headers["X-Frame-Options"] = "SAMEORIGIN"

        # 3. Enable legacy browser XSS filters
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # 4. Strict referrer policy to protect user privacy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # 5. HSTS (enforced when request arrives over HTTPS or behind TLS terminator)
        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").lower() == "https"
        )
        if is_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response


class RateLimiter:
    """
    In-memory sliding window rate limiter.
    Provides protection against credential stuffing, brute-force attacks,
    and API flood attempts without requiring an external Redis dependency.
    """
    def __init__(self):
        # Maps client_key -> list of timestamp floats
        self._history: Dict[str, List[float]] = defaultdict(list)
        self._last_pruned = time.time()

    def _get_client_ip(self, request: Request) -> str:
        # Check standard reverse proxy headers
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        return request.client.host if request.client else "127.0.0.1"

    def _prune(self, now: float):
        # Periodically purge entries older than 5 minutes to prevent memory growth
        if now - self._last_pruned > 60:
            keys_to_delete = []
            cutoff = now - 300
            for key, timestamps in self._history.items():
                self._history[key] = [t for t in timestamps if t > cutoff]
                if not self._history[key]:
                    keys_to_delete.append(key)
            for k in keys_to_delete:
                del self._history[k]
            self._last_pruned = now

    def check(self, request: Request, max_requests: int = 15, window_seconds: int = 60) -> Tuple[bool, int, int]:
        """
        Validates request against rate limit.
        Returns: (is_allowed, remaining, retry_after)
        """
        now = time.time()
        self._prune(now)

        ip = self._get_client_ip(request)
        key = f"{request.url.path}:{ip}"
        cutoff = now - window_seconds

        timestamps = [t for t in self._history[key] if t > cutoff]
        self._history[key] = timestamps

        if len(timestamps) >= max_requests:
            oldest = timestamps[0]
            retry_after = max(1, int(window_seconds - (now - oldest)))
            return False, 0, retry_after

        self._history[key].append(now)
        remaining = max_requests - len(self._history[key])
        return True, remaining, 0


# Global rate limiter instance
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware applying targeted rate limits to sensitive authentication routes.
    """
    RATE_LIMITED_ROUTES = {
        "/api/auth/login": (15, 60),            # 15 attempts / min
        "/api/auth/register": (10, 60),         # 10 registrations / min
        "/api/auth/forgot-password": (5, 60),   # 5 password resets / min
    }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "POST" and path in self.RATE_LIMITED_ROUTES:
            max_req, window = self.RATE_LIMITED_ROUTES[path]
            allowed, remaining, retry_after = rate_limiter.check(request, max_requests=max_req, window_seconds=window)
            if not allowed:
                req_id = getattr(request.state, "request_id", "unknown")
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "detail": f"Rate limit exceeded. Please wait {retry_after} seconds before trying again.",
                        "retry_after": retry_after,
                        "request_id": req_id,
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "RateLimit-Limit": str(max_req),
                        "RateLimit-Remaining": "0",
                    }
                )

        return await call_next(request)
