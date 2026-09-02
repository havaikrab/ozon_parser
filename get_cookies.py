import asyncio
import logging
import os
import time

import zendriver as zd
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

phone = os.getenv("PHONE_NUMBER", "")


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
    await phone_input.send_keys(phone)
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
