#!/usr/bin/env python3
"""One-time local setup tool: generates a Google Ads API refresh token.

This needs to run locally and interactively (it opens a browser for you to log in as
whichever Google account has access to the Ads account) -- it cannot be run inside GitHub
Actions or by Claude. Run it once, copy the printed refresh_token into the ADS_REFRESH_TOKEN
GitHub repository secret, and you never need to run it again (unless the token is revoked).

Usage:
    pip install google-auth-oauthlib
    python scripts/setup/generate_ads_refresh_token.py --client-id ... --client-secret ...

IMPORTANT: after this works, go to Google Cloud Console -> APIs & Services -> OAuth consent
screen and set Publishing status to "In production". While it's "Testing", Google expires the
refresh token after 7 days, which would silently break the daily automation a week from now.
"""
import argparse
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True, help="OAuth2 client ID from Google Cloud Console")
    parser.add_argument("--client-secret", required=True, help="OAuth2 client secret from Google Cloud Console")
    args = parser.parse_args()

    client_config = {
        "installed": {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    # opens a browser; log in with the Google account that has access to the Ads account (via the MCC).
    credentials = flow.run_local_server(port=0)

    print("\n" + "=" * 60)
    print("Success. Add this as the ADS_REFRESH_TOKEN GitHub secret:")
    print("=" * 60)
    print(credentials.refresh_token)
    print("=" * 60)
    print(
        "\nReminder: set the OAuth consent screen's Publishing status to "
        "'In production' in Google Cloud Console, or this token expires in 7 days."
    )


if __name__ == "__main__":
    main()
