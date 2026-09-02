import asyncio
import logging
import os
import time

import zendriver as zd
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
PHONE = os.getenv("PHONE_NUMBER", "")
EMAIL_TOKEN_PATH = "token.json"
CLIENT_SECRETS_FILE = "credentials.json"


def get_gmail_service() -> Resource:
    """Авторизуется в google-почте и возвращает объект взаимодействия с ней"""

    credentials = None
    if os.path.exists(EMAIL_TOKEN_PATH):
        credentials = Credentials.from_authorized_user_file(EMAIL_TOKEN_PATH, SCOPES)
    if credentials is not None and credentials.expired:
        if credentials.refresh_token:
            credentials.refresh(Request())
        else:
            credentials = None
    if credentials is None:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        credentials = flow.run_local_server(port=0)
        with open(EMAIL_TOKEN_PATH, "w") as token:
            token.write(credentials.to_json())
    print(type(build("gmail", "v1", credentials=credentials)))
    return build("gmail", "v1", credentials=credentials)


async def get_secret_key() -> None:
    """Инициирует отправку на email секретного кода для входа в аккаунт ozon.ru"""

    browser = await zd.start()
    page = await browser.get("https://data.ozon.ru/?locale=ru")
    time.sleep(10)
    buttons = await page.find_all("button")
    logger.info("На текущей странице обнаружено %d кнопок", len(buttons))
    for button in buttons:
        if "Перейти к аналитике" in button.text:
            await button.click()
            logger.info("Редирект на страницу авторизации")
            break
    phone_input = await page.wait_for('input[type="tel"]', timeout=30)
    time.sleep(3)
    await phone_input.send_keys(PHONE)
    submit_button = await page.select('button[type="submit"]')
    await submit_button.click()
    login_form = await page.find('section[class="csma-ozon-id-page-anonymous"]', timeout=30)
    if "Введите код" not in login_form.text:
        await browser.stop()
        logger.error("Указан некорректный номер телефона для авторизации")
    else:

        time.sleep(60)
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(get_secret_key())
