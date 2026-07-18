"""
Custom business metrics for the payment processing system.

These go beyond what auto-instrumentation provides — they capture
business-level signals that enable revenue-aware dashboards and
per-tenant SLO tracking.
"""

from opentelemetry import metrics

meter = metrics.get_meter("payment.system", "1.0.0")


class PaymentMetrics:
    def __init__(self):
        self.payment_duration = meter.create_histogram(
            name="payment.duration",
            description="Time to process a payment end-to-end",
            unit="ms",
        )

        self.payment_value_total = meter.create_counter(
            name="payment.value.total",
            description="Total USD value of processed payments",
            unit="USD",
        )

        self.payment_count = meter.create_counter(
            name="payment.count",
            description="Number of payment attempts",
            unit="1",
        )

        self.payment_error_count = meter.create_counter(
            name="payment.error.count",
            description="Number of failed payments",
            unit="1",
        )

        self.fraud_detection_duration = meter.create_histogram(
            name="fraud.detection.duration",
            description="Time for fraud detection LLM analysis",
            unit="ms",
        )

        self.fraud_confidence = meter.create_histogram(
            name="fraud.confidence",
            description="Fraud detection model confidence score",
            unit="1",
        )

        self.fraud_llm_tokens = meter.create_counter(
            name="fraud.llm.tokens",
            description="Tokens consumed by fraud detection LLM",
            unit="tokens",
        )

        self.fraud_llm_cost = meter.create_counter(
            name="fraud.llm.cost",
            description="USD cost of fraud detection LLM calls",
            unit="USD",
        )

        self.slo_error_budget_remaining = meter.create_gauge(
            name="slo.error_budget.remaining",
            description="Remaining error budget percentage per tenant tier",
            unit="percent",
        )

        self.active_transactions = meter.create_up_down_counter(
            name="payment.active_transactions",
            description="Currently in-flight transactions",
            unit="1",
        )

    def record_payment(self, duration_ms: float, value: float, tenant_tier: str,
                       transaction_type: str, success: bool, tenant_id: str):
        attrs = {
            "tenant.tier": tenant_tier,
            "tenant.id": tenant_id,
            "transaction.type": transaction_type,
            "payment.status": "success" if success else "error",
        }
        self.payment_duration.record(duration_ms, attrs)
        self.payment_count.add(1, attrs)
        if success:
            self.payment_value_total.add(value, attrs)
        else:
            self.payment_error_count.add(1, attrs)

    def record_fraud_check(self, duration_ms: float, confidence: float,
                           tokens_used: int, cost_usd: float,
                           tenant_id: str, risk_score: float):
        attrs = {
            "tenant.id": tenant_id,
            "fraud.risk_level": "high" if risk_score > 0.7 else "medium" if risk_score > 0.4 else "low",
        }
        self.fraud_detection_duration.record(duration_ms, attrs)
        self.fraud_confidence.record(confidence, attrs)
        self.fraud_llm_tokens.add(tokens_used, attrs)
        self.fraud_llm_cost.add(cost_usd, attrs)

    def update_error_budget(self, tenant_tier: str, remaining_percent: float):
        self.slo_error_budget_remaining.set(
            remaining_percent,
            {"tenant.tier": tenant_tier},
        )
