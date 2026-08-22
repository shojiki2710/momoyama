"""Google Ads API access for the direct-API pipeline (replaces Windsor.ai's google_ads connector).

Live-verified against the real account on 2026-08-05 via scripts/direct_api/test_direct_api.py
(run through GitHub Actions, since this environment has no local Google Ads API credentials).

Two things Windsor abstracted away that this file has to do explicitly:
  - cost is returned as cost_micros (int, 1/1,000,000 of the currency unit) -- must divide by 1e6.
  - GAQL splits reporting the same way Windsor's fields did: asset_group-level fields
    (status, listing group filters) can't be combined with product-level segments
    (segments.product_item_id) in one query -- these are genuinely different resources
    (asset_group / asset_group_listing_group_filter vs shopping_performance_view), not just an
    API quirk Windsor invented.
"""
import os

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

GOOGLE_ADS_API_VERSION = "v21"  # bump alongside the google-ads package; check the changelog when upgrading


def build_client():
    """Reads ADS_DEVELOPER_TOKEN / ADS_CLIENT_ID / ADS_CLIENT_SECRET / ADS_REFRESH_TOKEN /
    ADS_LOGIN_CUSTOMER_ID from the environment (GitHub Actions secrets in production)."""
    login_customer_id = os.environ.get("ADS_LOGIN_CUSTOMER_ID", "").replace("-", "")
    config = {
        "developer_token": os.environ["ADS_DEVELOPER_TOKEN"],
        "client_id": os.environ["ADS_CLIENT_ID"],
        "client_secret": os.environ["ADS_CLIENT_SECRET"],
        "refresh_token": os.environ["ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }
    if login_customer_id:
        config["login_customer_id"] = login_customer_id
    return GoogleAdsClient.load_from_dict(config)


def _run_search(client, customer_id, query):
    ga_service = client.get_service("GoogleAdsService")
    try:
        return list(ga_service.search(customer_id=customer_id, query=query))
    except GoogleAdsException as ex:
        details = "; ".join(
            f"{err.error_code}: {err.message}" for err in ex.failure.errors
        )
        raise RuntimeError(f"Google Ads API request failed: {details}") from ex


def fetch_ag_status(client, customer_id):
    """campaign+asset_group status, for the "現在稼働中/一時停止中" badge -- same role as the
    Windsor-based fetch_ag_status in generate_board.py, not the source of truth for which
    products are shown (see build_products there)."""
    query = """
        SELECT
          campaign.name,
          campaign.status,
          asset_group.name,
          asset_group.status,
          asset_group.id
        FROM asset_group
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
    """
    rows = _run_search(client, customer_id, query)
    return [
        {
            "campaign": row.campaign.name,
            "campaign_status": row.campaign.status.name,
            "asset_group": row.asset_group.name,
            "asset_group_status": row.asset_group.status.name,
        }
        for row in rows
    ]


def fetch_all_campaigns(client, customer_id):
    """Every campaign in the account, no filter -- feeds generate_board.py's structure audit
    (catching e.g. a brand-new campaign that doesn't match CAMPAIGN_KEYWORDS)."""
    query = "SELECT campaign.name, campaign.status FROM campaign"
    rows = _run_search(client, customer_id, query)
    return [{"campaign": row.campaign.name, "campaign_status": row.campaign.status.name} for row in rows]


def fetch_listing_group_filters(client, customer_id):
    """The NEW capability Windsor couldn't expose: which product_custom_label_0-4 value(s) each
    asset group's listing group filter actually targets, read straight from the account's real
    configuration instead of the hand-maintained AG_TO_LABELS dict in generate_board.py.

    Only UNIT_INCLUDED/UNIT_EXCLUDED filter nodes are real leaf partitions with a concrete
    case_value; SUBDIVISION nodes are branch points in the partition tree and are skipped here.
    (Confirmed live 2026-08-05: the type enum is UNIT_INCLUDED/UNIT_EXCLUDED/SUBDIVISION, not a
    plain "UNIT" -- an earlier draft of this function assumed the latter and silently matched
    zero rows.) A UNIT node with no product_custom_attribute set (case_value on a different oneof
    member, or unset) represents an "everything else" catch-all partition -- callers should treat
    that as "no specific label." custom_label_index can be any of INDEX0-INDEX4, not just
    INDEX0/custom_label_0 -- confirmed live some AGs partition on custom_label_1.
    """
    # NOTE: asset_group_listing_group_filter.type is not filterable in WHERE (confirmed live
    # 2026-08-05: GAQL rejects it with BAD_ENUM_CONSTANT even quoted correctly) -- filter for
    # type == UNIT in Python instead, same defensive pattern used for the Windsor REST filters.
    query = """
        SELECT
          campaign.name,
          asset_group.name,
          asset_group.id,
          asset_group_listing_group_filter.type,
          asset_group_listing_group_filter.case_value.product_custom_attribute.index,
          asset_group_listing_group_filter.case_value.product_custom_attribute.value
        FROM asset_group_listing_group_filter
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
    """
    rows = _run_search(client, customer_id, query)
    results = []
    for row in rows:
        node_type = row.asset_group_listing_group_filter.type.name
        if node_type not in ("UNIT_INCLUDED", "UNIT_EXCLUDED"):
            continue
        attr = row.asset_group_listing_group_filter.case_value.product_custom_attribute
        results.append({
            "campaign": row.campaign.name,
            "asset_group": row.asset_group.name,
            "included": node_type == "UNIT_INCLUDED",
            "custom_label_index": attr.index.name if attr.index else None,
            "custom_label_value": attr.value or None,
        })
    return results


def fetch_account_daily_totals(client, customer_id, date_from, date_to):
    """TRUE account-wide daily cost/conversions_value -- every campaign, regardless of
    CAMPAIGN_KEYWORDS or custom_label_0 classification. Added 2026-08-20 because the board's
    "全体ROAS" was silently computed only from item_id-classified spend (fetch_item_performance,
    joined against a recognized custom_label_0), which understates true ad cost by however much
    is running under an unmapped label (best_seller_quickship/excluded/single, etc. -- see the
    board's own structure-audit warnings). The `customer` resource aggregates at the account
    level per day, independent of any campaign/AG/product join, so this is the one honest source
    for "how much did advertising actually cost, and what did it actually return"."""
    query = f"""
        SELECT
          segments.date,
          metrics.cost_micros,
          metrics.conversions_value
        FROM customer
        WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
    """
    rows = _run_search(client, customer_id, query)
    return [
        {
            "date": row.segments.date,
            "cost": row.metrics.cost_micros / 1_000_000,
            "conversions_value": row.metrics.conversions_value,
        }
        for row in rows
    ]


def fetch_campaign_daily_totals(client, customer_id, date_from, date_to):
    """TRUE per-campaign daily cost/conversions_value -- every product under that campaign,
    regardless of custom_label_0 classification. Added 2026-08-20 for the same reason as
    fetch_account_daily_totals (see its docstring), one level down: the campaign-tier ROAS cards
    (Best-Selling/Second-Team/Gift-Scene) were summing only classified-product spend
    (fetch_item_performance joined against custom_label_0), missing whatever ran under an
    unrecognized label within that campaign -- confirmed live 2026-08-20 that e.g. the "excluded"
    label is the ２軍 (Second-Team) AG's own listing-group-filter exclusion value, so a product
    that earned spend before being excluded has no recoverable label but its spend still counts
    toward Second-Team's real total. The `campaign` resource aggregates per campaign per day,
    independent of any product/AG join."""
    query = f"""
        SELECT
          campaign.name,
          segments.date,
          metrics.cost_micros,
          metrics.conversions,
          metrics.conversions_value
        FROM campaign
        WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
    """
    rows = _run_search(client, customer_id, query)
    return [
        {
            "campaign": row.campaign.name,
            "date": row.segments.date,
            "cost": row.metrics.cost_micros / 1_000_000,
            "conversions": row.metrics.conversions,
            "conversions_value": row.metrics.conversions_value,
        }
        for row in rows
    ]


def fetch_ag_daily_totals(client, customer_id, date_from, date_to):
    """TRUE per-asset-group daily cost/conversions/conversions_value -- every surface the AG
    serves on (Shopping, Search, Display, YouTube, ...), not just the shopping_performance_view
    slice fetch_item_performance sees. Added 2026-08-22 after confirming live that the AG column
    heading ROAS -- previously just the sum of that AG's product cards -- understated Second-
    Team's true ROAS by +41.5pt (263.1% true vs 304.6% product-card sum) because non-Shopping
    surface spend/value has no item_id and so never reaches shopping_performance_view. The
    `asset_group` resource aggregates per asset group per day, independent of any product/item_id
    join -- same resource fetch_ag_status already queries for the status badge, just with
    segments.date + metrics added."""
    query = f"""
        SELECT
          campaign.name,
          asset_group.name,
          segments.date,
          metrics.cost_micros,
          metrics.conversions,
          metrics.conversions_value
        FROM asset_group
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          AND segments.date BETWEEN '{date_from}' AND '{date_to}'
    """
    rows = _run_search(client, customer_id, query)
    return [
        {
            "campaign": row.campaign.name,
            "asset_group": row.asset_group.name,
            "date": row.segments.date,
            "cost": row.metrics.cost_micros / 1_000_000,
            "conversions": row.metrics.conversions,
            "conversions_value": row.metrics.conversions_value,
        }
        for row in rows
    ]


def fetch_item_performance(client, customer_id, date_from, date_to):
    """Daily per-product performance across all campaigns -- Windsor's item_id-level step.
    Cost comes back as cost_micros; divide by 1_000_000 here so callers get real currency units."""
    query = f"""
        SELECT
          campaign.name,
          segments.product_item_id,
          segments.date,
          metrics.cost_micros,
          metrics.conversions,
          metrics.conversions_value
        FROM shopping_performance_view
        WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
    """
    rows = _run_search(client, customer_id, query)
    return [
        {
            "campaign": row.campaign.name,
            "product_item_id": row.segments.product_item_id,
            "date": row.segments.date,
            "cost": row.metrics.cost_micros / 1_000_000,
            "conversions": row.metrics.conversions,
            "conversions_value": row.metrics.conversions_value,
        }
        for row in rows
    ]
