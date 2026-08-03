#!/usr/bin/env python3
"""Fetch AG x product performance from Windsor.ai and render the listing group board.

Pipeline (see the Notion spec "仕様書：リスティンググループ商品ボード自動更新" for background):
  1. google_ads:      campaign, asset_group_name, asset_group_status, asset_group_id
                       -> which asset groups (AGs) are currently ENABLED.
  2. google_merchant:  product_id, product_custom_label_0, product_title, product_image_link
                       -> current product -> AG-label mapping (the source of truth; Google Ads'
                       own product-segment fields reflect click-time values, not current ones).
  3. google_ads:      date, campaign, product_item_id, cost, conversions, conversions_value
                       -> per-product, per-day performance (titles vary A/B, summed by item_id).
  4. Join 2 and 3 on item_id, keep only items whose label maps to a currently-active AG.

Step 3 fetches a rolling HISTORY_DAYS-day window at daily granularity (not pre-aggregated) so the
published page can let viewers pick any sub-range (7d/30d/90d presets or a custom range) and
re-aggregate/re-render entirely client-side -- no backend, still a static GitHub Pages site.

Two real-data quirks confirmed against the live account (2026-07-28) that the source spec did not
mention:
  - google_merchant product_id ("shopify_JP_...") and google_ads product_item_id
    ("shopify_jp_...") differ in case. All joins below normalize to lowercase.
  - asset_group_status is directly queryable as ENABLED/PAUSED -- no need for the "paused AGs
    don't appear in the results" heuristic from the spec; filtering on the field is more robust.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TEMPLATE_PATH = SCRIPT_DIR / "board_template.html"
OUTPUT_PATH = REPO_ROOT / "docs" / "index.html"
FIXTURES_DIR = SCRIPT_DIR / "dev_fixtures"

WINDSOR_BASE_URL = "https://connectors.windsor.ai"
GOOGLE_ADS_ACCOUNT = "795-169-0216"
MERCHANT_ACCOUNT = "273780463"
HISTORY_DAYS = 90  # rolling window baked into the page; bounds the custom date-range picker
DEFAULT_PRESET_DAYS = 30  # initial selection shown on page load
JST = ZoneInfo("Asia/Tokyo")

# campaign name must contain at least one of these substrings to be in scope.
CAMPAIGN_KEYWORDS = ("Gift", "Best")

# Google Ads asset_group_name -> Merchant Center custom_label_0 value(s) it corresponds to.
# Verified against the live account on 2026-07-28. The Best-Selling campaign has a single AG
# ("ベストセラー"); the 似顔絵/名入れ split is a custom-label-only distinction, not a separate AG.
AG_TO_LABELS = {
    "還暦祝い": ["60th"],
    "誕生日祝い": ["birthday"],
    "結婚祝い": ["wedding"],
    "すぐ届く": ["quick-ship"],
    "ベストセラー": ["best_seller_nigaoe", "best_seller_signed"],
}

LABEL_TO_DISPLAY_AG = {
    "60th": "還暦祝い",
    "birthday": "誕生日祝い",
    "wedding": "結婚祝い",
    "quick-ship": "すぐ届く",
    "best_seller_nigaoe": "Best-Selling(似顔絵)",
    "best_seller_signed": "Best-Selling(名入れ)",
}

# preferred column order when the AG is active; unexpected/new active AGs are appended after.
AG_DISPLAY_ORDER = ["還暦祝い", "誕生日祝い", "結婚祝い", "すぐ届く", "Best-Selling(似顔絵)", "Best-Selling(名入れ)"]

ROAS_GOOD = 400
ROAS_MID = 300


def normalize_label(label):
    if not label:
        return None
    return label.strip().lower()


class WindsorClient:
    """Thin wrapper around the Windsor.ai REST connectors API.

    NOTE: filtering (campaign contains "Gift"/"Best", asset_group_status == ENABLED, null
    item_id) is deliberately done in Python after fetching, not via REST query filters. The
    exact REST filter query-param syntax could not be live-tested from the environment this
    script was authored in (only the separate Windsor.ai MCP connector was available, which
    abstracts the raw REST call away). Fetching broad and filtering client-side sidesteps that
    unknown and mirrors what the source spec already recommends for the merchant step.
    """

    def __init__(self, api_key):
        self.api_key = api_key

    def get_data(self, connector, fields, accounts, date_from, date_to):
        params = {
            "api_key": self.api_key,
            "fields": ",".join(fields),
            "accounts": ",".join(accounts) if isinstance(accounts, (list, tuple)) else accounts,
            "date_from": date_from,
            "date_to": date_to,
        }
        resp = requests.get(f"{WINDSOR_BASE_URL}/{connector}", params=params, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected Windsor.ai response shape for {connector}: {payload!r}")
        return rows


class FixtureClient:
    """Drop-in replacement for WindsorClient that reads scripts/dev_fixtures/*.json.

    Used for local development/testing without a live Windsor.ai REST API key -- see README.
    """

    _FILES = {
        ("google_ads", ("campaign", "campaign_status", "asset_group_name", "asset_group_status", "asset_group_id")): "step1_asset_groups.json",
        ("google_merchant", ("product_id", "product_custom_label_0", "product_title", "product_image_link")): "step2_merchant_products.json",
        ("google_ads", ("date", "campaign", "product_item_id", "cost", "conversions", "conversions_value")): "step3_item_performance_daily.json",
    }

    def get_data(self, connector, fields, accounts, date_from, date_to):
        key = (connector, tuple(fields))
        filename = self._FILES.get(key)
        if not filename:
            raise RuntimeError(f"No fixture registered for {key}")
        return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


def fetch_active_labels(client, date_from, date_to):
    """An asset group only actually serves when BOTH it and its parent campaign are enabled.
    asset_group_status stays ENABLED even when the parent campaign is paused (confirmed live on
    2026-08-02: the Gift-Scene campaign was paused on 2026-07-31, but every one of its asset
    groups still reported asset_group_status=ENABLED) -- checking asset_group_status alone
    silently over-counts AGs whose whole campaign has been paused."""
    rows = client.get_data(
        "google_ads",
        ["campaign", "campaign_status", "asset_group_name", "asset_group_status", "asset_group_id"],
        accounts=[GOOGLE_ADS_ACCOUNT],
        date_from=date_from,
        date_to=date_to,
    )
    active_labels = set()
    active_ag_names = set()
    for row in rows:
        campaign = row.get("campaign") or ""
        if not any(k in campaign for k in CAMPAIGN_KEYWORDS):
            continue
        campaign_status = (row.get("campaign_status") or "").upper()
        ag_status = (row.get("asset_group_status") or "").upper()
        if campaign_status != "ENABLED" or ag_status != "ENABLED":
            continue
        ag_name = row.get("asset_group_name")
        active_ag_names.add(ag_name)
        for label in AG_TO_LABELS.get(ag_name, []):
            active_labels.add(label)
    return active_labels, active_ag_names


def fetch_product_labels(client, date_from, date_to):
    rows = client.get_data(
        "google_merchant",
        ["product_id", "product_custom_label_0", "product_title", "product_image_link"],
        accounts=[MERCHANT_ACCOUNT],
        date_from=date_from,
        date_to=date_to,
    )
    mapping = {}
    for row in rows:
        pid = row.get("product_id")
        if not pid:
            continue
        mapping[pid.lower()] = {
            "label": normalize_label(row.get("product_custom_label_0")),
            "title": row.get("product_title"),
            "image": row.get("product_image_link"),
        }
    return mapping


def fetch_item_performance(client, date_from, date_to):
    """Returns {item_id: {"by_date": {date_str: {cost, conversions, value}}}}, daily granularity."""
    rows = client.get_data(
        "google_ads",
        ["date", "campaign", "product_item_id", "cost", "conversions", "conversions_value"],
        accounts=[GOOGLE_ADS_ACCOUNT],
        date_from=date_from,
        date_to=date_to,
    )
    per_item = {}
    for row in rows:
        campaign = row.get("campaign") or ""
        item_id = row.get("product_item_id")
        date = row.get("date")
        if not item_id or not date or not any(k in campaign for k in CAMPAIGN_KEYWORDS):
            continue
        key = item_id.lower()
        by_date = per_item.setdefault(key, {})
        day = by_date.setdefault(date, {"cost": 0.0, "conversions": 0.0, "value": 0.0})
        day["cost"] += float(row.get("cost") or 0)
        day["conversions"] += float(row.get("conversions") or 0)
        day["value"] += float(row.get("conversions_value") or 0)
    return per_item


def build_products(active_labels, product_labels, performance, date_list):
    """Each product carries cost/cv/value as arrays aligned index-for-index with date_list, so
    the page can sum any contiguous slice client-side without refetching anything."""
    products = []
    for item_id, by_date in performance.items():
        info = product_labels.get(item_id)
        if not info or info["label"] not in active_labels:
            continue
        total_cost = sum(d["cost"] for d in by_date.values())
        if total_cost <= 0:
            continue
        cost_arr, cv_arr, value_arr = [], [], []
        for d in date_list:
            day = by_date.get(d)
            cost_arr.append(round(day["cost"], 2) if day else 0)
            cv_arr.append(round(day["conversions"], 4) if day else 0)
            value_arr.append(round(day["value"], 2) if day else 0)
        products.append({
            "ag": LABEL_TO_DISPLAY_AG[info["label"]],
            "title": info["title"] or item_id,
            "id": item_id,
            "img": info["image"],
            "cost": cost_arr,
            "cv": cv_arr,
            "value": value_arr,
        })
    return products


def render_html(products, date_list, active_ag_names, generated_at):
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    present_ags = {p["ag"] for p in products}
    ag_order = [ag for ag in AG_DISPLAY_ORDER if ag in present_ags]
    ag_order += sorted(present_ags - set(ag_order))

    def js_string_escape(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def js_json(obj):
        # guard against a title/id containing "</script" and breaking out of the inline <script> tag
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    html = template
    html = html.replace("__DATES_JSON__", js_json(date_list))
    html = html.replace("__PRODUCTS_JSON__", js_json(products))
    html = html.replace("__AG_ORDER_JSON__", js_json(ag_order))
    html = html.replace("__GENERATED_AT__", js_string_escape(generated_at))
    html = html.replace("__ROAS_GOOD__", str(ROAS_GOOD))
    html = html.replace("__ROAS_MID__", str(ROAS_MID))
    html = html.replace("__DEFAULT_PRESET_DAYS__", str(DEFAULT_PRESET_DAYS))
    return html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures", action="store_true",
        help="use local scripts/dev_fixtures/*.json instead of calling the live Windsor.ai REST API",
    )
    parser.add_argument(
        "--out", type=Path, default=OUTPUT_PATH,
        help=f"output HTML path (default: {OUTPUT_PATH.relative_to(REPO_ROOT)})",
    )
    args = parser.parse_args()

    now_jst = datetime.now(JST)
    date_to = now_jst.date()
    date_from = date_to - timedelta(days=HISTORY_DAYS - 1)
    date_list = [str(date_from + timedelta(days=i)) for i in range(HISTORY_DAYS)]

    if args.fixtures:
        client = FixtureClient()
    else:
        api_key = os.environ.get("WINDSOR_API_KEY")
        if not api_key:
            sys.exit("WINDSOR_API_KEY is not set (expected as a GitHub Actions secret / env var).")
        client = WindsorClient(api_key)

    active_labels, active_ag_names = fetch_active_labels(client, str(date_from), str(date_to))
    product_labels = fetch_product_labels(client, str(date_from), str(date_to))
    performance = fetch_item_performance(client, str(date_from), str(date_to))
    products = build_products(active_labels, product_labels, performance, date_list)

    if not products:
        sys.exit(
            "No products resolved after joining -- refusing to overwrite the board with an "
            "empty page. Check WINDSOR_API_KEY / account IDs / AG_TO_LABELS mapping."
        )

    generated_at = now_jst.strftime("%Y-%m-%d %H:%M JST")
    html = render_html(products, date_list, active_ag_names, generated_at)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Wrote {args.out} ({len(products)} products, {len(active_ag_names)} active AGs, {HISTORY_DAYS}d history).")


if __name__ == "__main__":
    main()
