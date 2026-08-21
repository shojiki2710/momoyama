#!/usr/bin/env python3
"""Diagnostic script: dumps fetch_listing_group_filters() output for ONLY the currently-relevant,
ENABLED asset groups under the three in-scope campaigns (Gift-Scene/Best-Selling/Second-Team) --
2026-08-21 investigation into building a "is this product currently targeted by this AG's live
config" feature. Read-only. Meant to be run manually via GitHub Actions (see
.github/workflows/test-direct-api.yml), since this environment has no local credentials to test
against.
"""
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from scripts.direct_api import ads_client

ADS_CUSTOMER_ID = "7951690216"  # 795-169-0216, no dashes
IN_SCOPE_CAMPAIGN_KEYWORDS = ("Gift-Scene", "Best-Selling", "Second-Team")


def main():
    client = ads_client.build_client()

    print("=== fetch_ag_status: which AGs under in-scope campaigns are currently ENABLED ===")
    ag_rows = ads_client.fetch_ag_status(client, ADS_CUSTOMER_ID)
    enabled_pairs = set()
    for row in ag_rows:
        if not any(k in row["campaign"] for k in IN_SCOPE_CAMPAIGN_KEYWORDS):
            continue
        enabled = row["campaign_status"] == "ENABLED" and row["asset_group_status"] == "ENABLED"
        marker = "ENABLED " if enabled else "paused  "
        print(f"  {marker} [{row['campaign']}] / [{row['asset_group']}]")
        if enabled:
            enabled_pairs.add((row["campaign"], row["asset_group"]))

    print(f"\n{len(enabled_pairs)} currently-enabled (campaign, asset_group) pairs in scope\n")

    print("=== fetch_listing_group_filters, filtered to those enabled pairs ===")
    filter_rows = ads_client.fetch_listing_group_filters(client, ADS_CUSTOMER_ID)
    grouped = defaultdict(list)
    for row in filter_rows:
        key = (row["campaign"], row["asset_group"])
        if key in enabled_pairs:
            grouped[key].append(row)

    for key in sorted(enabled_pairs):
        rows = grouped.get(key, [])
        print(f"\n[{key[0]}] / [{key[1]}]  ({len(rows)} UNIT nodes)")
        if not rows:
            print("    (no UNIT_INCLUDED/UNIT_EXCLUDED rows at all -- unexpected, investigate)")
        for r in rows:
            kind = "INCLUDE" if r["included"] else "EXCLUDE"
            label_desc = f"index={r['custom_label_index']} value={r['custom_label_value']!r}" if r["custom_label_value"] else "(catch-all: no specific value)"
            print(f"    {kind}  {label_desc}")

    print("\nAll checks completed without raising.")


if __name__ == "__main__":
    main()
