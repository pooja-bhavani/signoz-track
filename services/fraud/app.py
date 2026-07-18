"""
Fraud Detection Service — uses LLM to analyze transaction risk.

Generates rich GenAI semantic convention spans:
- gen_ai.system, gen_ai.request.model
- gen_ai.usage.input_tokens, gen_ai.usage.output_tokens
- fraud.risk_score, fraud.confidence, fraud.decision
"""

import os
import time
import random
import logging
import json
from fastapi import FastAPI
from opentelemetry import trace

import sys
sys.path.insert(0, "/app")
from instrumentation import init_telemetry, set_business_context, PaymentMetrics
from instrumentation.setup import instrument_fastapi

init_telemetry("fraud-detection")
app = FastAPI(title="Fraud Detection Service")
instrument_fastapi(app)

tracer = trace.get_tracer("fraud-detection", "1.0.0")
logger = logging.getLogger("fraud-detection")
payment_metrics = PaymentMetrics()

USE_REAL_LLM = os.environ.get("USE_REAL_LLM", "false").lower() == "true"


def simulate_llm_analysis(transaction: dict) -> dict:
    """Simulated LLM fraud analysis when no API key is available."""
    time.sleep(random.uniform(0.1, 0.5))

    amount = transaction.get("amount", 0)
    # Higher amounts = higher risk (simplified)
    base_risk = min(amount / 10000, 0.8)
    noise = random.uniform(-0.15, 0.15)
    risk_score = max(0.0, min(1.0, base_risk + noise))

    confidence = random.uniform(0.75, 0.98)
    input_tokens = random.randint(200, 500)
    output_tokens = random.randint(50, 150)

    return {
        "risk_score": round(risk_score, 3),
        "confidence": round(confidence, 3),
        "reasoning": f"Transaction of ${amount} analyzed. Risk factors: amount={'high' if amount > 2000 else 'normal'}, card={'known' if random.random() > 0.3 else 'new'}.",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": "claude-haiku-4-5-simulated",
    }


async def real_llm_analysis(transaction: dict) -> dict:
    """Real LLM analysis using Anthropic API."""
    import anthropic

    client = anthropic.Anthropic()
    prompt = f"""Analyze this transaction for fraud risk. Return JSON with risk_score (0-1), confidence (0-1), and reasoning.

Transaction:
- Amount: ${transaction['amount']}
- Type: {transaction['type']}
- Card last 4: {transaction.get('card_last4', 'unknown')}
- Tenant: {transaction['tenant_id']}

Respond ONLY with valid JSON: {{"risk_score": float, "confidence": float, "reasoning": string}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    result = json.loads(text)
    result["input_tokens"] = response.usage.input_tokens
    result["output_tokens"] = response.usage.output_tokens
    result["model"] = "claude-haiku-4-5-20251001"
    return result


@app.post("/analyze")
async def analyze_transaction(transaction: dict):
    tenant_id = transaction.get("tenant_id", "unknown")
    transaction_id = transaction.get("transaction_id", "unknown")
    amount = transaction.get("amount", 0)

    with set_business_context(
        tenant_id=tenant_id,
        tenant_tier="enterprise",
        transaction_id=transaction_id,
        transaction_value=amount,
        transaction_type=transaction.get("type", "payment"),
    ):
        with tracer.start_as_current_span("fraud.analyze") as analyze_span:
            analyze_span.set_attribute("fraud.transaction_amount", amount)
            analyze_span.set_attribute("fraud.check_type", "llm_analysis")

            # LLM call span (follows GenAI semantic conventions)
            with tracer.start_as_current_span("fraud.llm_call") as llm_span:
                start = time.time()

                if USE_REAL_LLM:
                    result = await real_llm_analysis(transaction)
                else:
                    result = simulate_llm_analysis(transaction)

                duration_ms = (time.time() - start) * 1000

                # GenAI semantic convention attributes
                llm_span.set_attribute("gen_ai.system", "anthropic")
                llm_span.set_attribute("gen_ai.request.model", result["model"])
                llm_span.set_attribute("gen_ai.usage.input_tokens", result["input_tokens"])
                llm_span.set_attribute("gen_ai.usage.output_tokens", result["output_tokens"])
                llm_span.set_attribute("gen_ai.request.max_tokens", 200)

                # Fraud-specific attributes
                llm_span.set_attribute("fraud.risk_score", result["risk_score"])
                llm_span.set_attribute("fraud.confidence", result["confidence"])
                llm_span.set_attribute("fraud.analysis_duration_ms", round(duration_ms, 2))

                # Compute cost (Haiku pricing: $0.25/MTok input, $1.25/MTok output)
                cost = (result["input_tokens"] * 0.25 + result["output_tokens"] * 1.25) / 1_000_000
                llm_span.set_attribute("gen_ai.usage.cost_usd", round(cost, 6))

                # Record metrics
                payment_metrics.record_fraud_check(
                    duration_ms=duration_ms,
                    confidence=result["confidence"],
                    tokens_used=result["input_tokens"] + result["output_tokens"],
                    cost_usd=cost,
                    tenant_id=tenant_id,
                    risk_score=result["risk_score"],
                )

            # Decision span
            with tracer.start_as_current_span("fraud.decision") as decision_span:
                risk_score = result["risk_score"]

                if risk_score > 0.7:
                    decision = "reject"
                elif risk_score > 0.4:
                    decision = "review"
                else:
                    decision = "approve"

                decision_span.set_attribute("fraud.decision", decision)
                decision_span.set_attribute("fraud.risk_score", risk_score)
                decision_span.set_attribute("fraud.confidence", result["confidence"])
                decision_span.set_attribute("fraud.reasoning", result["reasoning"][:200])

                if decision == "reject":
                    logger.warning(
                        "Fraud detected - rejecting transaction",
                        extra={
                            "transaction_id": transaction_id,
                            "risk_score": risk_score,
                            "amount": amount,
                            "tenant_id": tenant_id,
                        },
                    )
                else:
                    logger.info(
                        f"Fraud check passed: {decision}",
                        extra={
                            "transaction_id": transaction_id,
                            "risk_score": risk_score,
                            "decision": decision,
                            "tenant_id": tenant_id,
                        },
                    )

                return {
                    "transaction_id": transaction_id,
                    "risk_score": risk_score,
                    "confidence": result["confidence"],
                    "decision": decision,
                    "reasoning": result["reasoning"],
                    "model": result["model"],
                    "analysis_duration_ms": round(duration_ms, 2),
                }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fraud-detection"}
