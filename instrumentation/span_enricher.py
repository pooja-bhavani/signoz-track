"""
Custom SpanProcessor that automatically injects business context
into every span created within its scope.

This is the key differentiator from generic auto-instrumentation:
every span carries tenant_id, tier, transaction_value — enabling
SigNoz Query Builder to filter/group by business dimensions.
"""

from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan
from opentelemetry.trace import Span
from .business_context import get_business_context


class BusinessContextSpanProcessor(SpanProcessor):
    """Injects business context attributes into every span on start."""

    def on_start(self, span: Span, parent_context=None) -> None:
        ctx = get_business_context()
        if ctx is None:
            return
        for key, value in ctx.to_span_attributes().items():
            span.set_attribute(key, value)

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
