#!/usr/bin/env python3
"""One-time setup: registers this GCP project as a developer on the Merchant Center account.
Required before any other Merchant API call works (confirmed live 2026-08-05: calls fail with
401 GCP_NOT_REGISTERED until this has been done once). Safe to run more than once.
"""
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
    response = client.register_gcp(request=request)
    print("Registered:", response)


if __name__ == "__main__":
    main()
