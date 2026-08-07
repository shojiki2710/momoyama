"""Merchant API (v1) access for the direct-API pipeline (replaces Windsor.ai's google_merchant
connector and, more importantly, the old Content API for Shopping -- which sunsets 2026-08-18).

Live-verified against the real account on 2026-08-05 (110 products returned). Two one-time setup
steps were needed before any call worked, both confirmed live and handled by
scripts/direct_api/register_merchant_gcp.py:
  1. The calling GCP project must be registered on the Merchant account once
     (developerRegistration.registerGcp) -- calls fail with 401 GCP_NOT_REGISTERED until then.
  2. The developer_email passed to that registration must be a real Google account (not the
     service account -- service accounts can't receive the verification), and needs its
     "API developer" role to show as accepted in Merchant Center's Users screen (there's a
     propagation delay of a few minutes) before calls succeed.
The service account itself additionally needs Admin-level access on the Merchant account --
Standard access was not sufficient to complete step 1.
"""
import json
import os

from google.oauth2 import service_account
from google.shopping import merchant_products_v1

SCOPES = ["https://www.googleapis.com/auth/content"]


def build_credentials():
    """Reads the service account JSON from the GCP_SERVICE_ACCOUNT_JSON env var (its full
    file contents, not a path -- GitHub Actions secrets don't have a filesystem path)."""
    info = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def fetch_products(credentials, merchant_account_id):
    """Returns raw merchant_products_v1.Product protos -- see normalize_product() for the
    {product_id: {label, title, image}} shape generate_board.py actually consumes."""
    client = merchant_products_v1.ProductsServiceClient(credentials=credentials)
    request = merchant_products_v1.ListProductsRequest(
        parent=f"accounts/{merchant_account_id}", page_size=250
    )
    return list(client.list_products(request=request))


def normalize_product(product):
    """product.offer_id is the join key against Google Ads' product_item_id -- confirmed live
    it can differ in case ("shopify_JP_..." here vs "shopify_jp_..." from Ads), same quirk as the
    Windsor pipeline; callers must lowercase both sides before joining."""
    attrs = product.product_attributes
    return {
        "product_id": product.offer_id,
        "label": attrs.custom_label_0 or None,
        "title": attrs.title or None,
        "image": attrs.image_link or None,
    }
