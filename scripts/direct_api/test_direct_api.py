#!/usr/bin/env python3
"""Diagnostic script: exercises the direct Google Ads / Merchant API clients against real
credentials and prints samples + counts, for validating scripts/direct_api/*.py against reality
before generate_board.py is ever switched over to use them. Does not write anything anywhere --
read-only. Meant to be run manually via GitHub Actions (see .github/workflows/test-direct-api.yml),
since this environment has no local credentials to test against.
"""
import sys

sys.path.insert(0, ".")
from scripts.direct_api import ads_client, merchant_client

ADS_CUSTOMER_ID = "7951690216"  # 795-169-0216, no dashes
MERCHANT_ACCOUNT_ID = "273780463"


def main():
    print("=== Google Ads: fetch_ag_status ===")
    client = ads_client.build_client()
    ag_rows = ads_client.fetch_ag_status(client, ADS_CUSTOMER_ID)
    print(f"{len(ag_rows)} rows")
    for row in ag_rows[:10]:
        print(" ", row)

    print("\n=== Google Ads: fetch_listing_group_filters ===")
    filter_rows = ads_client.fetch_listing_group_filters(client, ADS_CUSTOMER_ID)
    print(f"{len(filter_rows)} rows")
    tracked_campaigns = ("2024.3.1 P-MAX Gift-Scene", "2026.4.28 P-MAX Best-Selling", "2026.8.1 P-MAX Second-Team")
    current_rows = [r for r in filter_rows if r["campaign"] in tracked_campaigns]
    print(f"{len(current_rows)} rows in currently-tracked campaigns:")
    for row in current_rows:
        print(" ", row)

    print("\n=== DEBUG: raw asset_group_listing_group_filter rows (unfiltered) ===")
    raw_query = """
        SELECT
          campaign.name,
          asset_group.name,
          asset_group_listing_group_filter.type,
          asset_group_listing_group_filter.case_value.product_custom_attribute.index,
          asset_group_listing_group_filter.case_value.product_custom_attribute.value
        FROM asset_group_listing_group_filter
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
    """
    raw_rows = ads_client._run_search(client, ADS_CUSTOMER_ID, raw_query)
    print(f"{len(raw_rows)} raw rows")
    for row in raw_rows[:20]:
        f = row.asset_group_listing_group_filter
        print(" ", {
            "campaign": row.campaign.name,
            "asset_group": row.asset_group.name,
            "type": f.type.name,
            "attr_index": f.case_value.product_custom_attribute.index.name,
            "attr_value": f.case_value.product_custom_attribute.value,
        })

    print("\n=== Google Ads: fetch_item_performance (last 3 days) ===")
    from datetime import date, timedelta
    date_to = date.today()
    date_from = date_to - timedelta(days=2)
    perf_rows = ads_client.fetch_item_performance(client, ADS_CUSTOMER_ID, str(date_from), str(date_to))
    print(f"{len(perf_rows)} rows")
    for row in perf_rows[:10]:
        print(" ", row)

    print("\n=== Merchant API: fetch_products (raw, first 3) ===")
    credentials = merchant_client.build_credentials()
    products = merchant_client.fetch_products(credentials, MERCHANT_ACCOUNT_ID)
    print(f"{len(products)} products total")
    for p in products[:3]:
        print(" ", repr(p))

    print("\nAll checks completed without raising.")


if __name__ == "__main__":
    main()
