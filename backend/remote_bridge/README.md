# Local Telegram Bridge

This tool allows you to exchange files with your local computer via a Telegram Bot.

## Setup

1.  **Install Python Dependencies:**
    Open your terminal in this directory (`remote_bridge`) and run:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Get a Bot Token:**
    *   Open Telegram and search for **@BotFather**.
    *   Send `/newbot` and follow the instructions.
    *   Copy the **API Token** provided.

3.  **Configure:**
    *   Open the `.env` file in this folder.
    *   Paste your token: `TELEGRAM_BOT_TOKEN=123456:ABC-DEF...`

4.  **Run:**
    ```bash
    python bot.py
    ```

## Usage

*   **Send a file/photo:** The bot will save it to the `downloads` folder on your PC.
*   `/ls`: List files currently in the `downloads` folder.
*   `/get <filename>`: Download a file from your PC to your phone.
