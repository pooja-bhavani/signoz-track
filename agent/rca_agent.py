"""
AI Root Cause Analysis Agent

Uses SigNoz MCP to query all three signals (traces, logs, metrics),
correlates them, and produces a structured root cause diagnosis.

The agent itself is fully instrumented — its investigation
appears as traces in SigNoz for meta-observability.
"""

import os
import json
import time
import httpx
from dataclasses import dataclass, asdict
from opentelemetry import trace
from opentelemetry.trace import StatusCode

SIGNOZ_API = os.environ.get("SIGNOZ_API_URL", "http://localhost:8080/api/v1")
SIGNOZ_TOKEN = os.environ.get("SIGNOZ_ACCESS_TOKEN", "")

tracer = trace.get_tracer("rca-agent", "1.0.0")


@dataclass
class RCADiagnosis:
    root_cause: str
    confidence: float
    affected_tenants: list
    affected_services: list
    evidence: dict
    recommended_actions: list
    investigation_duration_ms: float


class SigNozClient:
    """Query SigNoz API for traces, logs, and metrics."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(timeout=30.0, headers=self.headers)

    @tracer.start_as_current_span("rca.query_error_traces")
    def get_error_traces(self, service: str = None, minutes: int = 30):
        span = trace.get_current_span()
        span.set_attribute("rca.query_type", "error_traces")
        span.set_attribute("rca.time_window_minutes", minutes)

        params = {
            "start": int((time.time() - minutes * 60) * 1e9),
            "end": int(time.time() * 1e9),
            "limit": 50,
        }

        try:
            resp = self.client.get(
                f"{self.base_url}/traces",
                params=params,
            )
            if resp.status_code == 200:
                data = resp.json()
                span.set_attribute("rca.results_count", len(data.get("traces", [])))
                return data
            span.set_attribute("rca.query_error", f"HTTP {resp.status_code}")
            return {"traces": []}
        except Exception as e:
            span.set_status(StatusCode.ERROR, str(e))
            return {"traces": []}

    @tracer.start_as_current_span("rca.query_error_logs")
    def get_error_logs(self, service: str = None, minutes: int = 30):
        span = trace.get_current_span()
        span.set_attribute("rca.query_type", "error_logs")

        params = {
            "start": int((time.time() - minutes * 60) * 1e9),
            "end": int(time.time() * 1e9),
            "limit": 100,
        }

        try:
            resp = self.client.get(
                f"{self.base_url}/logs",
                params=params,
            )
            if resp.status_code == 200:
                data = resp.json()
                span.set_attribute("rca.results_count", len(data.get("logs", [])))
                return data
            return {"logs": []}
        except Exception as e:
            span.set_status(StatusCode.ERROR, str(e))
            return {"logs": []}

    @tracer.start_as_current_span("rca.query_metrics")
    def get_service_metrics(self, metric_name: str, minutes: int = 30):
        span = trace.get_current_span()
        span.set_attribute("rca.query_type", "metrics")
        span.set_attribute("rca.metric_name", metric_name)

        params = {
            "start": int((time.time() - minutes * 60) * 1e9),
            "end": int(time.time() * 1e9),
            "step": 60,
        }

        try:
            resp = self.client.get(
                f"{self.base_url}/metrics",
                params=params,
            )
            if resp.status_code == 200:
                return resp.json()
            return {"metrics": []}
        except Exception as e:
            span.set_status(StatusCode.ERROR, str(e))
            return {"metrics": []}


class RCAAgent:
    """
    AI-powered Root Cause Analysis Agent.

    Investigation flow:
    1. Detect anomaly (triggered by alert or manual)
    2. Query traces for error patterns
    3. Query logs correlated with errors
    4. Query metrics (CPU, memory) for resource signals
    5. Correlate all signals
    6. Produce structured diagnosis
    """

    def __init__(self):
        self.signoz = SigNozClient(SIGNOZ_API, SIGNOZ_TOKEN)

    @tracer.start_as_current_span("rca.investigate")
    def investigate(self, trigger: str = "manual", context: dict = None):
        """Run full RCA investigation."""
        span = trace.get_current_span()
        span.set_attribute("rca.trigger", trigger)
        start_time = time.time()

        if context:
            for k, v in context.items():
                span.set_attribute(f"rca.context.{k}", str(v))

        # Phase 1: Gather signals
        traces = self._gather_traces(context)
        logs = self._gather_logs(context)
        metrics = self._gather_metrics()

        # Phase 2: Correlate
        correlation = self._correlate_signals(traces, logs, metrics)

        # Phase 3: Diagnose
        diagnosis = self._diagnose(correlation, context)

        duration_ms = (time.time() - start_time) * 1000
        diagnosis.investigation_duration_ms = duration_ms

        span.set_attribute("rca.diagnosis.root_cause", diagnosis.root_cause)
        span.set_attribute("rca.diagnosis.confidence", diagnosis.confidence)
        span.set_attribute("rca.diagnosis.affected_services", json.dumps(diagnosis.affected_services))
        span.set_attribute("rca.investigation_duration_ms", duration_ms)

        return diagnosis

    @tracer.start_as_current_span("rca.gather_traces")
    def _gather_traces(self, context: dict = None):
        service = context.get("service") if context else None
        data = self.signoz.get_error_traces(service=service, minutes=30)
        traces = data.get("traces", [])

        span = trace.get_current_span()
        span.set_attribute("rca.traces_found", len(traces))

        # Extract patterns
        error_services = {}
        error_operations = {}
        affected_tenants = set()

        for t in traces:
            svc = t.get("serviceName", "unknown")
            op = t.get("name", "unknown")
            tenant = t.get("tags", {}).get("tenant.id", "unknown")

            error_services[svc] = error_services.get(svc, 0) + 1
            error_operations[op] = error_operations.get(op, 0) + 1
            affected_tenants.add(tenant)

        return {
            "raw": traces,
            "error_services": error_services,
            "error_operations": error_operations,
            "affected_tenants": list(affected_tenants),
        }

    @tracer.start_as_current_span("rca.gather_logs")
    def _gather_logs(self, context: dict = None):
        service = context.get("service") if context else None
        data = self.signoz.get_error_logs(service=service, minutes=30)
        logs = data.get("logs", [])

        span = trace.get_current_span()
        span.set_attribute("rca.logs_found", len(logs))

        error_patterns = {}
        for log_entry in logs:
            msg = log_entry.get("body", "")[:100]
            error_patterns[msg] = error_patterns.get(msg, 0) + 1

        return {
            "raw": logs,
            "error_patterns": error_patterns,
            "count": len(logs),
        }

    @tracer.start_as_current_span("rca.gather_metrics")
    def _gather_metrics(self):
        cpu = self.signoz.get_service_metrics("system_cpu_utilization")
        memory = self.signoz.get_service_metrics("system_memory_utilization")

        return {
            "cpu": cpu,
            "memory": memory,
        }

    @tracer.start_as_current_span("rca.correlate_signals")
    def _correlate_signals(self, traces: dict, logs: dict, metrics: dict):
        """Find patterns across all three signal types."""
        span = trace.get_current_span()

        correlation = {
            "primary_error_service": None,
            "error_concentration": 0.0,
            "temporal_correlation": False,
            "resource_saturation": False,
            "affected_tenants": traces.get("affected_tenants", []),
        }

        # Find the service with most errors
        error_services = traces.get("error_services", {})
        if error_services:
            top_service = max(error_services, key=error_services.get)
            total_errors = sum(error_services.values())
            correlation["primary_error_service"] = top_service
            correlation["error_concentration"] = error_services[top_service] / max(total_errors, 1)

        span.set_attribute("rca.correlation.primary_service", correlation["primary_error_service"] or "none")
        span.set_attribute("rca.correlation.concentration", correlation["error_concentration"])

        return correlation

    @tracer.start_as_current_span("rca.diagnose")
    def _diagnose(self, correlation: dict, context: dict = None):
        """Produce final diagnosis from correlated signals."""
        span = trace.get_current_span()

        primary_service = correlation.get("primary_error_service", "unknown")
        concentration = correlation.get("error_concentration", 0)

        # Determine root cause hypothesis
        if concentration > 0.8:
            root_cause = f"Single-service failure: {primary_service} is the source of {concentration*100:.0f}% of errors"
            confidence = 0.85
        elif concentration > 0.5:
            root_cause = f"Cascading failure originating from {primary_service}"
            confidence = 0.7
        else:
            root_cause = "Distributed failure across multiple services — likely infrastructure or dependency issue"
            confidence = 0.5

        actions = [
            f"Investigate {primary_service} pods for resource constraints",
            "Check recent deployments for regressions",
            "Review error logs for common exception patterns",
            "Verify downstream dependency health",
        ]

        if correlation.get("resource_saturation"):
            actions.insert(0, "URGENT: Scale up affected service — resource saturation detected")
            confidence += 0.1

        diagnosis = RCADiagnosis(
            root_cause=root_cause,
            confidence=min(confidence, 1.0),
            affected_tenants=correlation.get("affected_tenants", []),
            affected_services=[primary_service] if primary_service else [],
            evidence={
                "error_concentration": concentration,
                "primary_service": primary_service,
                "resource_saturated": correlation.get("resource_saturation", False),
            },
            recommended_actions=actions,
            investigation_duration_ms=0,
        )

        span.set_attribute("rca.diagnosis.confidence", diagnosis.confidence)
        return diagnosis


def main():
    """CLI entrypoint for manual investigations."""
    import sys
    from instrumentation.setup import init_telemetry

    init_telemetry("rca-agent")

    agent = RCAAgent()

    context = {}
    if len(sys.argv) > 1:
        context["service"] = sys.argv[1]
    if len(sys.argv) > 2:
        context["tenant"] = sys.argv[2]

    print("Starting Root Cause Analysis investigation...")
    print(f"Context: {context or 'all services'}")
    print("-" * 50)

    diagnosis = agent.investigate(trigger="cli", context=context)

    print(f"\nRoot Cause: {diagnosis.root_cause}")
    print(f"Confidence: {diagnosis.confidence:.0%}")
    print(f"Affected Tenants: {', '.join(diagnosis.affected_tenants) or 'unknown'}")
    print(f"Affected Services: {', '.join(diagnosis.affected_services) or 'unknown'}")
    print(f"\nRecommended Actions:")
    for i, action in enumerate(diagnosis.recommended_actions, 1):
        print(f"  {i}. {action}")
    print(f"\nInvestigation Duration: {diagnosis.investigation_duration_ms:.0f}ms")

    # Output as JSON for programmatic use
    print(f"\n--- JSON Output ---")
    print(json.dumps(asdict(diagnosis), indent=2))


if __name__ == "__main__":
    main()
