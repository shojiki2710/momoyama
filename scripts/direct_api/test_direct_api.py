#!/usr/bin/env python3
"""Diagnostic script: dumps the FULL real listing-group-filter configuration for every
PERFORMANCE_MAX campaign/asset group in the account, to evaluate automating generate_board.py's
hand-maintained AG_TO_LABELS/LABEL_TO_DISPLAY_AG tables from Google Ads' own config instead
(2026-08-20 investigation). Read-only. Meant to be run manually via GitHub Actions (see
.github/workflows/test-direct-api.yml), since this environment has no local credentials to test
against.
"""
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from scripts.direct_api import ads_client

ADS_CUSTOMER_ID = "7951690216"  # 795-169-0216, no dashes


def main():
    client = ads_client.build_client()

    print("=== All campaigns (name, status) ===")
    campaigns = ads_client.fetch_all_campaigns(client, ADS_CUSTOMER_ID)
    for c in campaigns:
        print(" ", c)

    print("\n=== fetch_ag_status: every AG's campaign + enabled state ===")
    ag_rows = ads_client.fetch_ag_status(client, ADS_CUSTOMER_ID)
    for row in ag_rows:
        print(" ", row)

    print("\n=== fetch_listing_group_filters: FULL dump, grouped by (campaign, asset_group) ===")
    filter_rows = ads_client.fetch_listing_group_filters(client, ADS_CUSTOMER_ID)
    print(f"{len(filter_rows)} total UNIT_INCLUDED/UNIT_EXCLUDED rows\n")

    grouped = defaultdict(list)
    for row in filter_rows:
        grouped[(row["campaign"], row["asset_group"])].append(row)

    for (campaign, asset_group), rows in sorted(grouped.items()):
        print(f"[{campaign}] / [{asset_group}]")
        for r in rows:
            kind = "INCLUDE" if r["included"] else "EXCLUDE"
            print(f"    {kind}  index={r['custom_label_index']}  value={r['custom_label_value']!r}")
    print()

    print("=== Specifically: any row whose value looks like 'excluded' or 'single' (any index) ===")
    for row in filter_rows:
        v = (row["custom_label_value"] or "").lower()
        if "exclud" in v or v == "single":
            print(" ", row)

    print("\n=== Specifically: 即納・ベストセラー AG's filter rows ===")
    for row in filter_rows:
        if row["asset_group"] == "即納・ベストセラー":
            print(" ", row)

    print("\nAll checks completed without raising.")


if __name__ == "__main__":
    main()
