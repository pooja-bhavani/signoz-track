"""
SLO Error Budget Calculator.

Tracks error rates per tenant tier, computes remaining error budget,
and exposes burn rate as a metric for predictive alerting.

SLO Targets:
  enterprise: 99.9% (1 error per 1000 requests allowed)
  pro:        99.5% (5 errors per 1000 requests allowed)
  free:       99.0% (10 errors per 1000 requests allowed)
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


SLO_TARGETS = {
    "enterprise": 0.999,
    "pro": 0.995,
    "free": 0.990,
}

WINDOW_SECONDS = 3600  # 1-hour rolling window


@dataclass
class TierStats:
    total: int = 0
    errors: int = 0
    timestamps: list = field(default_factory=list)
    error_timestamps: list = field(default_factory=list)


class SLOCalculator:
    def __init__(self):
        self._stats: dict[str, TierStats] = defaultdict(TierStats)
        self._lock = Lock()

    def record_request(self, tenant_tier: str, is_error: bool):
        now = time.time()
        with self._lock:
            stats = self._stats[tenant_tier]
            stats.total += 1
            stats.timestamps.append(now)
            if is_error:
                stats.errors += 1
                stats.error_timestamps.append(now)
            self._prune(stats, now)

    def _prune(self, stats: TierStats, now: float):
        cutoff = now - WINDOW_SECONDS
        while stats.timestamps and stats.timestamps[0] < cutoff:
            stats.timestamps.pop(0)
            stats.total -= 1
        while stats.error_timestamps and stats.error_timestamps[0] < cutoff:
            stats.error_timestamps.pop(0)
            stats.errors -= 1

    def get_error_rate(self, tenant_tier: str) -> float:
        with self._lock:
            stats = self._stats[tenant_tier]
            if stats.total == 0:
                return 0.0
            return stats.errors / stats.total

    def get_error_budget_remaining(self, tenant_tier: str) -> float:
        """Returns remaining error budget as a percentage (0-100)."""
        target = SLO_TARGETS.get(tenant_tier, 0.99)
        allowed_error_rate = 1.0 - target
        actual_error_rate = self.get_error_rate(tenant_tier)

        if allowed_error_rate == 0:
            return 0.0 if actual_error_rate > 0 else 100.0

        consumed = actual_error_rate / allowed_error_rate
        remaining = max(0.0, (1.0 - consumed) * 100.0)
        return remaining

    def get_burn_rate(self, tenant_tier: str) -> float:
        """
        Burn rate normalized to 1.0.
        > 1.0 means budget is being consumed faster than sustainable.
        """
        target = SLO_TARGETS.get(tenant_tier, 0.99)
        allowed_error_rate = 1.0 - target
        actual_error_rate = self.get_error_rate(tenant_tier)

        if allowed_error_rate == 0:
            return float("inf") if actual_error_rate > 0 else 0.0

        return actual_error_rate / allowed_error_rate

    def get_all_budgets(self) -> dict[str, dict]:
        result = {}
        for tier in SLO_TARGETS:
            result[tier] = {
                "slo_target": SLO_TARGETS[tier],
                "error_rate": self.get_error_rate(tier),
                "budget_remaining_pct": self.get_error_budget_remaining(tier),
                "burn_rate": self.get_burn_rate(tier),
            }
        return result
