import asyncio
import base64
import json
import logging
import os
import re

import zendriver as zd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from zendriver.core.browser import Browser
from zendriver.core.tab import Tab

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()


CREDENTIALS_FILE = "secrets/credentials.json"  # Файл, содержащий секрет для подключения к Google Cloud
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]  # Список разрешений Google Cloud для данного приложения
EMAIL_TOKEN_FILE = "secrets/token.json"  # Имя файла для сохранения токенов доступа к сервисам Google

PHONE = os.getenv("PHONE_NUMBER", "")
SENDER_EMAILS = ["mailer@sender.ozon.ru"]  # Список потенциальных отправителей секретного кода авторизации
START_URL = "https://data.ozon.ru/?locale=ru"  # Адрес стартовой страницы для авторизации

MAX_ATTEMPTS = 3  # Максимальное число попыток соединения с отдельными сервисами и страницами при авторизации
TIMEOUT_LIMIT = 10  # Максимальное время ожидания страницы или элемента при переходе к следующему этапу авторизации
CODE_LENGTH = 6  # Ожидаемая длина кода авторизации
AUTH_COOKIES = {
    "__Secure-token",
    "__Secure-refresh-token",
    "__Secure-access-token",
    "__Secure-idp-token",
    "__Secure-user-id",
}  # Список куков, необходимых для авторизации
COOKIES_FILE = "secrets/cookies.json"  # Файл для сохранения куков сессии авторизованного пользователя
HEADERS_FILE = "secrets/headers.json"  # Файл для сохранения заголовков браузера при успешной авторизации
# Убедиться, что директории существуют


async def get_gmail_service() -> Resource:
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


async def get_code_fom_message(gmail_service: Resource, senders: list) -> str:
    """Возвращает секретный код из письма"""

    query = " OR ".join([f"from:{s}" for s in senders])
    query += " is:unread in:anywhere"
    messages_list = None
    for i in range(MAX_ATTEMPTS):
        result = gmail_service.users().messages().list(userId="me", q=query).execute()  # type: ignore
        messages_list = result.get("messages")
        if messages_list is not None:
            break
        logger.warning("Письмо с кодом не доставлено, следующая проверка через %d секунд", TIMEOUT_LIMIT)
        await asyncio.sleep(TIMEOUT_LIMIT)
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


async def save_cookies_and_headers(browser: Browser, current_tab: Tab, page: Tab, secret_code: str) -> None:
    """Вводит секретный код авторизации,
    получает и сохраняет заголовки браузера и куки авторизованного пользователя в соответствующие файлы"""

    code_input = await page.find('input[type="text"]', timeout=TIMEOUT_LIMIT)
    async with current_tab.expect_response(re.compile(r".*data\.ozon\.ru.*")) as response:
        await code_input.send_keys(secret_code)
        for i in range(MAX_ATTEMPTS):
            try:
                await asyncio.sleep(3)
                await page.find("Избранное", timeout=5)
                logger.info("Авторизация прошла успешно")
            except Exception as exc:
                logger.error("Авторизоваться не удалось.\nОшибка %s", exc)
            else:
                cookies = await browser.cookies.get_all()
                cookies_list = [cookie.to_json() for cookie in cookies]  # type: ignore
                logger.info("Получены новые куки")
                if check_authentication(cookies_list):
                    logger.info("Куки соответствуют авторизованному состоянию пользователя")
                    cookies_dict = {c["name"]: c["value"] for c in cookies_list}
                    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
                        json.dump(cookies_dict, f, indent=4, ensure_ascii=False)
                    logger.info("Куки сохранены в  файл %s", COOKIES_FILE)
                    event = await response.request
                    headers = dict(event.headers)
                    with open(HEADERS_FILE, "w", encoding="utf-8") as file:
                        json.dump(headers, file, ensure_ascii=False, indent=4)
                        logger.info("Заголовки браузера сохранены в файл %s", HEADERS_FILE)
                else:
                    logger.info("Куки не соответствуют авторизованному состоянию пользователя")
                break


async def get_cookies() -> None:
    """Инициирует отправку на email секретного кода для входа в аккаунт ozon.ru,
    авторизуется в сервисе, сохраняет заголовки браузера и куки в файл для дальнейшего использования
    """

    browser = await zd.start()
    current_tab = browser.tabs[0]
    page = await current_tab.get(START_URL)
    await asyncio.sleep(3)
    for _ in range(MAX_ATTEMPTS):
        try:
            button = await page.find("Перейти к аналитике", timeout=5)
            await asyncio.sleep(1)
            await button.click()
        except Exception as exc:
            logger.error("Не удалось перейти на страницу ввода номера телефона.\nОшибка %s", exc)
            await asyncio.sleep(5)
        else:
            logger.info("Редирект на страницу авторизации")
            try:
                phone_input = await page.wait_for('input[type="tel"]', timeout=TIMEOUT_LIMIT)
                await asyncio.sleep(1)
                await phone_input.send_keys(PHONE)
                submit_button = await page.select('button[type="submit"]')
                await asyncio.sleep(1)
                await submit_button.click()
                await asyncio.sleep(3)
                code_form = await page.find('section[class="csma-ozon-id-page-anonymous"]', timeout=TIMEOUT_LIMIT)
            except Exception as exc:
                logger.error("Не удалось перейти на страницу ввода секретного кода.\nОшибка %s", exc)
            else:
                await asyncio.sleep(5)
                if "ведите код" not in code_form.text.lower():
                    logger.error("Указан некорректный номер телефона для авторизации")
                else:
                    await page.evaluate("document.querySelector('input[type=\"text\"]').disabled = false;")
                    service = await get_gmail_service()
                    secret_code = await get_code_fom_message(service, SENDER_EMAILS)
                    if secret_code == "":
                        logger.error("Секретный код из почты не был получен")
                    else:
                        await save_cookies_and_headers(browser, current_tab, page, secret_code)
            break
    await browser.stop()


if __name__ == "__main__":
    asyncio.run(get_cookies())
