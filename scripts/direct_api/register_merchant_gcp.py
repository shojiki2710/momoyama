#!/usr/bin/env python3
"""One-time setup: registers this GCP project as a developer on the Merchant Center account.
Required before any other Merchant API call works (confirmed live 2026-08-05: calls fail with
401 GCP_NOT_REGISTERED until this has been done once). Safe to run more than once.
"""
import sys

sys.path.insert(0, ".")

from google.api_core.exceptions import AlreadyExists
from google.shopping import merchant_accounts_v1

from scripts.direct_api import merchant_client

MERCHANT_ACCOUNT_ID = "273780463"
DEVELOPER_EMAIL = "shojiki2710@gmail.com"  # must be a real Google account, not the service account


def main():
    credentials = merchant_client.build_credentials()
    client = merchant_accounts_v1.DeveloperRegistrationServiceClient(credentials=credentials)
    request = merchant_accounts_v1.RegisterGcpRequest(
        name=f"accounts/{MERCHANT_ACCOUNT_ID}/developerRegistration",
        developer_email=DEVELOPER_EMAIL,
    )
    try:
        response = client.register_gcp(request=request)
        print("Registered:", response)
    except AlreadyExists:
        # confirmed live 2026-08-05: re-registering an already-registered GCP project raises
        # ALREADY_EXISTS rather than being a no-op -- treat that as success, not a failure.
        print("Already registered, nothing to do.")


if __name__ == "__main__":
    main()
