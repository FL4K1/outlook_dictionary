import os

# Microsoft Entra ID (Azure AD) App Registration details
# Replace these with your actual App Registration credentials
CLIENT_ID = os.environ.get("OUTLOOK_CLIENT_ID", "YOUR_CLIENT_ID_HERE")
TENANT_ID = os.environ.get("OUTLOOK_TENANT_ID", "consumers")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

# Scopes required by the application
SCOPES = ["User.Read", "Mail.Read"]

# Token cache file
CACHE_FILE = "msal_token_cache.bin"
