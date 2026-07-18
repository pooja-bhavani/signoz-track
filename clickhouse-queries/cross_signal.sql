-- ============================================================
-- CROSS-SIGNAL CLICKHOUSE QUERIES FOR SIGNOZ
-- Context-Aware Root Cause Dashboard
-- ============================================================

-- ============================================================
-- QUERY 1: P99 Latency + Error Spans + Error Logs (per minute, per service)
-- Use in: Cross-Signal Root Cause Panel
-- ============================================================
SELECT
    toStartOfInterval(timestamp, INTERVAL 1 MINUTE) AS minute,
    serviceName,
    quantile(0.99)(durationNano / 1000000) AS p99_latency_ms,
    quantile(0.50)(durationNano / 1000000) AS p50_latency_ms,
    countIf(statusCode = 2) AS error_span_count,
    count() AS total_span_count,
    round(countIf(statusCode = 2) * 100.0 / count(), 2) AS error_rate_pct
FROM signoz_traces.signoz_index_v3
WHERE timestamp >= now() - INTERVAL 1 HOUR
    AND kind = 2  -- SPAN_KIND_SERVER
GROUP BY minute, serviceName
ORDER BY minute DESC, serviceName;


-- ============================================================
-- QUERY 2: Business Impact — Revenue at Risk by Tenant
-- Use in: Business Context Overview Panel
-- ============================================================
SELECT
    stringTagMap['tenant.id'] AS tenant_id,
    stringTagMap['tenant.tier'] AS tier,
    count() AS total_transactions,
    countIf(statusCode = 2) AS failed_transactions,
    round(countIf(statusCode = 2) * 100.0 / count(), 2) AS failure_rate,
    -- Approximate revenue at risk (from span attributes)
    sum(toFloat64OrZero(numberTagMap['transaction.value'])) AS total_value_processed,
    sumIf(toFloat64OrZero(numberTagMap['transaction.value']), statusCode = 2) AS revenue_at_risk
FROM signoz_traces.signoz_index_v3
WHERE timestamp >= now() - INTERVAL 1 HOUR
    AND name = 'payment.charge'
GROUP BY tenant_id, tier
ORDER BY revenue_at_risk DESC;


-- ============================================================
-- QUERY 3: Trace → Log Correlation (join spans with their logs)
-- Use in: Cross-Signal Panel (trace-map alongside log frequency)
-- ============================================================
SELECT
    t.traceID AS trace_id,
    t.serviceName AS service,
    t.name AS operation,
    t.durationNano / 1000000 AS duration_ms,
    t.statusCode AS span_status,
    t.stringTagMap['tenant.id'] AS tenant_id,
    l.severity_text AS log_level,
    l.body AS log_message,
    l.timestamp AS log_timestamp
FROM signoz_traces.signoz_index_v3 AS t
LEFT JOIN signoz_logs.logs AS l
    ON t.traceID = l.trace_id
    AND l.timestamp >= t.timestamp
    AND l.timestamp <= t.timestamp + (t.durationNano / 1000000)
WHERE t.timestamp >= now() - INTERVAL 30 MINUTE
    AND t.statusCode = 2  -- Only error traces
ORDER BY t.timestamp DESC
LIMIT 100;


-- ============================================================
-- QUERY 4: Service Dependency Error Map
-- Use in: Service topology with error overlay
-- ============================================================
SELECT
    serviceName AS source_service,
    stringTagMap['peer.service'] AS destination_service,
    count() AS call_count,
    countIf(statusCode = 2) AS error_count,
    round(countIf(statusCode = 2) * 100.0 / count(), 2) AS error_rate,
    quantile(0.99)(durationNano / 1000000) AS p99_ms,
    quantile(0.50)(durationNano / 1000000) AS p50_ms
FROM signoz_traces.signoz_index_v3
WHERE timestamp >= now() - INTERVAL 1 HOUR
    AND kind = 3  -- SPAN_KIND_CLIENT
    AND stringTagMap['peer.service'] != ''
GROUP BY source_service, destination_service
ORDER BY error_rate DESC;


-- ============================================================
-- QUERY 5: SLO Error Budget by Tier (rolling 1-hour window)
-- Use in: SLO Compliance Panel
-- ============================================================
WITH
    tier_stats AS (
        SELECT
            stringTagMap['tenant.tier'] AS tier,
            count() AS total_requests,
            countIf(statusCode = 2) AS error_count,
            round(1.0 - (countIf(statusCode = 2) / count()), 6) AS actual_success_rate
        FROM signoz_traces.signoz_index_v3
        WHERE timestamp >= now() - INTERVAL 1 HOUR
            AND kind = 2
            AND stringTagMap['tenant.tier'] != ''
        GROUP BY tier
    ),
    slo_targets AS (
        SELECT 'enterprise' AS tier, 0.999 AS target
        UNION ALL SELECT 'pro', 0.995
        UNION ALL SELECT 'free', 0.990
    )
SELECT
    ts.tier,
    st.target AS slo_target,
    ts.total_requests,
    ts.error_count,
    ts.actual_success_rate,
    round((1 - st.target) * ts.total_requests, 2) AS error_budget_total,
    round((1 - st.target) * ts.total_requests - ts.error_count, 2) AS error_budget_remaining,
    round(ts.error_count / ((1 - st.target) * ts.total_requests), 4) AS burn_rate,
    CASE
        WHEN ts.error_count / ((1 - st.target) * ts.total_requests) > 1.0 THEN 'CRITICAL'
        WHEN ts.error_count / ((1 - st.target) * ts.total_requests) > 0.5 THEN 'WARNING'
        ELSE 'OK'
    END AS budget_status
FROM tier_stats ts
JOIN slo_targets st ON ts.tier = st.tier
ORDER BY burn_rate DESC;


-- ============================================================
-- QUERY 6: Fraud Detection LLM Performance
-- Use in: AI Agent Operations Panel
-- ============================================================
SELECT
    toStartOfInterval(timestamp, INTERVAL 5 MINUTE) AS interval,
    quantile(0.50)(durationNano / 1000000) AS p50_llm_latency_ms,
    quantile(0.95)(durationNano / 1000000) AS p95_llm_latency_ms,
    avg(toFloat64OrZero(numberTagMap['gen_ai.usage.input_tokens'])) AS avg_input_tokens,
    avg(toFloat64OrZero(numberTagMap['gen_ai.usage.output_tokens'])) AS avg_output_tokens,
    avg(toFloat64OrZero(numberTagMap['fraud.confidence'])) AS avg_confidence,
    countIf(toFloat64OrZero(numberTagMap['fraud.risk_score']) > 0.7) AS high_risk_count,
    count() AS total_checks
FROM signoz_traces.signoz_index_v3
WHERE timestamp >= now() - INTERVAL 1 HOUR
    AND name = 'fraud.llm_call'
GROUP BY interval
ORDER BY interval DESC;


-- ============================================================
-- QUERY 7: Log Frequency Heatmap by Service and Severity
-- Use in: Cross-Signal Panel (log frequency view)
-- ============================================================
SELECT
    toStartOfInterval(timestamp, INTERVAL 1 MINUTE) AS minute,
    resources_string['service.name'] AS service_name,
    severity_text,
    count() AS log_count
FROM signoz_logs.logs
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY minute, service_name, severity_text
ORDER BY minute DESC, service_name, severity_text;


-- ============================================================
-- QUERY 8: Anomaly Detection — P99 Spike Correlation with Errors
-- Use in: Predictive SLO Alert supporting data
-- ============================================================
WITH
    baseline AS (
        SELECT
            serviceName,
            quantile(0.99)(durationNano / 1000000) AS baseline_p99
        FROM signoz_traces.signoz_index_v3
        WHERE timestamp >= now() - INTERVAL 24 HOUR
            AND timestamp < now() - INTERVAL 1 HOUR
            AND kind = 2
        GROUP BY serviceName
    )
SELECT
    toStartOfInterval(t.timestamp, INTERVAL 5 MINUTE) AS interval,
    t.serviceName,
    quantile(0.99)(t.durationNano / 1000000) AS current_p99,
    b.baseline_p99,
    round(quantile(0.99)(t.durationNano / 1000000) / b.baseline_p99, 2) AS spike_ratio,
    countIf(t.statusCode = 2) AS errors_in_window,
    count() AS requests_in_window
FROM signoz_traces.signoz_index_v3 AS t
JOIN baseline AS b ON t.serviceName = b.serviceName
WHERE t.timestamp >= now() - INTERVAL 1 HOUR
    AND t.kind = 2
GROUP BY interval, t.serviceName, b.baseline_p99
HAVING spike_ratio > 2.0  -- Only show intervals with >2x baseline latency
ORDER BY spike_ratio DESC;


-- ============================================================
-- QUERY 9: Transaction Value Distribution (for business impact)
-- Use in: Business Context Dashboard
-- ============================================================
SELECT
    stringTagMap['tenant.tier'] AS tier,
    stringTagMap['transaction.type'] AS txn_type,
    count() AS count,
    round(avg(toFloat64OrZero(numberTagMap['transaction.value'])), 2) AS avg_value,
    round(quantile(0.95)(toFloat64OrZero(numberTagMap['transaction.value'])), 2) AS p95_value,
    round(max(toFloat64OrZero(numberTagMap['transaction.value'])), 2) AS max_value,
    round(sum(toFloat64OrZero(numberTagMap['transaction.value'])), 2) AS total_value
FROM signoz_traces.signoz_index_v3
WHERE timestamp >= now() - INTERVAL 1 HOUR
    AND name = 'payment.charge'
    AND numberTagMap['transaction.value'] > 0
GROUP BY tier, txn_type
ORDER BY tier, total_value DESC;


-- ============================================================
-- QUERY 10: End-to-End Trace Breakdown (gateway → payment → fraud)
-- Use in: Trace map panel for root cause investigation
-- ============================================================
SELECT
    t.traceID,
    t.spanID,
    t.parentSpanID,
    t.serviceName,
    t.name AS operation,
    t.durationNano / 1000000 AS duration_ms,
    t.statusCode,
    t.stringTagMap['tenant.id'] AS tenant_id,
    t.stringTagMap['tenant.tier'] AS tier,
    toFloat64OrZero(t.numberTagMap['transaction.value']) AS txn_value,
    t.statusMessage
FROM signoz_traces.signoz_index_v3 AS t
WHERE t.traceID IN (
    -- Get trace IDs of recent errors for enterprise tenants
    SELECT DISTINCT traceID
    FROM signoz_traces.signoz_index_v3
    WHERE timestamp >= now() - INTERVAL 30 MINUTE
        AND statusCode = 2
        AND stringTagMap['tenant.tier'] = 'enterprise'
    LIMIT 10
)
ORDER BY t.traceID, t.timestamp;
