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
    for row in filter_rows[:15]:
        print(" ", row)

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
