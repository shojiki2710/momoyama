"""Google Ads API access for the direct-API pipeline (replaces Windsor.ai's google_ads connector).

NOT YET LIVE-TESTED -- written from Google's official documentation (developers.google.com/google-ads/api)
since this environment has no Google Ads API credentials to verify against directly, unlike the
Windsor.ai MCP connector used to validate the original pipeline. Treat field paths here as a strong
first draft; confirm against a real run (see scripts/direct_api/test_ads_client.py) before relying on it.

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


def fetch_listing_group_filters(client, customer_id):
    """The NEW capability Windsor couldn't expose: which product_custom_label_0-4 value(s) each
    asset group's listing group filter actually targets, read straight from the account's real
    configuration instead of the hand-maintained AG_TO_LABELS dict in generate_board.py.

    Only UNIT-type filter nodes are real leaf partitions with a concrete case_value; SUBDIVISION
    nodes are branch points in the partition tree and are skipped here. A UNIT node with no
    product_custom_attribute set (case_value on a different oneof member, or unset) represents an
    "everything else" catch-all partition -- callers should treat that as "no specific label."
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
        if row.asset_group_listing_group_filter.type.name != "UNIT":
            continue
        attr = row.asset_group_listing_group_filter.case_value.product_custom_attribute
        results.append({
            "campaign": row.campaign.name,
            "asset_group": row.asset_group.name,
            "custom_label_index": attr.index.name if attr.index else None,
            "custom_label_value": attr.value or None,
        })
    return results


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
