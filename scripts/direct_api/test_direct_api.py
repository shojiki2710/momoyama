#!/usr/bin/env python3
"""Diagnostic script: finds why the Second-Team campaign's true total (from
fetch_campaign_daily_totals) doesn't match the sum of product cards shown under the "２軍" AG
column (2026-08-21 investigation) -- likely item_ids with real ad spend that don't resolve to a
Merchant Center product record via fetch_product_labels (deleted/mismatched product_id), which
build_products() silently drops. Read-only. Meant to be run manually via GitHub Actions (see
.github/workflows/test-direct-api.yml), since this environment has no local credentials to test
against.
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from scripts.direct_api import ads_client, merchant_client

ADS_CUSTOMER_ID = "7951690216"  # 795-169-0216, no dashes
MERCHANT_ACCOUNT_ID = "273780463"
SECOND_TEAM_CAMPAIGN_KEYWORD = "Second-Team"
HISTORY_DAYS = 90


def main():
    date_to = date.today()
    date_from = date_to - timedelta(days=HISTORY_DAYS - 1)

    print("=== Fetching Merchant Center products ===")
    credentials = merchant_client.build_credentials()
    raw_products = merchant_client.fetch_products(credentials, MERCHANT_ACCOUNT_ID)
    product_labels = {}
    for p in raw_products:
        n = merchant_client.normalize_product(p)
        if n["product_id"]:
            product_labels[n["product_id"].lower()] = n
    print(f"{len(product_labels)} Merchant Center products (lowercased item_id keys)")

    print("\n=== Fetching Google Ads item performance (90d) ===")
    client = ads_client.build_client()
    perf_rows = ads_client.fetch_item_performance(client, ADS_CUSTOMER_ID, str(date_from), str(date_to))
    print(f"{len(perf_rows)} raw performance rows")

    second_team_cost_by_item = {}
    for row in perf_rows:
        if SECOND_TEAM_CAMPAIGN_KEYWORD not in (row["campaign"] or ""):
            continue
        item_id = (row["product_item_id"] or "").lower()
        if not item_id:
            continue
        second_team_cost_by_item[item_id] = second_team_cost_by_item.get(item_id, 0.0) + row["cost"]

    total_second_team_cost = sum(second_team_cost_by_item.values())
    print(f"\n{len(second_team_cost_by_item)} distinct item_ids with Second-Team spend, total cost={total_second_team_cost:.2f}")

    unresolved = {
        item_id: cost for item_id, cost in second_team_cost_by_item.items()
        if item_id not in product_labels
    }
    unresolved_cost = sum(unresolved.values())
    print(f"\n=== {len(unresolved)} item_ids with Second-Team spend but NO Merchant Center product match ===")
    print(f"Total unresolved cost: {unresolved_cost:.2f} ({100*unresolved_cost/total_second_team_cost:.1f}% of Second-Team total)")
    for item_id, cost in sorted(unresolved.items(), key=lambda kv: -kv[1]):
        print(f"  {item_id}  cost={cost:.2f}")

    print("\n=== Resolved item_ids, showing their current custom_label_0 value ===")
    resolved_by_label = {}
    for item_id, cost in second_team_cost_by_item.items():
        info = product_labels.get(item_id)
        if not info:
            continue
        label = info["label"]
        resolved_by_label.setdefault(label, {"cost": 0.0, "count": 0})
        resolved_by_label[label]["cost"] += cost
        resolved_by_label[label]["count"] += 1
    for label, d in sorted(resolved_by_label.items(), key=lambda kv: -kv[1]["cost"]):
        print(f"  label={label!r}  items={d['count']}  cost={d['cost']:.2f}")

    print("\nAll checks completed without raising.")


if __name__ == "__main__":
    main()
