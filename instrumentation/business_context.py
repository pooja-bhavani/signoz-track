"""
Business Context Propagation for OpenTelemetry.

Injects tenant-level and transaction-level attributes into every span
automatically via context variables. This enables filtering traces,
logs, and metrics by business dimensions in SigNoz.

Usage:
    with set_business_context(tenant_id="acme", tier="enterprise", transaction_value=4999.99):
        # All spans created in this block carry business attributes
        do_work()
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass(frozen=True)
class BusinessContext:
    tenant_id: str
    tenant_tier: str  # free, pro, enterprise
    transaction_id: Optional[str] = None
    transaction_value: Optional[float] = None
    transaction_type: Optional[str] = None  # payment, refund, chargeback
    deployment_version: str = "1.0.0"
    region: str = "ap-south-1"
    _created_at: float = field(default_factory=time.time)

    def to_span_attributes(self) -> dict:
        attrs = {
            "tenant.id": self.tenant_id,
            "tenant.tier": self.tenant_tier,
            "deployment.version": self.deployment_version,
            "cloud.region": self.region,
        }
        if self.transaction_id:
            attrs["transaction.id"] = self.transaction_id
        if self.transaction_value is not None:
            attrs["transaction.value"] = self.transaction_value
        if self.transaction_type:
            attrs["transaction.type"] = self.transaction_type
        return attrs

    def to_log_attributes(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "tenant_tier": self.tenant_tier,
            "transaction_id": self.transaction_id or "",
        }


_business_context_var: ContextVar[Optional[BusinessContext]] = ContextVar(
    "business_context", default=None
)


def get_business_context() -> Optional[BusinessContext]:
    return _business_context_var.get()


class set_business_context:
    """Context manager that sets business context for the current execution scope."""

    def __init__(
        self,
        tenant_id: str,
        tenant_tier: str = "free",
        transaction_id: Optional[str] = None,
        transaction_value: Optional[float] = None,
        transaction_type: Optional[str] = None,
    ):
        self._ctx = BusinessContext(
            tenant_id=tenant_id,
            tenant_tier=tenant_tier,
            transaction_id=transaction_id,
            transaction_value=transaction_value,
            transaction_type=transaction_type,
        )
        self._token = None

    def __enter__(self):
        self._token = _business_context_var.set(self._ctx)
        return self._ctx

    def __exit__(self, *_):
        if self._token:
            _business_context_var.reset(self._token)
