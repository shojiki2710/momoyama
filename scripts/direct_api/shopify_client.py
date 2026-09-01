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

Requires the `read_all_orders` scope, not just `read_orders` -- confirmed live 2026-08-20 that
Shopify's orders() query silently caps results to the last 60 days without it (no error, just
missing orders), which had been zero-filling ~29 of the 90 days generate_board.py asks for. An
initial attempt at diagnosing this as a `status:open`-default issue (adding `status:any` to the
query) was a red herring: `any` isn't a valid value for that field (the API returns a warning and
ignores the clause) and had no effect either way -- the 60-day cap was the whole story. Granting
the scope in Dev Dashboard > Configuration wasn't enough by itself; the store's install also
needed to be re-approved once (Shopify admin > Apps > this app > re-authorize) before a fresh
client_credentials token actually carried `read_all_orders`.
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


_LINE_ITEMS_QUERY = """
query($cursor: String, $q: String!) {
  orders(first: 250, after: $cursor, query: $q, sortKey: CREATED_AT) {
    edges {
      cursor
      node {
        createdAt
        cancelledAt
        lineItems(first: 250) {
          edges {
            node {
              currentQuantity
              originalTotalSet { shopMoney { amount } }
              variant { id product { id } }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage }
  }
}
"""


def fetch_line_item_quantities(access_token, shop_domain, date_from, date_to):
    """{item_id: {date_str: {qty, revenue}}}, aggregated across every order's line items
    regardless of channel/referrer -- feeds the "未広告・要検討" opportunity list added
    2026-08-31: products the store actually sells in volume that current Google Ads item-level
    spend doesn't reflect at all.

    item_id is built from the line item's product/variant numeric Shopify IDs as
    "shopify_jp_<productId>_<variantId>", matching the same convention Merchant Center/Google Ads
    item_ids already use elsewhere in this pipeline (confirmed 2026-07-28, see
    merchant_client.normalize_product) -- lets this join directly against product_labels/
    performance without a separate lookup table.

    currentQuantity (not the original ordered quantity) nets out later refunds/removals, mirroring
    fetch_daily_total_sales' use of currentTotalPriceSet for the same reason. Line items without a
    variant (manual/custom line items, or a since-deleted product) are skipped -- they have no
    Shopify catalog product to attribute the sale to. Assumes no single order has more than 250
    distinct line items (unpaginated `first: 250` on lineItems) -- a safe assumption for this
    store's typical gift-shop order sizes."""
    q = f"created_at:>='{date_from}T00:00:00+09:00' AND created_at:<='{date_to}T23:59:59+09:00'"
    totals = {}
    cursor = None
    while True:
        data = _graphql(shop_domain, access_token, _LINE_ITEMS_QUERY, {"cursor": cursor, "q": q})
        orders = data["orders"]
        edges = orders["edges"]
        for edge in edges:
            node = edge["node"]
            if node["cancelledAt"]:
                continue
            created_utc = datetime.strptime(node["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            date_str = created_utc.astimezone(JST).date().isoformat()
            for li_edge in node["lineItems"]["edges"]:
                li = li_edge["node"]
                variant = li.get("variant")
                if not variant or not variant.get("product"):
                    continue
                product_num = variant["product"]["id"].rsplit("/", 1)[-1]
                variant_num = variant["id"].rsplit("/", 1)[-1]
                item_id = f"shopify_jp_{product_num}_{variant_num}"
                qty = li.get("currentQuantity") or 0
                revenue = float(li["originalTotalSet"]["shopMoney"]["amount"])
                by_date = totals.setdefault(item_id, {})
                day = by_date.setdefault(date_str, {"qty": 0, "revenue": 0.0})
                day["qty"] += qty
                day["revenue"] += revenue
        if not orders["pageInfo"]["hasNextPage"] or not edges:
            break
        cursor = edges[-1]["cursor"]
    return totals


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
