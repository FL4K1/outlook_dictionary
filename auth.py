import os
import atexit
import logging
import msal
from config import CLIENT_ID, AUTHORITY, SCOPES, CACHE_FILE

logger = logging.getLogger(__name__)

def _build_msal_app(cache=None):
    return msal.PublicClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache
    )

def get_access_token() -> str:
    """
    Authenticates the user and returns an access token.
    Uses cached token if available, otherwise prompts for interactive login.
    """
    cache = msal.SerializableTokenCache()
    
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cache.deserialize(f.read())
            logger.info("Loaded token cache from file.")
        except Exception as e:
            logger.warning(f"Failed to load token cache: {e}")

    atexit.register(lambda:
        open(CACHE_FILE, "w").write(cache.serialize())
        if cache.has_state_changed else None
    )

    app = _build_msal_app(cache)
    
    # Try to find a cached token
    accounts = app.get_accounts()
    if accounts:
        logger.info(f"Found {len(accounts)} accounts in cache. Attempting silent token acquisition.")
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            logger.info("Successfully acquired token silently.")
            return result["access_token"]
            
    # If no cached token or silent acquisition failed, prompt interactive login
    logger.info("No valid cached token found. Starting interactive authentication.")
    result = app.acquire_token_interactive(scopes=SCOPES)
    
    if "access_token" in result:
        logger.info("Successfully acquired token interactively.")
        return result["access_token"]
    else:
        error_msg = result.get("error_description", result.get("error", "Unknown error"))
        logger.error(f"Failed to acquire token: {error_msg}")
        raise RuntimeError(f"Authentication failed: {error_msg}")
