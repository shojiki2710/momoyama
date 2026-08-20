#!/usr/bin/env python3
"""Fetch AG x product performance directly from Google Ads API / Merchant API and render the
listing group board.

Switched from Windsor.ai to direct API access on 2026-08-05 (see scripts/direct_api/) -- driven by
wanting to drop the Windsor subscription and to read listing-group-filter configuration Windsor
could not expose. DirectApiClient below adapts the direct API clients to the exact same
.get_data(connector, fields, accounts, date_from, date_to) shape WindsorClient used to have, so
everything below this point (fetch_ag_status, fetch_item_performance, build_products, etc.) is
unchanged from the Windsor-era version and the local --fixtures dev fixtures still apply as-is.

Pipeline (see the Notion spec "仕様書：リスティンググループ商品ボード自動更新" for background):
  1. google_ads:      campaign, campaign_status, asset_group_name, asset_group_status, asset_group_id
                       -> which asset groups (AGs) are CURRENTLY enabled (both the AG itself and
                       its parent campaign -- see quirk below). Used only for a "現在稼働中/一時
                       停止中" status badge per column, NOT to decide which columns can appear.
  2. google_merchant:  product_id, product_custom_label_0, product_title, product_image_link
                       -> current product -> AG-label mapping (the source of truth; Google Ads'
                       own product-segment fields reflect click-time values, not current ones).
  3. google_ads:      date, campaign, product_item_id, cost, conversions, conversions_value
                       -> per-product, per-day performance (titles vary A/B, summed by item_id).
  4. Join 2 and 3 on item_id, keeping every item with a recognized label regardless of its AG's
     current status. Which AG columns are actually shown for a given viewer-selected date range
     is decided client-side, per range, based on whether that AG had any spend in it -- so
     pausing a campaign doesn't erase its pre-pause history from the board (see board_template.html).

Step 3 fetches a rolling HISTORY_DAYS-day window at daily granularity (not pre-aggregated) so the
published page can let viewers pick any sub-range (7d/30d/90d presets or a custom range) and
re-aggregate/re-render entirely client-side -- no backend, still a static GitHub Pages site.

Real-data quirks confirmed against the live account that the source spec did not mention:
  - google_merchant product_id ("shopify_JP_...") and google_ads product_item_id
    ("shopify_jp_...") differ in case (2026-07-28). All joins below normalize to lowercase.
  - asset_group_status stays ENABLED even when the parent campaign is paused (2026-08-03: the
    Gift-Scene campaign was paused on 2026-07-31, but its asset groups kept reporting
    asset_group_status=ENABLED) -- campaign_status must be checked too.
  - A brand-new campaign can silently fall outside CAMPAIGN_KEYWORDS entirely (2026-08-05: the
    "Second-Team" campaign, launched 2026-08-01, went unnoticed for days).

Every one of the quirks above was found only because a human happened to notice the board didn't
match reality and asked for it to be investigated -- the pipeline had no way to notice on its
own. audit_structure() exists to close that gap: every run, it diffs what the account actually
contains (campaigns / asset groups / product custom labels) against what this file knows how to
handle (CAMPAIGN_KEYWORDS / AG_TO_LABELS / LABEL_TO_DISPLAY_AG), and surfaces anything unrecognized
as a warning banner on the board itself plus a log line -- instead of the old behavior of quietly
`continue`-ing past it. It doesn't auto-fix anything: deciding how a new campaign/AG/label should
be categorized is a judgment call about the business, not something to infer from data.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TEMPLATE_PATH = SCRIPT_DIR / "board_template.html"
OUTPUT_PATH = REPO_ROOT / "docs" / "index.html"
FIXTURES_DIR = SCRIPT_DIR / "dev_fixtures"

GOOGLE_ADS_ACCOUNT = "795-169-0216"  # DirectApiClient strips dashes before calling the Ads API
MERCHANT_ACCOUNT = "273780463"
SHOPIFY_SHOP_DOMAIN = "momoyama7245.myshopify.com"
HISTORY_DAYS = 90  # rolling window baked into the page; bounds the custom date-range picker
DEFAULT_PRESET_DAYS = 30  # initial selection shown on page load
JST = ZoneInfo("Asia/Tokyo")

# campaign name must contain at least one of these substrings to be in scope.
CAMPAIGN_KEYWORDS = ("Gift", "Best", "Second-Team")

# Second-Team (found 2026-08-05: a new campaign since 2026-08-01, missed until then because its
# name didn't match the old CAMPAIGN_KEYWORDS) doesn't target by custom_label_0 the way every
# other campaign does -- its one asset group ("２軍") spans products with mixed/no labels
# (confirmed: null, "Quick-Ship", "60th", "wedding" all present). So it can't be folded into the
# label-based AG_TO_LABELS/LABEL_TO_DISPLAY_AG scheme below without silently merging its spend
# into unrelated AGs' numbers for shared item_ids. It gets its own fixed bucket instead --
# see fetch_item_performance/build_products.
SECOND_TEAM_CAMPAIGN_KEYWORD = "Second-Team"
SECOND_TEAM_RAW_AG_NAME = "２軍"
SECOND_TEAM_DISPLAY_AG = "Second-Team(２軍)"

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
AG_DISPLAY_ORDER = ["還暦祝い", "誕生日祝い", "結婚祝い", "すぐ届く", "Best-Selling(似顔絵)", "Best-Selling(名入れ)", SECOND_TEAM_DISPLAY_AG]

# raw AG name -> the campaign it lives under, for the campaign-level ROAS tier between the
# account-wide total and the per-AG columns (added 2026-08-18). Second-Team gets its own fixed
# entry below since (like its display-AG mapping) it isn't part of the label-based scheme.
RAW_AG_TO_CAMPAIGN = {
    "還暦祝い": "Gift-Scene",
    "誕生日祝い": "Gift-Scene",
    "結婚祝い": "Gift-Scene",
    "すぐ届く": "Gift-Scene",
    "ベストセラー": "Best-Selling",
}
CAMPAIGN_DISPLAY_ORDER = ["Best-Selling", "Second-Team", "Gift-Scene"]

ROAS_GOOD = 400
ROAS_MID = 300


def normalize_label(label):
    if not label:
        return None
    return label.strip().lower()


class DirectApiClient:
    """Adapts scripts/direct_api/{ads_client,merchant_client}.py to the exact same
    .get_data(connector, fields, accounts, date_from, date_to) shape the old WindsorClient had,
    so every fetch_* function below is unchanged from the Windsor-era version -- only this class
    and FixtureClient's underlying JSON needed to line up with reality (and the fixtures already
    used these same field names, so they needed no changes either).

    The import is lazy (inside __init__, not at module level) so `--fixtures` runs don't require
    the google-ads / google-shopping-merchant-products packages to be installed at all.
    """

    def __init__(self):
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.direct_api import ads_client, merchant_client, shopify_client

        self._ads = ads_client
        self._merchant = merchant_client
        self._shopify = shopify_client
        self._ads_client = ads_client.build_client()
        self._merchant_credentials = merchant_client.build_credentials()
        self._shopify_token = shopify_client.build_credentials(SHOPIFY_SHOP_DOMAIN)

    def get_data(self, connector, fields, accounts, date_from, date_to):
        fields = tuple(fields)
        account = accounts[0] if isinstance(accounts, (list, tuple)) else accounts

        if connector == "google_ads":
            customer_id = account.replace("-", "")
            if fields == ("campaign", "campaign_status", "asset_group_name", "asset_group_status", "asset_group_id"):
                rows = self._ads.fetch_ag_status(self._ads_client, customer_id)
                return [
                    {
                        "campaign": r["campaign"],
                        "campaign_status": r["campaign_status"],
                        "asset_group_name": r["asset_group"],
                        "asset_group_status": r["asset_group_status"],
                    }
                    for r in rows
                ]
            if fields == ("campaign", "campaign_status"):
                return self._ads.fetch_all_campaigns(self._ads_client, customer_id)
            if fields == ("date", "campaign", "product_item_id", "cost", "conversions", "conversions_value"):
                return self._ads.fetch_item_performance(self._ads_client, customer_id, date_from, date_to)
            if fields == ("date", "cost", "conversions_value"):
                return self._ads.fetch_account_daily_totals(self._ads_client, customer_id, date_from, date_to)

        elif connector == "google_merchant":
            if fields == ("product_id", "product_custom_label_0", "product_title", "product_image_link"):
                products = self._merchant.fetch_products(self._merchant_credentials, account)
                normalized = [self._merchant.normalize_product(p) for p in products]
                return [
                    {
                        "product_id": n["product_id"],
                        "product_custom_label_0": n["label"],
                        "product_title": n["title"],
                        "product_image_link": n["image"],
                    }
                    for n in normalized
                ]

        elif connector == "shopify":
            if fields == ("date", "total_sales"):
                totals = self._shopify.fetch_daily_total_sales(self._shopify_token, account, date_from, date_to)
                return [{"date": d, "total_sales": v} for d, v in totals.items()]

        raise RuntimeError(f"DirectApiClient has no mapping for connector={connector!r} fields={fields!r}")


class FixtureClient:
    """Drop-in replacement for DirectApiClient that reads scripts/dev_fixtures/*.json.

    Used for local development/testing without live Google Ads / Merchant API credentials --
    see README. The fixture files were captured with Windsor-era field names, which is fine:
    DirectApiClient reshapes its own responses into those exact same names, so nothing here needed
    to change when the data source switched.
    """

    _FILES = {
        ("google_ads", ("campaign", "campaign_status")): "step0_all_campaigns.json",
        ("google_ads", ("campaign", "campaign_status", "asset_group_name", "asset_group_status", "asset_group_id")): "step1_asset_groups.json",
        ("google_merchant", ("product_id", "product_custom_label_0", "product_title", "product_image_link")): "step2_merchant_products.json",
        ("google_ads", ("date", "campaign", "product_item_id", "cost", "conversions", "conversions_value")): "step3_item_performance_daily.json",
        ("google_ads", ("date", "cost", "conversions_value")): "step4_account_daily_totals.json",
        ("shopify", ("date", "total_sales")): "step5_shopify_daily_sales.json",
    }

    def get_data(self, connector, fields, accounts, date_from, date_to):
        key = (connector, tuple(fields))
        filename = self._FILES.get(key)
        if not filename:
            raise RuntimeError(f"No fixture registered for {key}")
        return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


def fetch_ag_status(client, date_from, date_to):
    """raw asset_group_name -> True/False (currently enabled), requiring BOTH the AG itself and
    its parent campaign to be enabled. asset_group_status alone stays ENABLED even when the
    parent campaign is paused (confirmed live on 2026-08-02: the Gift-Scene campaign was paused
    on 2026-07-31, but every one of its asset groups still reported asset_group_status=ENABLED).

    Used only for the "現在稼働中/一時停止中" status badge per column -- not to decide which
    columns can appear (see build_products).

    A name can legitimately appear more than once across different campaigns (confirmed
    2026-08-05: "ベストセラー" exists both as a paused leftover under Gift-Scene and as the real
    active one under Best-Selling) -- treat as enabled if ANY matching row is enabled.

    Also returns unrecognized_ags: asset groups seen inside in-scope campaigns that aren't in
    AG_TO_LABELS or the Second-Team AG -- feeds the structure audit (see audit_structure)."""
    rows = client.get_data(
        "google_ads",
        ["campaign", "campaign_status", "asset_group_name", "asset_group_status", "asset_group_id"],
        accounts=[GOOGLE_ADS_ACCOUNT],
        date_from=date_from,
        date_to=date_to,
    )
    known_ag_names = set(AG_TO_LABELS) | {SECOND_TEAM_RAW_AG_NAME}
    status = {}
    unrecognized_ags = []
    for row in rows:
        campaign = row.get("campaign") or ""
        if not any(k in campaign for k in CAMPAIGN_KEYWORDS):
            continue
        campaign_status = (row.get("campaign_status") or "").upper()
        ag_status = (row.get("asset_group_status") or "").upper()
        ag_name = row.get("asset_group_name")
        enabled = campaign_status == "ENABLED" and ag_status == "ENABLED"
        status[ag_name] = status.get(ag_name, False) or enabled
        if ag_name not in known_ag_names:
            unrecognized_ags.append({
                "campaign": campaign, "asset_group": ag_name, "status": ag_status, "enabled": enabled,
            })
    return status, unrecognized_ags


def fetch_all_campaigns(client, date_from, date_to):
    """Every campaign in the account, no keyword filter -- feeds the structure audit below."""
    rows = client.get_data(
        "google_ads",
        ["campaign", "campaign_status"],
        accounts=[GOOGLE_ADS_ACCOUNT],
        date_from=date_from,
        date_to=date_to,
    )
    seen = {}
    for row in rows:
        name = row.get("campaign")
        if not name:
            continue
        seen[name] = (row.get("campaign_status") or "").upper()
    return seen


def audit_structure(all_campaigns, unrecognized_ags, product_labels, performance):
    """Surface anything the pipeline doesn't know how to categorize AND that's actually live right
    now, instead of silently dropping it. Added 2026-08-05 after three separate real incidents
    where new/changed account structure (a paused parent campaign, a brand-new campaign, an AG
    spanning mixed labels) went undetected until a human happened to notice the board didn't match
    reality. This can't auto-fix anything -- the account structure is a judgment call only a human
    can make (see AG_TO_LABELS/LABEL_TO_DISPLAY_AG/SECOND_TEAM_* above) -- it only makes gaps
    visible.

    Deliberately restricted to things that could be silently costing money or converting right now
    without showing up anywhere on the board: an ENABLED campaign/AG the pipeline doesn't route
    anywhere, or a custom label with real recent spend that isn't mapped. A PAUSED/REMOVED campaign
    or AG isn't spending, so nothing about it is missing from the board's current numbers -- and if
    it gets re-enabled later, this audit catches it on the very next run. Narrowed 2026-08-07 after
    the warning list grew to 20+ lines of long-dormant account debris, mostly obscuring the couple
    of warnings that actually mattered."""
    warnings = []

    for name, status in sorted(all_campaigns.items()):
        if status != "ENABLED":
            continue
        if not any(k in name for k in CAMPAIGN_KEYWORDS):
            warnings.append(f"未対応キャンペーン「{name}」(状態: {status}) はボードの対象外です。")

    seen_ags = set()
    for item in unrecognized_ags:
        if not item["enabled"]:
            continue
        key = (item["campaign"], item["asset_group"])
        if key in seen_ags:
            continue
        seen_ags.add(key)
        warnings.append(
            f"未対応アセットグループ「{item['asset_group']}」"
            f"(キャンペーン「{item['campaign']}」、状態: {item['status']}) はラベル対応表にありません。"
        )

    labels_with_spend = set()
    for (_bucket, item_id), by_date in performance.items():
        info = product_labels.get(item_id)
        if not info or not info["label"]:
            continue
        if sum(d["cost"] for d in by_date.values()) > 0:
            labels_with_spend.add(info["label"])

    seen_labels = set()
    for label in sorted(labels_with_spend):
        if label not in LABEL_TO_DISPLAY_AG and label not in seen_labels:
            seen_labels.add(label)
            warnings.append(f"未対応カスタムラベル「{label}」の商品に広告費用が発生しています。")

    return warnings


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
    """Returns {(bucket, item_id): {date_str: {cost, conversions, value}}}, daily granularity.

    bucket is "second_team" for the Second-Team campaign, "labeled" for everything else (Gift-
    Scene/Best-Selling). Keeping them apart -- rather than merging straight into a single
    item_id-keyed dict -- matters because the same item_id can run under both simultaneously
    (confirmed 2026-08-05): merging would silently mix Second-Team's spend into whichever AG the
    item's custom_label_0 happens to map to."""
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
        if not item_id or not date:
            continue
        if SECOND_TEAM_CAMPAIGN_KEYWORD in campaign:
            bucket = "second_team"
        elif any(k in campaign for k in ("Gift", "Best")):
            bucket = "labeled"
        else:
            continue
        key = (bucket, item_id.lower())
        by_date = per_item.setdefault(key, {})
        day = by_date.setdefault(date, {"cost": 0.0, "conversions": 0.0, "value": 0.0})
        day["cost"] += float(row.get("cost") or 0)
        day["conversions"] += float(row.get("conversions") or 0)
        day["value"] += float(row.get("conversions_value") or 0)
    return per_item


def fetch_account_daily_totals(client, date_from, date_to):
    """{date_str: {cost, value}}, TRUE account-wide totals (every campaign, not just ones the
    board can classify by custom_label_0). Feeds the site-wide ROAS shown in the hero banner --
    added 2026-08-20 after realizing that number was silently computed from classified spend only
    (fetch_item_performance's join), understating true cost by whatever ran under an unmapped
    label. See ads_client.fetch_account_daily_totals for why the `customer` resource is the right
    source for this (independent of any product/AG join)."""
    rows = client.get_data(
        "google_ads",
        ["date", "cost", "conversions_value"],
        accounts=[GOOGLE_ADS_ACCOUNT],
        date_from=date_from,
        date_to=date_to,
    )
    totals = {}
    for row in rows:
        date = row.get("date")
        if not date:
            continue
        day = totals.setdefault(date, {"cost": 0.0, "value": 0.0})
        day["cost"] += float(row.get("cost") or 0)
        day["value"] += float(row.get("conversions_value") or 0)
    return totals


def fetch_shopify_daily_sales(client, date_from, date_to):
    """{date_str: total_sales}, the whole store's own revenue (every order, every channel --
    not just Google Ads-attributed). Feeds 売上高広告費率 (ad cost / total store sales), added
    2026-08-20 to show what fraction of ALL store revenue advertising cost consumed, as a
    counterpart to ROAS (which only measures return on the ad spend itself)."""
    rows = client.get_data(
        "shopify",
        ["date", "total_sales"],
        accounts=[SHOPIFY_SHOP_DOMAIN],
        date_from=date_from,
        date_to=date_to,
    )
    totals = {}
    for row in rows:
        date = row.get("date")
        if not date:
            continue
        totals[date] = totals.get(date, 0.0) + float(row.get("total_sales") or 0)
    return totals


def build_products(product_labels, performance, date_list):
    """Each product carries cost/cv/value as arrays aligned index-for-index with date_list, so
    the page can sum any contiguous slice client-side without refetching anything.

    Deliberately NOT filtered by current AG/campaign active status: a board meant to review
    account-wide performance shouldn't silently drop an AG's entire history the moment it gets
    paused (confirmed as a real problem on 2026-08-03 -- pausing the Gift-Scene campaign made
    even its pre-pause history vanish from the board). Which AG columns are worth showing for a
    given viewer-selected date range is instead decided client-side, per range, based on whether
    that AG actually had spend in it -- see board_template.html."""
    products = []
    for (bucket, item_id), by_date in performance.items():
        info = product_labels.get(item_id)
        if not info:
            continue
        if bucket == "second_team":
            display_ag = SECOND_TEAM_DISPLAY_AG
        elif info["label"] in LABEL_TO_DISPLAY_AG:
            display_ag = LABEL_TO_DISPLAY_AG[info["label"]]
        else:
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
            "ag": display_ag,
            "title": info["title"] or item_id,
            "id": item_id,
            "img": info["image"],
            "cost": cost_arr,
            "cv": cv_arr,
            "value": value_arr,
        })
    return products


def build_ag_status(raw_ag_status):
    """Display-AG-name -> "ENABLED"/"PAUSED", reflecting the account's CURRENT state (used only
    for a status badge on each column -- no longer used to decide which columns can appear)."""
    status = {}
    for raw_ag_name, labels in AG_TO_LABELS.items():
        current = "ENABLED" if raw_ag_status.get(raw_ag_name) else "PAUSED"
        for label in labels:
            status[LABEL_TO_DISPLAY_AG[label]] = current
    status[SECOND_TEAM_DISPLAY_AG] = "ENABLED" if raw_ag_status.get(SECOND_TEAM_RAW_AG_NAME) else "PAUSED"
    return status


def build_campaign_of_ag():
    """Display-AG-name -> campaign name, for the client-side campaign-level ROAS tier.
    Derived from RAW_AG_TO_CAMPAIGN/AG_TO_LABELS/LABEL_TO_DISPLAY_AG rather than hand-duplicating
    the mapping, so it can't drift out of sync with those. A display AG with no known campaign
    (e.g. a brand-new AG the structure audit hasn't been taught yet) is simply absent here --
    the client groups those under a catch-all "その他" bucket rather than dropping them."""
    campaign_of_ag = {}
    for raw_ag_name, labels in AG_TO_LABELS.items():
        campaign = RAW_AG_TO_CAMPAIGN.get(raw_ag_name)
        if not campaign:
            continue
        for label in labels:
            campaign_of_ag[LABEL_TO_DISPLAY_AG[label]] = campaign
    campaign_of_ag[SECOND_TEAM_DISPLAY_AG] = "Second-Team"
    return campaign_of_ag


def render_html(
    products, date_list, raw_ag_status, structure_warnings, generated_at,
    account_cost, account_value, shop_sales,
):
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    present_ags = {p["ag"] for p in products}
    ag_order = [ag for ag in AG_DISPLAY_ORDER if ag in present_ags]
    ag_order += sorted(present_ags - set(ag_order))
    ag_status = build_ag_status(raw_ag_status)
    campaign_of_ag = build_campaign_of_ag()

    def js_string_escape(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def js_json(obj):
        # guard against a title/id containing "</script" and breaking out of the inline <script> tag
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    html = template
    html = html.replace("__DATES_JSON__", js_json(date_list))
    html = html.replace("__PRODUCTS_JSON__", js_json(products))
    html = html.replace("__AG_ORDER_JSON__", js_json(ag_order))
    html = html.replace("__AG_STATUS_JSON__", js_json(ag_status))
    html = html.replace("__CAMPAIGN_OF_AG_JSON__", js_json(campaign_of_ag))
    html = html.replace("__CAMPAIGN_ORDER_JSON__", js_json(CAMPAIGN_DISPLAY_ORDER))
    html = html.replace("__ACCOUNT_COST_JSON__", js_json(account_cost))
    html = html.replace("__ACCOUNT_VALUE_JSON__", js_json(account_value))
    html = html.replace("__SHOP_SALES_JSON__", js_json(shop_sales))
    html = html.replace("__STRUCTURE_WARNINGS_JSON__", js_json(structure_warnings))
    html = html.replace("__GENERATED_AT__", js_string_escape(generated_at))
    html = html.replace("__ROAS_GOOD__", str(ROAS_GOOD))
    html = html.replace("__ROAS_MID__", str(ROAS_MID))
    html = html.replace("__DEFAULT_PRESET_DAYS__", str(DEFAULT_PRESET_DAYS))
    return html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures", action="store_true",
        help="use local scripts/dev_fixtures/*.json instead of calling the live Google Ads / Merchant API",
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
        required_env = (
            "ADS_DEVELOPER_TOKEN", "ADS_CLIENT_ID", "ADS_CLIENT_SECRET",
            "ADS_REFRESH_TOKEN", "GCP_SERVICE_ACCOUNT_JSON", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
        )
        missing = [v for v in required_env if not os.environ.get(v)]
        if missing:
            sys.exit(f"Missing required environment variable(s): {', '.join(missing)} (expected as GitHub Actions secrets).")
        client = DirectApiClient()

    raw_ag_status, unrecognized_ags = fetch_ag_status(client, str(date_from), str(date_to))
    all_campaigns = fetch_all_campaigns(client, str(date_from), str(date_to))
    product_labels = fetch_product_labels(client, str(date_from), str(date_to))
    performance = fetch_item_performance(client, str(date_from), str(date_to))
    products = build_products(product_labels, performance, date_list)
    account_daily_totals = fetch_account_daily_totals(client, str(date_from), str(date_to))
    account_cost = [round(account_daily_totals.get(d, {}).get("cost", 0), 2) for d in date_list]
    account_value = [round(account_daily_totals.get(d, {}).get("value", 0), 2) for d in date_list]
    shopify_daily_sales = fetch_shopify_daily_sales(client, str(date_from), str(date_to))
    shop_sales = [round(shopify_daily_sales.get(d, 0), 2) for d in date_list]

    if not products:
        sys.exit(
            "No products resolved after joining -- refusing to overwrite the board with an "
            "empty page. Check the ADS_*/GCP_SERVICE_ACCOUNT_JSON secrets, account IDs, and "
            "AG_TO_LABELS mapping."
        )

    structure_warnings = audit_structure(all_campaigns, unrecognized_ags, product_labels, performance)
    if structure_warnings:
        print("WARNING: structure audit found account changes the pipeline doesn't recognize:")
        for w in structure_warnings:
            print(f"  - {w}")

    generated_at = now_jst.strftime("%Y-%m-%d %H:%M JST")
    html = render_html(
        products, date_list, raw_ag_status, structure_warnings, generated_at,
        account_cost, account_value, shop_sales,
    )

    active_count = sum(1 for v in raw_ag_status.values() if v)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Wrote {args.out} ({len(products)} products, {active_count} active AGs, {HISTORY_DAYS}d history).")


if __name__ == "__main__":
    main()
