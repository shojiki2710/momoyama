"""One-time helper: generate a new Google Ads API refresh token for a
"Desktop" -type OAuth 2.0 Client.

Why this exists (not the OAuth Playground): a Desktop-type OAuth client
only supports Google's "installed application" loopback flow -- it does
NOT accept arbitrary registered redirect URIs, so the Playground's fixed
redirect (https://developers.google.com/oauthplayground) is always
rejected with "Error 400: redirect_uri_mismatch" for this client type.
This script instead starts a temporary local web server on
http://localhost, opens your default browser to Google's consent screen,
and captures the redirect there -- the officially supported flow for this
client type.

Usage:
    pip install google-auth-oauthlib
    python3 scripts/generate_ads_refresh_token.py

You'll be prompted for your OAuth Client ID and Client Secret (the secret
is hidden as you type). A browser window will open for you to log in and
approve access; after that, the refresh token is printed here. Paste it
into the ADS_REFRESH_TOKEN GitHub secret -- do not paste it into chat.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main():
    client_id = input("OAuth Client ID: ").strip()
    # Deliberately visible (not getpass): some terminals mishandle pasting
    # into a hidden/no-echo prompt (bracketed-paste control bytes leaking
    # into the captured string), which was silently corrupting the secret
    # here even though the masked preview looked right. Typing/pasting into
    # your own terminal is fine -- the only thing to avoid is pasting into
    # chat.
    client_secret = input("OAuth Client secret (visible): ").strip()
    print(f"  -> read {len(client_secret)} character(s), starts with {client_secret[:6]!r}")

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    print("\nOpening your browser to sign in and approve access...")
    print("(If a warning about an unverified app appears, click 'Advanced' -> 'Go to ... (unsafe)' -- this is your own app.)\n")
    credentials = flow.run_local_server(port=0)

    print("\n" + "=" * 60)
    print("Refresh token (paste this into the ADS_REFRESH_TOKEN GitHub secret):")
    print(credentials.refresh_token)
    print("=" * 60)


if __name__ == "__main__":
    main()
