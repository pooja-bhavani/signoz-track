"""
Payment Service — processes payments with multi-step pipeline.

Pipeline: validate → fraud check (for high-value) → charge → confirm.
Each step is a custom span with business context propagated.
"""

import time
import random
import logging
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace

import sys
sys.path.insert(0, "/app")
from instrumentation import init_telemetry, set_business_context, PaymentMetrics, SLOCalculator
from instrumentation.setup import instrument_fastapi

init_telemetry("payment-service")
app = FastAPI(title="Payment Service")
instrument_fastapi(app)

tracer = trace.get_tracer("payment-service", "1.0.0")
logger = logging.getLogger("payment-service")
payment_metrics = PaymentMetrics()
slo_calculator = SLOCalculator()

FRAUD_SERVICE_URL = "http://fraud-detection:8002"
FRAUD_THRESHOLD = 500.0  # Check fraud for transactions above $500


@app.post("/process")
async def process_payment(request: Request):
    body = await request.json()

    tenant_id = body["tenant_id"]
    tenant_tier = body["tenant_tier"]
    transaction_id = body["transaction_id"]
    amount = body["amount"]
    txn_type = body["type"]

    with set_business_context(
        tenant_id=tenant_id,
        tenant_tier=tenant_tier,
        transaction_id=transaction_id,
        transaction_value=amount,
        transaction_type=txn_type,
    ):
        start = time.time()

        # Step 1: Validate
        with tracer.start_as_current_span("payment.validate") as span:
            span.set_attribute("payment.amount", amount)
            span.set_attribute("payment.currency", body.get("currency", "USD"))
            span.set_attribute("payment.card_last4", body.get("card_last4", ""))

            # Simulate validation logic
            if amount <= 0:
                span.set_status(trace.StatusCode.ERROR, "Invalid amount")
                logger.error("Validation failed: invalid amount",
                             extra={"transaction_id": transaction_id, "amount": amount})
                slo_calculator.record_request(tenant_tier, is_error=True)
                return JSONResponse(status_code=400, content={
                    "error": "invalid_amount",
                    "transaction_id": transaction_id,
                })

            if amount > 50000:
                span.set_status(trace.StatusCode.ERROR, "Amount exceeds limit")
                logger.error("Validation failed: amount exceeds limit",
                             extra={"transaction_id": transaction_id, "amount": amount})
                slo_calculator.record_request(tenant_tier, is_error=True)
                return JSONResponse(status_code=400, content={
                    "error": "amount_exceeds_limit",
                    "transaction_id": transaction_id,
                })

            # Simulate processing time
            time.sleep(random.uniform(0.01, 0.05))

        # Step 2: Fraud check (for high-value transactions)
        fraud_result = None
        if amount >= FRAUD_THRESHOLD:
            with tracer.start_as_current_span("payment.fraud_check") as span:
                span.set_attribute("fraud.check_required", True)
                span.set_attribute("fraud.threshold", FRAUD_THRESHOLD)

                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.post(
                            f"{FRAUD_SERVICE_URL}/analyze",
                            json={
                                "transaction_id": transaction_id,
                                "tenant_id": tenant_id,
                                "amount": amount,
                                "type": txn_type,
                                "card_last4": body.get("card_last4", "4242"),
                            },
                        )
                        fraud_result = resp.json()

                    span.set_attribute("fraud.risk_score", fraud_result.get("risk_score", 0))
                    span.set_attribute("fraud.decision", fraud_result.get("decision", "unknown"))

                    if fraud_result.get("decision") == "reject":
                        span.set_status(trace.StatusCode.ERROR, "Rejected by fraud detection")
                        logger.warning(
                            "Transaction rejected by fraud detection",
                            extra={
                                "transaction_id": transaction_id,
                                "risk_score": fraud_result.get("risk_score"),
                                "tenant_id": tenant_id,
                            },
                        )
                        slo_calculator.record_request(tenant_tier, is_error=True)
                        return JSONResponse(status_code=403, content={
                            "error": "fraud_detected",
                            "transaction_id": transaction_id,
                            "risk_score": fraud_result.get("risk_score"),
                        })

                except httpx.TimeoutException:
                    span.set_status(trace.StatusCode.ERROR, "Fraud service timeout")
                    logger.error("Fraud service timeout",
                                 extra={"transaction_id": transaction_id})
                    # On timeout, allow transaction but flag for review
                    fraud_result = {"decision": "timeout_allow", "risk_score": 0.5}

        # Step 3: Charge
        with tracer.start_as_current_span("payment.charge") as span:
            span.set_attribute("charge.provider", "stripe_sim")
            span.set_attribute("charge.amount", amount)

            # Simulate charging with occasional failures
            charge_time = random.uniform(0.05, 0.3)

            # Inject realistic failures
            failure_rate = 0.02 if tenant_tier == "enterprise" else 0.05 if tenant_tier == "pro" else 0.08
            if random.random() < failure_rate:
                time.sleep(charge_time)
                error_code = random.choice(["card_declined", "insufficient_funds", "processor_error"])
                span.set_status(trace.StatusCode.ERROR, error_code)
                span.set_attribute("charge.error_code", error_code)
                logger.error(
                    f"Charge failed: {error_code}",
                    extra={
                        "transaction_id": transaction_id,
                        "error_code": error_code,
                        "amount": amount,
                        "tenant_id": tenant_id,
                    },
                )
                slo_calculator.record_request(tenant_tier, is_error=True)
                duration_ms = (time.time() - start) * 1000
                payment_metrics.record_payment(
                    duration_ms=duration_ms, value=amount, tenant_tier=tenant_tier,
                    transaction_type=txn_type, success=False, tenant_id=tenant_id,
                )
                return JSONResponse(status_code=402, content={
                    "error": error_code,
                    "transaction_id": transaction_id,
                })

            time.sleep(charge_time)
            span.set_attribute("charge.success", True)

        # Step 4: Confirm
        with tracer.start_as_current_span("payment.confirm") as span:
            time.sleep(random.uniform(0.01, 0.03))
            span.set_attribute("confirm.status", "completed")

            duration_ms = (time.time() - start) * 1000
            slo_calculator.record_request(tenant_tier, is_error=False)

            payment_metrics.record_payment(
                duration_ms=duration_ms, value=amount, tenant_tier=tenant_tier,
                transaction_type=txn_type, success=True, tenant_id=tenant_id,
            )

            # Update SLO error budget metric
            for tier in ["enterprise", "pro", "free"]:
                remaining = slo_calculator.get_error_budget_remaining(tier)
                payment_metrics.update_error_budget(tier, remaining)

            logger.info(
                "Payment confirmed",
                extra={
                    "transaction_id": transaction_id,
                    "amount": amount,
                    "duration_ms": round(duration_ms, 2),
                    "tenant_id": tenant_id,
                    "fraud_checked": fraud_result is not None,
                },
            )

            return {
                "status": "confirmed",
                "transaction_id": transaction_id,
                "amount": amount,
                "duration_ms": round(duration_ms, 2),
                "fraud_check": fraud_result,
            }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "payment-service"}


@app.get("/slo")
async def slo_status():
    return slo_calculator.get_all_budgets()
