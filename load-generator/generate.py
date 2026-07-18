"""
Realistic multi-tenant traffic generator.

Simulates real payment patterns:
- Multiple tenants across tiers
- Varying transaction amounts
- Burst patterns and quiet periods
- Occasional failure-inducing scenarios
"""

import os
import time
import random
import httpx
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("load-generator")

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
RPS = int(os.environ.get("REQUESTS_PER_SECOND", "5"))

TENANTS = [
    {"id": "acme-corp", "tier": "enterprise"},
    {"id": "acme-corp", "tier": "enterprise"},
    {"id": "startup-io", "tier": "pro"},
    {"id": "startup-io", "tier": "pro"},
    {"id": "small-biz", "tier": "pro"},
    {"id": "indie-dev", "tier": "free"},
    {"id": "test-user", "tier": "free"},
    {"id": "bigco-inc", "tier": "enterprise"},
]

TRANSACTION_TYPES = ["payment", "payment", "payment", "refund", "chargeback"]


def generate_transaction():
    tenant = random.choice(TENANTS)
    txn_type = random.choice(TRANSACTION_TYPES)

    # Amount distribution: mostly small, some large
    if random.random() < 0.1:
        amount = round(random.uniform(1000, 5000), 2)  # High value
    elif random.random() < 0.3:
        amount = round(random.uniform(100, 999), 2)    # Medium
    else:
        amount = round(random.uniform(5, 99), 2)       # Small

    return {
        "tenant": tenant,
        "body": {
            "amount": amount,
            "type": txn_type,
            "currency": "USD",
            "card_last4": random.choice(["4242", "5555", "1234", "9876", "0000"]),
        },
    }


def send_request(client: httpx.Client, txn: dict):
    try:
        resp = client.post(
            f"{GATEWAY_URL}/api/v1/payments",
            json=txn["body"],
            headers={
                "X-Tenant-ID": txn["tenant"]["id"],
                "X-Tenant-Tier": txn["tenant"]["tier"],
            },
            timeout=30.0,
        )
        status = "OK" if resp.status_code == 200 else f"ERR:{resp.status_code}"
        logger.info(
            f"{status} tenant={txn['tenant']['id']} "
            f"tier={txn['tenant']['tier']} "
            f"amount=${txn['body']['amount']} "
            f"type={txn['body']['type']}"
        )
    except Exception as e:
        logger.error(f"Request failed: {e}")


def main():
    logger.info(f"Starting load generator: {RPS} req/s → {GATEWAY_URL}")
    time.sleep(5)  # Wait for services to start

    with httpx.Client() as client:
        while True:
            # Normal traffic
            for _ in range(RPS):
                txn = generate_transaction()
                send_request(client, txn)
                time.sleep(1.0 / RPS)

            # Occasional burst (simulates flash sale)
            if random.random() < 0.05:
                logger.info("BURST: simulating traffic spike")
                for _ in range(RPS * 5):
                    txn = generate_transaction()
                    send_request(client, txn)
                    time.sleep(0.05)


if __name__ == "__main__":
    main()
