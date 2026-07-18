"""
API Gateway Service — entry point for all payment requests.

Responsibilities:
- Extract tenant identity from headers
- Set business context for downstream tracing
- Rate limit by tenant tier
- Route to payment-service
"""

import uuid
import time
import logging
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from opentelemetry import trace

import sys
sys.path.insert(0, "/app")
from instrumentation import init_telemetry, set_business_context, PaymentMetrics
from instrumentation.setup import instrument_fastapi

init_telemetry("api-gateway")
app = FastAPI(title="API Gateway")
instrument_fastapi(app)

tracer = trace.get_tracer("api-gateway", "1.0.0")
logger = logging.getLogger("api-gateway")
payment_metrics = PaymentMetrics()

PAYMENT_SERVICE_URL = "http://payment-service:8001"

# Rate limits per tier (requests per minute)
RATE_LIMITS = {"enterprise": 1000, "pro": 200, "free": 30}
_request_counts: dict[str, list] = {}


def check_rate_limit(tenant_id: str, tier: str) -> bool:
    now = time.time()
    key = f"{tenant_id}:{tier}"
    if key not in _request_counts:
        _request_counts[key] = []

    # Prune old entries
    _request_counts[key] = [t for t in _request_counts[key] if t > now - 60]
    _request_counts[key].append(now)

    limit = RATE_LIMITS.get(tier, 30)
    return len(_request_counts[key]) <= limit


@app.post("/api/v1/payments")
async def create_payment(request: Request):
    # Extract tenant from headers
    tenant_id = request.headers.get("X-Tenant-ID", "unknown")
    tenant_tier = request.headers.get("X-Tenant-Tier", "free")

    body = await request.json()
    transaction_value = body.get("amount", 0.0)
    transaction_type = body.get("type", "payment")
    transaction_id = str(uuid.uuid4())

    with set_business_context(
        tenant_id=tenant_id,
        tenant_tier=tenant_tier,
        transaction_id=transaction_id,
        transaction_value=transaction_value,
        transaction_type=transaction_type,
    ):
        # Authenticate
        with tracer.start_as_current_span("gateway.authenticate") as span:
            span.set_attribute("auth.method", "api_key")
            span.set_attribute("auth.tenant_id", tenant_id)
            # Simulated auth check
            if tenant_id == "unknown":
                span.set_status(trace.StatusCode.ERROR, "Missing tenant ID")
                raise HTTPException(status_code=401, detail="Missing X-Tenant-ID header")

        # Rate limit check
        with tracer.start_as_current_span("gateway.rate_limit") as span:
            span.set_attribute("rate_limit.tier", tenant_tier)
            span.set_attribute("rate_limit.max_rpm", RATE_LIMITS.get(tenant_tier, 30))
            if not check_rate_limit(tenant_id, tenant_tier):
                span.set_attribute("rate_limit.exceeded", True)
                logger.warning(
                    "Rate limit exceeded",
                    extra={"tenant_id": tenant_id, "tier": tenant_tier},
                )
                payment_metrics.payment_error_count.add(1, {
                    "tenant.id": tenant_id,
                    "tenant.tier": tenant_tier,
                    "error.type": "rate_limited",
                })
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            span.set_attribute("rate_limit.exceeded", False)

        # Route to payment service
        with tracer.start_as_current_span("gateway.route") as span:
            span.set_attribute("http.route", "/api/v1/payments")
            span.set_attribute("destination.service", "payment-service")

            payment_metrics.active_transactions.add(1, {"tenant.tier": tenant_tier})
            start = time.time()

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{PAYMENT_SERVICE_URL}/process",
                        json={
                            "transaction_id": transaction_id,
                            "tenant_id": tenant_id,
                            "tenant_tier": tenant_tier,
                            "amount": transaction_value,
                            "type": transaction_type,
                            "currency": body.get("currency", "USD"),
                            "card_last4": body.get("card_last4", "4242"),
                        },
                        headers={
                            "X-Tenant-ID": tenant_id,
                            "X-Tenant-Tier": tenant_tier,
                            "X-Transaction-ID": transaction_id,
                        },
                    )

                duration_ms = (time.time() - start) * 1000
                payment_metrics.active_transactions.add(-1, {"tenant.tier": tenant_tier})

                success = response.status_code == 200
                payment_metrics.record_payment(
                    duration_ms=duration_ms,
                    value=transaction_value,
                    tenant_tier=tenant_tier,
                    transaction_type=transaction_type,
                    success=success,
                    tenant_id=tenant_id,
                )

                if not success:
                    logger.error(
                        "Payment failed",
                        extra={
                            "tenant_id": tenant_id,
                            "transaction_id": transaction_id,
                            "status_code": response.status_code,
                            "amount": transaction_value,
                        },
                    )
                    span.set_status(trace.StatusCode.ERROR, f"Payment failed: {response.status_code}")
                    return JSONResponse(
                        status_code=response.status_code,
                        content=response.json(),
                    )

                logger.info(
                    "Payment processed",
                    extra={
                        "tenant_id": tenant_id,
                        "transaction_id": transaction_id,
                        "amount": transaction_value,
                        "duration_ms": round(duration_ms, 2),
                    },
                )
                return response.json()

            except httpx.TimeoutException:
                duration_ms = (time.time() - start) * 1000
                payment_metrics.active_transactions.add(-1, {"tenant.tier": tenant_tier})
                payment_metrics.record_payment(
                    duration_ms=duration_ms,
                    value=transaction_value,
                    tenant_tier=tenant_tier,
                    transaction_type=transaction_type,
                    success=False,
                    tenant_id=tenant_id,
                )
                logger.error(
                    "Payment service timeout",
                    extra={"tenant_id": tenant_id, "transaction_id": transaction_id},
                )
                span.set_status(trace.StatusCode.ERROR, "Timeout")
                raise HTTPException(status_code=504, detail="Payment service timeout")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "api-gateway"}
