import asyncio
import base64
import json
import logging
import os
import re
import time

import zendriver as zd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

PHONE = os.getenv("PHONE_NUMBER", "")
SENDER_EMAILS = ["mailer@sender.ozon.ru"]
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

EMAIL_TOKEN_FILE = "token.json"
CREDENTIALS_FILE = "credentials.json"
COOKIES_FILE = "cookies.json"

MAX_ATTEMPTS = 3
CODE_LENGTH = 6
AUTH_COOKIES = {
    "__Secure-token",
    "__Secure-refresh-token",
    "__Secure-access-token",
    "__Secure-idp-token",
    "__Secure-user-id",
}


def get_gmail_service() -> Resource:
    """Авторизуется в google-почте и возвращает объект взаимодействия с ней"""

    credentials = None
    if os.path.exists(EMAIL_TOKEN_FILE):
        credentials = Credentials.from_authorized_user_file(EMAIL_TOKEN_FILE, SCOPES)
    if credentials is not None and credentials.expired:
        if credentials.refresh_token:
            credentials.refresh(Request())
        else:
            credentials = None
    if credentials is None:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        credentials = flow.run_local_server(port=0)
    with open(EMAIL_TOKEN_FILE, "w") as token:
        token.write(credentials.to_json())
    return build("gmail", "v1", credentials=credentials)


def extract_code_from_html(html_text: str) -> str:
    """Извлекает секретный код из html-документа"""

    soup = BeautifulSoup(html_text, "html.parser")
    clean_text = str(soup.get_text(separator=""))
    pattern = re.compile(rf"код:\s+\d{{{CODE_LENGTH}}}", re.IGNORECASE)
    result = re.findall(pattern, clean_text)[0][-CODE_LENGTH:]
    return str(result)


def get_code_fom_message(gmail_service: Resource, senders: list) -> str:
    """Возвращает секретный код из письма"""

    query = " OR ".join([f"from:{s}" for s in senders])
    query += " is:unread in:anywhere"
    messages_list = None
    for i in range(MAX_ATTEMPTS):
        result = gmail_service.users().messages().list(userId="me", q=query).execute()  # type: ignore
        messages_list = result.get("messages")
        if messages_list is not None:
            break
        logger.warning("Письмо с кодом не доставлено, следующая проверка через 10 секунд")
        time.sleep(10)
        continue
    if messages_list is None:
        logger.error("Письмо с кодом не доставлено")
        return ""
    message_id = messages_list[0]["id"]
    all_ids = [msg["id"] for msg in messages_list]
    gmail_service.users().messages().batchModify(  # type: ignore
        userId="me", body={"ids": all_ids, "removeLabelIds": ["UNREAD"]}
    ).execute()
    message = gmail_service.users().messages().get(userId="me", id=message_id, format="full").execute()  # type: ignore
    headers = message.get("payload", dict()).get("headers")
    content_type = next(filter(lambda x: x.get("name") == "Content-Type", headers))["value"]
    charset = next(filter(lambda x: "charset" in x, content_type.split(";"))).split("=")[-1].strip().lower()
    message_body = message.get("payload", dict()).get("body", dict()).get("data")
    decoded_bytes = base64.urlsafe_b64decode(message_body.encode("ASCII"))
    html_text = decoded_bytes.decode(charset)
    try:
        return extract_code_from_html(html_text)
    except Exception as exc:
        logger.error("Возникла ошибка при извлечении кода из письма: %s", exc)
        return ""


def check_authentication(cookies: list) -> bool:
    """Проверяет куки на признак успешной авторизации"""

    auth_cookies_set = set()
    for c in cookies:
        c_name = c.get("name")
        if c_name in AUTH_COOKIES:
            if c_name == "__Secure-user-id" and len(c.get("value")) <= 1:
                return False
            auth_cookies_set.add(c_name)
    if auth_cookies_set != AUTH_COOKIES:
        return False
    return True


async def get_cookies() -> None:
    """Инициирует отправку на email секретного кода для входа в аккаунт ozon.ru,
    авторизуется в сервисе и сохраняет куки в файл для дальнейшего использования
    """

    browser = await zd.start()
    try:
        page = await browser.get("https://data.ozon.ru/?locale=ru")
        await asyncio.sleep(10)
        buttons = await page.find_all("button")
        logger.info("На текущей странице обнаружено %d кнопок", len(buttons))
        for button in buttons:
            if "Перейти к аналитике" in button.text:
                await button.click()
                logger.info("Редирект на страницу авторизации")
                break
        phone_input = await page.wait_for('input[type="tel"]', timeout=30)
        await asyncio.sleep(1)
        await phone_input.send_keys(PHONE)
        submit_button = await page.select('button[type="submit"]')
        await asyncio.sleep(3)
        await submit_button.click()
        await asyncio.sleep(3)
        login_form = await page.find('section[class="csma-ozon-id-page-anonymous"]', timeout=30)
        if "ведите код" not in login_form.text.lower():
            await browser.stop()
            logger.error("Указан некорректный номер телефона для авторизации")
        else:
            await page.evaluate("document.querySelector('input[type=\"text\"]').disabled = false;")
            service = get_gmail_service()
            secret_code = get_code_fom_message(service, SENDER_EMAILS)
            if secret_code == "":
                logger.error("Секретный код не был получен")
            else:
                code_input = await page.find('input[type="text"]', timeout=30)
                await asyncio.sleep(1)
                await code_input.send_keys(secret_code)
                await asyncio.sleep(5)
                cookies = await browser.cookies.get_all()
                cookies_json = [cookie.to_json() for cookie in cookies]  # type: ignore
                logger.info("Получены новые куки")
                if check_authentication(cookies_json):
                    logger.info("Куки соответствуют авторизованному состоянию пользователя")
                    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
                        json.dump(cookies_json, f, indent=2, ensure_ascii=False)
                    logger.info("Куки сохранены в  файл %s", COOKIES_FILE)
                else:
                    logger.info("Авторизация не удалась, куки не были сохранены")
    except Exception as exc:
        logger.critical("При попытке извлеч куки возникла ошибка %s", exc)
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(get_cookies())
