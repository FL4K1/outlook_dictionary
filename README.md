# Outlook Recent Mail Exporter

A production-ready Python application that authenticates securely with the Microsoft Graph API, retrieves the 5 most recent emails from your Outlook Inbox, and exports them as text files directly to your Desktop.

## 🌟 Features
- **Secure Authentication**: Uses Microsoft Authentication Library (MSAL) for robust, industry-standard OAuth 2.0 authentication.
- **Local Token Caching**: Caches your authentication token locally (`msal_token_cache.bin`) to avoid repetitive sign-in prompts.
- **Microsoft Graph API Integration**: Connects to the official Microsoft Graph API to efficiently query and retrieve your inbox messages.
- **Automated Exporting**: Parses the email (Subject, Sender, Date, Body) and writes it into a clean `.txt` format in an `OutlookExports` folder on your Desktop.
- **Smart HTML Stripping**: Cleans up HTML-formatted emails to ensure the exported text is readable.
- **Cross-Platform**: Works seamlessly on Windows, macOS, and Linux.

## 📂 Project Structure

- `main.py`: The application entry point. Orchestrates the workflow: logging setup, authentication, data fetching, and exporting.
- `auth.py`: Handles all Microsoft Entra ID (Azure AD) authentication using the MSAL library. Manages interactive login and token caching.
- `graph_client.py`: Contains the `GraphClient` class responsible for building and sending the HTTP requests to the Microsoft Graph API.
- `email_exporter.py`: Contains the logic for processing the raw email data, formatting it, sanitizing filenames, and writing it to the local filesystem.
- `config.py`: Stores configuration constants like `CLIENT_ID`, `TENANT_ID`, `AUTHORITY`, and required `SCOPES`.
- `requirements.txt`: Lists the Python dependencies required to run the project.

## 🚀 Prerequisites
- Python 3.11 or higher
- An Azure AD App Registration (see setup instructions below)
- An active Microsoft/Outlook account

## ⚙️ App Registration Setup

Before running the application, you need to register an app in the Azure Portal to get your `CLIENT_ID`.

1. Go to the [Azure Portal](https://portal.azure.com/).
2. Navigate to **Microsoft Entra ID** -> **App registrations** -> **New registration**.
3. Enter a Name for your application (e.g., "Outlook Desktop Exporter").
4. For **Supported account types**, select **Accounts in any organizational directory and personal Microsoft accounts**.
5. Under **Redirect URI**, select **Public client/native (mobile & desktop)** and enter `http://localhost`.
6. Click **Register**.
7. In the app overview page, note down the **Application (client) ID**.
8. Go to **API permissions** -> **Add a permission** -> **Microsoft Graph** -> **Delegated permissions**.
9. Search for and add the following permissions: 
   - `Mail.Read`
   - `User.Read`
10. Open `config.py` in this project and replace the `CLIENT_ID` (if different) with your Application (client) ID.

## 💻 Installation

1. Clone or download this project to your local machine.
2. Open a terminal and navigate to the project directory:
   ```bash
   cd path/to/outlook_project
   ```
3. (Optional but recommended) Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
4. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## ▶️ Execution

Run the application:
```bash
python main.py
```

### What to expect:
1. **First Run**: A browser window will open asking you to sign in to your Microsoft account. You will be prompted to grant the application permission to read your profile and emails.
2. **Subsequent Runs**: The application will use the cached token and silently authenticate in the background.
3. The app will fetch the latest 5 emails and save them to a new `OutlookExports` directory on your Desktop.
4. Check the console logs to see the live progress of the script!

## 🛡️ Error Handling and Logging
This application includes comprehensive logging at the `INFO` and `ERROR` levels. If something goes wrong (e.g., network failure, invalid credentials, filesystem permission issues), check the console output for a detailed trace of what occurred.
