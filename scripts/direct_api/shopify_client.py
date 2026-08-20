"""Shopify Admin API access -- daily total store-wide sales revenue, for the
"売上高広告費率" (ad-cost-to-total-sales ratio) metric added 2026-08-20.

Deliberately NOT the ShopifyQL/Analytics `sales` resource (what the interactive Shopify
connector in chat uses) -- that requires a `read_reports`-style scope whose availability for a
custom/automation-token app wasn't confirmed. Standard Orders (`read_orders` scope, confirmed
working via graphql_query during development) gives the same number by summing each order's
current total price ourselves, which is simple enough not to need the Analytics API at all.

Uses a Dev-Dashboard "app automation token" (Settings > アプリのオートメーショントークン),
not a legacy custom-app Admin API access token -- this app is CI/CD-only, so this was the
intended credential type, not an OAuth flow.

Query date boundaries are given explicit JST (+09:00) offsets rather than bare dates -- verified
live (2026-08-20) that Shopify's `created_at:>=`/`<=` search filters interpret bare dates in the
shop's own timezone, but being explicit removes any ambiguity about which day an order near
midnight lands in.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API_VERSION = "2026-07"
JST = ZoneInfo("Asia/Tokyo")

_ORDERS_QUERY = """
query($cursor: String, $q: String!) {
  orders(first: 250, after: $cursor, query: $q, sortKey: CREATED_AT) {
    edges {
      cursor
      node {
        createdAt
        cancelledAt
        currentTotalPriceSet { shopMoney { amount } }
      }
    }
    pageInfo { hasNextPage }
  }
}
"""


def _graphql(shop_domain, access_token, query, variables):
    url = f"https://{shop_domain}/admin/api/{API_VERSION}/graphql.json"
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": access_token},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Shopify Admin API request failed ({e.code}): {e.read().decode(errors='replace')}") from e
    if payload.get("errors"):
        raise RuntimeError(f"Shopify GraphQL error: {payload['errors']}")
    return payload["data"]


def build_credentials():
    """Reads SHOPIFY_ACCESS_TOKEN from the environment (a GitHub Actions secret in production)."""
    token = os.environ["SHOPIFY_ACCESS_TOKEN"]
    return token


def fetch_daily_total_sales(access_token, shop_domain, date_from, date_to):
    """{date_str: total_sales}, bucketed by the shop's own JST calendar day (matching
    generate_board.py's date_list). Cancelled orders are excluded; current_total_price_set
    reflects post-refund/post-edit amounts, so partial refunds already net out correctly."""
    q = f"created_at:>='{date_from}T00:00:00+09:00' AND created_at:<='{date_to}T23:59:59+09:00'"
    totals = {}
    cursor = None
    while True:
        data = _graphql(shop_domain, access_token, _ORDERS_QUERY, {"cursor": cursor, "q": q})
        orders = data["orders"]
        edges = orders["edges"]
        for edge in edges:
            node = edge["node"]
            if node["cancelledAt"]:
                continue
            created_utc = datetime.strptime(node["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            date_str = created_utc.astimezone(JST).date().isoformat()
            amount = float(node["currentTotalPriceSet"]["shopMoney"]["amount"])
            totals[date_str] = totals.get(date_str, 0.0) + amount
        if not orders["pageInfo"]["hasNextPage"] or not edges:
            break
        cursor = edges[-1]["cursor"]
    return totals
