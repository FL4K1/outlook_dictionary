import os
import re
import logging
from typing import List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

def get_desktop_path() -> Path:
    """Returns the path to the user's Desktop directory."""
    return Path.home() / "Desktop"

def sanitize_filename(filename: str) -> str:
    """Removes invalid characters from a string to make it a safe filename."""
    # Remove invalid characters for Windows/macOS/Linux
    sanitized = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Replace newlines and excessive whitespace
    sanitized = re.sub(r'\s+', ' ', sanitized)
    return sanitized.strip()[:100]  # Limit length

def format_datetime(iso_date: str) -> str:
    """Formats ISO datetime string for filenames."""
    # Convert "2023-10-27T10:30:00Z" to "20231027_103000"
    clean_date = re.sub(r'[^0-9T]', '', iso_date)
    return clean_date.replace('T', '_')

def export_emails_to_desktop(emails: List[Dict[str, Any]]):
    """
    Saves the provided emails to the user's Desktop in a designated folder.
    """
    if not emails:
        logger.info("No emails to export. Inbox might be empty.")
        return

    desktop_path = get_desktop_path()
    export_dir = desktop_path / "OutlookExports"
    
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Export directory ready at: {export_dir}")
    except OSError as e:
        logger.error(f"Failed to create export directory {export_dir}: {e}")
        raise RuntimeError(f"Filesystem error: {e}")

    saved_count = 0
    for email in emails:
        try:
            subject = email.get("subject") or "No Subject"
            received_dt = email.get("receivedDateTime", "1970-01-01T00:00:00Z")
            mime_content = email.get("mime_content")
            
            if not mime_content:
                logger.warning(f"No MIME content found for email '{subject}'. Skipping.")
                continue
                
            date_prefix = format_datetime(received_dt)
            safe_subject = sanitize_filename(subject)
            
            # Saving as .eml (MIME format) which Outlook opens natively
            filename = f"{date_prefix}_{safe_subject}.eml"
            filepath = export_dir / filename
            
            # Write file content in binary mode
            with open(filepath, "wb") as f:
                f.write(mime_content)
                
            logger.info(f"Saved email: {filename}")
            saved_count += 1
            
        except Exception as e:
            logger.error(f"Failed to save email '{email.get('subject', 'Unknown')}': {e}")
            
    logger.info(f"Export complete. Successfully saved {saved_count} out of {len(emails)} emails.")
