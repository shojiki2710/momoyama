"""Merchant API (v1) access for the direct-API pipeline (replaces Windsor.ai's google_merchant
connector and, more importantly, the old Content API for Shopping -- which sunsets 2026-08-18).

NOT YET LIVE-TESTED -- see the note at the top of ads_client.py; same caveat applies here.
Field names on the returned Product proto (snake_case vs e.g. "customLabel0" in REST/JSON) are a
real risk of being wrong on the first attempt -- scripts/direct_api/test_direct_api.py prints raw
repr() of a few products specifically so this can be corrected against real data before relying
on it, the same way Windsor's fields were validated live earlier in this project.
"""
import os

from google.oauth2 import service_account
from google.shopping import merchant_products_v1

SCOPES = ["https://www.googleapis.com/auth/content"]


def build_credentials():
    """Reads the service account JSON from the GCP_SERVICE_ACCOUNT_JSON env var (its full
    file contents, not a path -- GitHub Actions secrets don't have a filesystem path)."""
    import json

    info = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def fetch_products(credentials, merchant_account_id):
    """Returns raw merchant_products_v1.Product protos. Caller normalizes into the
    {product_id: {label, title, image}} shape generate_board.py expects -- kept as raw objects
    here rather than pre-normalized, since the exact attribute path (product_attributes.title vs
    product_attributes.custom_label0 etc.) needs to be confirmed against a real response first."""
    client = merchant_products_v1.ProductsServiceClient(credentials=credentials)
    request = merchant_products_v1.ListProductsRequest(
        parent=f"accounts/{merchant_account_id}", page_size=250
    )
    return list(client.list_products(request=request))
