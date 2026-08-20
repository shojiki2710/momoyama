"""Shopify Admin API access -- daily total store-wide sales revenue, for the
"売上高広告費率" (ad-cost-to-total-sales ratio) metric added 2026-08-20.

Deliberately NOT the ShopifyQL/Analytics `sales` resource (what the interactive Shopify
connector in chat uses) -- that requires a `read_reports`-style scope whose availability for
this app wasn't confirmed. Standard Orders (`read_orders` scope, confirmed working via
graphql_query during development) gives the same number by summing each order's current total
price ourselves, which is simple enough not to need the Analytics API at all.

Auth: the client credentials grant (client_id + client_secret -> a fresh, shop-scoped access
token), NOT a Dev-Dashboard "app automation token" -- confirmed live 2026-08-20 that automation
tokens are for `shopify app deploy`/CLI release workflows only and return 401 against the Admin
API itself. client_credentials tokens expire after 24h (Shopify's own limit), so this fetches a
fresh one on every run rather than storing one -- see https://shopify.dev/docs/apps/build/
authentication-authorization/access-tokens/client-credentials-grant. Only available for apps
owned by and installed in your own store, which this is.

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


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Shopify API request failed ({e.code}): {e.read().decode(errors='replace')}") from e


def build_credentials(shop_domain):
    """Exchanges SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET (GitHub Actions secrets in production)
    for a fresh shop-scoped access token via the client credentials grant. Returns the token
    string -- callers should NOT cache this across runs, it's only valid ~24h."""
    client_id = os.environ["SHOPIFY_CLIENT_ID"]
    client_secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    body = f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}".encode("ascii")
    payload = _post(
        f"https://{shop_domain}/admin/oauth/access_token",
        body,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    return payload["access_token"]


def _graphql(shop_domain, access_token, query, variables):
    url = f"https://{shop_domain}/admin/api/{API_VERSION}/graphql.json"
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    payload = _post(url, body, {"Content-Type": "application/json", "X-Shopify-Access-Token": access_token})
    if payload.get("errors"):
        raise RuntimeError(f"Shopify GraphQL error: {payload['errors']}")
    return payload["data"]


def fetch_daily_total_sales(access_token, shop_domain, date_from, date_to):
    """{date_str: total_sales}, bucketed by the shop's own JST calendar day (matching
    generate_board.py's date_list). Cancelled orders are excluded; current_total_price_set
    reflects post-refund/post-edit amounts, so partial refunds already net out correctly."""
    # status:any is required -- Shopify's orders search defaults to status:open only, silently
    # excluding archived/closed orders. Without this, any order old enough to have been
    # auto-archived (observed: ~2026-08-20, missing all orders before ~2026-06-21) drops out of
    # the result with no error, and callers relying on .get(date, 0) see a false zero for that day.
    q = f"status:any AND created_at:>='{date_from}T00:00:00+09:00' AND created_at:<='{date_to}T23:59:59+09:00'"
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
