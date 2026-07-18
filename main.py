import logging
from auth import get_access_token
from graph_client import GraphClient
from email_exporter import export_emails_to_desktop

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

def main():
    setup_logging()
    logger = logging.getLogger("main")
    
    logger.info("Starting Outlook Export Application")
    
    try:
        # 1. Authenticate and get access token
        logger.info("Authenticating with Microsoft Graph...")
        access_token = get_access_token()
        
        # 2. Fetch emails
        logger.info("Connecting to Graph API...")
        client = GraphClient(access_token)
        recent_emails = client.get_recent_emails(count=5)
        
        # 3. Export to Desktop
        if recent_emails:
            logger.info("Exporting emails to Desktop...")
            export_emails_to_desktop(recent_emails)
        else:
            logger.warning("No emails found to export.")
            
        logger.info("Application finished successfully.")
        
    except Exception as e:
        logger.error(f"Application failed: {e}")

if __name__ == "__main__":
    main()
