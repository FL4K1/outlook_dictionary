import logging
import requests
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"

class GraphClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def get_recent_emails(self, count: int = 5) -> List[Dict[str, Any]]:
        """
        Fetches the most recent emails from the user's inbox along with their MIME content.
        """
        logger.info(f"Fetching latest {count} emails from Inbox...")
        
        url = f"{GRAPH_API_ENDPOINT}/me/mailFolders/inbox/messages"
        params = {
            "$top": count,
            "$select": "id,subject,receivedDateTime",
            "$orderby": "receivedDateTime DESC"
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            messages = data.get("value", [])
            logger.info(f"Successfully retrieved {len(messages)} emails metadata. Fetching MIME content...")
            
            # Fetch MIME content for each message
            for msg in messages:
                msg_id = msg['id']
                mime_url = f"{GRAPH_API_ENDPOINT}/me/messages/{msg_id}/$value"
                mime_response = requests.get(mime_url, headers=self.headers)
                mime_response.raise_for_status()
                
                # Store the raw bytes
                msg['mime_content'] = mime_response.content
                
            return messages
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching emails from Graph API: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response content: {e.response.text}")
            raise RuntimeError(f"Graph API request failed: {e}")
