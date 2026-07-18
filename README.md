# Context-Aware Root Cause Dashboard

**WeMakeDevs x SigNoz Hackathon — Track 2: Signals & Dashboards**

A multi-tenant payment processing system with deep custom OpenTelemetry instrumentation, cross-signal correlation dashboards, and predictive SLO alerting — all built for SigNoz.

## Architecture

```
                         ┌───────────────────────────────────┐
                         │         Load Generator            │
                         │   Multi-tenant traffic patterns   │
                         └───────────────┬───────────────────┘
                                         │ HTTP
                                         ▼
┌────────────────────────────────────────────────────────────────────┐
│                     Application Layer                               │
│                                                                    │
│  ┌──────────────┐    ┌──────────────────┐    ┌─────────────────┐  │
│  │ API Gateway  │───▶│ Payment Service  │───▶│ Fraud Detection │  │
│  │   :8000      │    │     :8001        │    │  :8002 (LLM)    │  │
│  └──────────────┘    └──────────────────┘    └─────────────────┘  │
│         │                     │                       │            │
│         └─────────────────────┼───────────────────────┘            │
│                               │                                    │
│              Custom OTel: Business Context in every span            │
│              (tenant.id, tenant.tier, transaction.value)            │
└───────────────────────────────┼────────────────────────────────────┘
                                │ OTLP/HTTP :4318
                                ▼
                    ┌─────────────────────┐
                    │  OTel Collector     │
                    │  + hostmetrics      │
                    │  + resource proc    │
                    │  + transform proc   │
                    └──────────┬──────────┘
                               │ OTLP/HTTP
                               ▼
                    ┌─────────────────────┐
                    │  SigNoz (EC2)       │
                    │  Traces + Logs +    │
                    │  Metrics            │
                    │  :4318 ingest       │
                    │  :8080 UI           │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  AI RCA Agent       │
                    │  Queries SigNoz API │
                    │  Cross-signal       │
                    │  diagnosis          │
                    └─────────────────────┘
```

## What Makes This Special (Track 2 Criteria)

### 1. Custom OTel Instrumentation (not just auto-instrumentation)
- **BusinessContext propagation** via Python `contextvars` — every span carries `tenant.id`, `tenant.tier`, `transaction.value`, `transaction.type`
- **Custom SpanProcessor** (`BusinessContextSpanProcessor`) that automatically injects business attributes on `on_start`
- **GenAI semantic conventions** for LLM tracing (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*`)
- **Custom metrics**: `payment.duration`, `fraud.confidence`, `slo.error_budget.remaining`

### 2. Cross-Signal Correlation (Traces + Logs + Metrics in one view)
- ClickHouse SQL queries joining `signoz_traces` with `signoz_logs` on `trace_id`
- Dashboards showing P99 latency alongside error log count alongside CPU utilization
- Business impact panel: revenue at risk = SUM(transaction.value) WHERE status=ERROR

### 3. SigNoz Query Builder Mastery
- 4 importable dashboard JSONs with complex multi-signal panels
- Formula queries (error rate = errors/total * 100)
- GroupBy on custom business attributes (tenant.tier, tenant.id)
- Threshold-based alerting on custom metrics

### 4. Predictive SLO Alert System
- Per-tier error budgets: enterprise=99.9%, pro=99.5%, free=99.0%
- Rolling 1-hour window with burn rate calculation
- Alert fires when burn_rate > 1.0 (budget exhausting faster than allowed)

## Prerequisites

- Docker & Docker Compose
- A running SigNoz instance (self-hosted or cloud)
  - Self-hosted: https://signoz.io/docs/install/docker/
  - You need the OTLP endpoint (default: `http://<signoz-ip>:4318`)

## Quick Start

```bash
# 1. Clone
git clone https://github.com/pooja-bhavani/signoz-rca-dashboard.git
cd signoz-rca-dashboard

# 2. Set your SigNoz endpoint
export SIGNOZ_ENDPOINT=http://<your-signoz-ip>:4318

# 3. Start everything
docker-compose up --build -d

# 4. Verify services are running
docker-compose ps

# 5. Check telemetry is flowing
# Open SigNoz UI → Services tab → you should see:
#   - api-gateway
#   - payment-service
#   - fraud-detection
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SIGNOZ_ENDPOINT` | `http://host.docker.internal:4318` | SigNoz OTLP HTTP endpoint |
| `RPS` | `5` | Requests per second from load generator |
| `USE_REAL_LLM` | `false` | Use real Anthropic API for fraud detection |
| `ANTHROPIC_API_KEY` | (empty) | Required if USE_REAL_LLM=true |

## Project Structure

```
signoz-rca-dashboard/
├── instrumentation/           # Custom OTel library
│   ├── business_context.py    # Context propagation via contextvars
│   ├── span_enricher.py       # Custom SpanProcessor
│   ├── custom_metrics.py      # Business metrics definitions
│   ├── slo_calculator.py      # Error budget computation
│   └── setup.py               # TracerProvider/MeterProvider init
├── services/
│   ├── gateway/app.py         # API Gateway (auth, rate-limit, route)
│   ├── payment/app.py         # Payment pipeline (validate→fraud→charge)
│   └── fraud/app.py           # LLM-based fraud detection (GenAI conventions)
├── load-generator/
│   └── generate.py            # Multi-tenant traffic simulation
├── agent/
│   └── rca_agent.py           # AI Root Cause Analysis agent
├── dashboards/                # Importable SigNoz dashboard JSONs
│   ├── business-context.json
│   ├── cross-signal-rca.json
│   ├── slo-compliance.json
│   └── agent-operations.json
├── alerts/                    # SigNoz alert rule definitions
│   ├── slo-burn-rate.json
│   ├── p99-by-tier.json
│   └── fraud-confidence.json
├── clickhouse-queries/
│   └── cross_signal.sql       # 10 advanced ClickHouse queries
├── otel-collector-config.yaml # Collector with hostmetrics + processors
└── docker-compose.yaml
```

## Importing Dashboards

1. Open SigNoz UI → Dashboards → New Dashboard → Import JSON
2. Upload each file from `dashboards/` directory
3. Set time range to last 15 minutes
4. Traffic from load-generator should populate all panels

## Importing Alert Rules

1. Open SigNoz UI → Alerts → New Alert
2. Use the Query Builder to replicate the queries from `alerts/*.json`
3. Set threshold values as specified in each alert definition

## ClickHouse Queries

The `clickhouse-queries/cross_signal.sql` file contains 10 production-ready queries:

1. **P99 + Error Spans per service** — latency/error correlation
2. **Revenue at Risk by Tenant** — business impact of errors
3. **Trace → Log Join** — spans correlated with their log entries
4. **Service Dependency Error Map** — topology with error rates
5. **SLO Error Budget by Tier** — rolling window compliance
6. **LLM Performance Tracking** — fraud detection AI metrics
7. **Log Frequency Heatmap** — severity distribution over time
8. **Anomaly Detection** — P99 spike vs 24h baseline
9. **Transaction Value Distribution** — business metric analysis
10. **End-to-End Trace Breakdown** — full trace for enterprise errors

## Running the RCA Agent

```bash
# From project root
cd agent
pip install -r requirements.txt

# Investigate all services
python rca_agent.py

# Investigate specific service
python rca_agent.py payment-service

# Investigate specific tenant
python rca_agent.py payment-service acme-corp
```

## Custom Instrumentation Deep Dive

### Business Context Propagation

```python
from instrumentation import BusinessContext, business_context

# Set business context (automatically injected into all child spans)
with business_context(
    tenant_id="acme-corp",
    tier="enterprise",
    transaction_value=1500.00,
    transaction_type="payment"
):
    # All spans created here carry tenant.id, tenant.tier, etc.
    process_payment(...)
```

### Custom SpanProcessor

```python
class BusinessContextSpanProcessor(SpanProcessor):
    def on_start(self, span, parent_context):
        ctx = get_current_business_context()
        if ctx:
            span.set_attribute("tenant.id", ctx.tenant_id)
            span.set_attribute("tenant.tier", ctx.tier)
            span.set_attribute("transaction.value", ctx.transaction_value)
```

### SLO Calculator

```python
from instrumentation import SLOCalculator

slo = SLOCalculator()
slo.record_request("enterprise", success=True)
slo.record_request("enterprise", success=False)

budget = slo.get_error_budget("enterprise")
# → {"remaining_pct": 98.5, "burn_rate": 0.15, "status": "OK"}
```

## Demo Scenario

1. Start services → load generator sends multi-tenant traffic
2. After 5 minutes, SigNoz shows all services with business context attributes
3. Enterprise tier maintains 99.9% → SLO dashboard shows healthy budget
4. Inject failure: increase free-tier error rate → watch burn rate climb
5. Alert fires → RCA agent investigates → identifies root cause
6. Cross-signal panel shows: latency spike + error logs + CPU correlation

## Tech Stack

- **Python 3.12** + FastAPI
- **OpenTelemetry SDK** (manual instrumentation, custom processors)
- **OTel Collector Contrib** 0.104.0 (hostmetrics, transform processor)
- **SigNoz** (ClickHouse backend)
- **Docker Compose** for local orchestration
