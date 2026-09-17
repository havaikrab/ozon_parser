import asyncio
import csv
import json
import logging
import urllib

from curl_cffi import requests
from curl_cffi.requests import AsyncSession, Response

from get_cookies import COOKIES_FILE, HEADERS_FILE, get_cookies

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


PRICE_KEY = "cardPrice"  # Варианты цен: 'cardPrice', 'price', 'originalPrice', 'showOriginalPrice', 'pricePerUnit'
ERRORS_LIMIT = 3  # Максимальное число ошибок в while-цикле при переборе списка артикулов продуктов
REQUEST_INTERVAL = 1  # Время ожидания между запросами к внутреннему api ozon.ru
BASE_CSV = "data/ozon_products.csv"  # Файл для штатного сохранения информации о продуктах
BACKUP_CSV = "data/backup.csv"  # Файл для аварийного сохранения информации о продуктах
# Убедиться, что директории существуют

SKU_LIST: list[str] = list()  # Личный список артикулов продуктов на ozon.ru


class SessionExpiredError(Exception):
    """Исключение для обновления сессии"""

    def __init__(self, status_code: int = 403, message: str = "Необходимо обновить сессию"):
        self.status_code = status_code
        self.message = message
        super().__init__(self.message)


async def get_session() -> requests.AsyncSession:
    """Создает объект асинхронной сессии, имитирующей авторизованного пользователя"""

    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as cookies_file:
            cookies = json.load(cookies_file)
        with open(HEADERS_FILE, "r", encoding="utf-8") as headers_file:
            headers = json.load(headers_file)
    except FileNotFoundError:
        await get_cookies()
        with open(COOKIES_FILE, "r", encoding="utf-8") as cookies_file:
            cookies = json.load(cookies_file)
        with open(HEADERS_FILE, "r", encoding="utf-8") as headers_file:
            headers = json.load(headers_file)
    session = AsyncSession(impersonate="chrome")
    session.cookies.update(cookies)
    session.headers = headers
    return session


def get_number_from_string(string: str) -> float:
    """Преобразует строку в число с плавающей точкой"""

    clear_str = ""
    for s in string:
        if s.isdigit() or s == ".":
            clear_str += s
        elif s == ",":
            clear_str += "."
    if clear_str == "":
        return 0.0
    return float(clear_str)


def parse_product(ozon_prodict_id: str, base_response: Response, additional_response: Response) -> dict:
    """Возвращает сведения о продукте, размещенном на сайте ozon.ru"""

    result = {
        "sku": ozon_prodict_id,
        "title": "",
        "price": None,
        "rating": 0.0,
        "reviews": 0,
        "cover_image": "",
        "photos_seller": 0,
        "videos_seller": 0,
        "color": "",
        "material": "",
    }
    base_widget_states = base_response.json().get("widgetStates", dict())
    base_widget_keys = base_widget_states.keys()

    # title
    meta_list = base_response.json().get("seo", dict()).get("meta")
    for meta in meta_list:
        if "property" in meta and meta["property"] == "og:title":
            title = meta.get("content", "")
            if title == "":
                logger.error("Название продукта <s%> не было найдено по стандартному пути", ozon_prodict_id)
            result["title"] = title

    # price
    price_dict = dict()
    price_keys = filter(lambda x: "webprice" in x.lower(), base_widget_keys)
    for p in price_keys:
        try:
            price_dict.update(json.loads(base_widget_states[p]))
        except Exception:
            continue
    price_str = price_dict.get(PRICE_KEY, "")
    price = get_number_from_string(price_str)
    if price > 0:
        result["price"] = price
    else:
        logger.error("Цена продукта <%s> не была найдена по стандартному пути", ozon_prodict_id)

    # rating, reviews
    rating_keys = filter(lambda x: "singleproductscore" in x.lower(), base_widget_keys)
    rating_data = dict()
    for r in rating_keys:
        try:
            rating_data.update(json.loads(base_widget_states[r]))
        except Exception:
            continue
    rating_list = rating_data.get("text", "").split("•")
    if len(rating_list) == 2:
        result["rating"] = get_number_from_string(rating_list[0])
        result["reviews"] = int(get_number_from_string(rating_list[1]))
    else:
        logger.error("Не удалось обнаружить сведения об отзывах на продукт <%s>", ozon_prodict_id)

    # cover, images
    gallery_keys = filter(lambda x: "gallery" in x.lower(), base_widget_keys)
    gallery_data = dict()
    for g in gallery_keys:
        try:
            gallery_data.update(json.loads(base_widget_states[g]))
        except Exception:
            continue
    cover_image = gallery_data.get("coverImage", "")
    if cover_image == "":
        logger.error("Не удалось получить ссылку на главное изображение продукта <%s>", ozon_prodict_id)
    result["cover_image"] = cover_image
    images = gallery_data.get("images", list())
    result["photos_seller"] = len([x for x in images if "src" in x])
    result["videos_seller"] = len(gallery_data.get("videos", list()))

    # color, material
    additional_widget_states = additional_response.json().get("widgetStates", dict())
    characteristics_key = next(filter(lambda x: "characteristics" in x.lower(), additional_widget_states.keys()))
    try:
        short_characteristics = (
            json.loads(additional_widget_states[characteristics_key])
            .get("characteristics", list())[0]
            .get("short", list())
        )
    except Exception:
        logger.error(
            "Сведения о цвете и материале продукта <%s> на были найдены по стандартному пути", ozon_prodict_id
        )
    else:
        # color
        color_filter = [x for x in short_characteristics if str(x.get("key", "")).lower() == "color"]
        if len(color_filter) > 0:
            color_characteristics = color_filter[0].get("values", list())
            result["color"] = ", ".join(
                [i.get("text") for i in color_characteristics if isinstance(i.get("text"), str)]
            )

        # material
        material_list = list()
        material_characteristics = filter(lambda x: "material" in x.get("key", "").lower(), short_characteristics)
        for m in material_characteristics:
            material_list.extend(m.get("values", list()))
        result["material"] = ", ".join([i.get("text") for i in material_list if isinstance(i.get("text"), str)])

    return result


async def get_responses(session: AsyncSession, ozon_prodict_id: str) -> tuple:
    """Возвращает два объекта Response с информацией о продукте с сайта ozon.ru"""

    base_product_path = f"/product/{ozon_prodict_id}/"
    base_encoded_url = urllib.parse.quote(base_product_path, safe="")
    base_api_url = f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url={base_encoded_url}"

    additional_product_path = f"/product/{ozon_prodict_id}/?layout_container=pdpPage2column&layout_page_index=2"
    additional_encoded_url = urllib.parse.quote(additional_product_path, safe="")
    additional_api_url = f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url={additional_encoded_url}"

    base_response, additional_response = await asyncio.gather(
        session.get(base_api_url), session.get(additional_api_url)
    )
    if base_response.status_code == 403 or additional_response.status_code == 403:
        raise SessionExpiredError
    return base_response, additional_response


def write_data_to_scv(data: list, file_name: str) -> None:
    """Записывает извлеченную информацию о продуктах в csv-файл"""

    with open(file_name, "w", newline="", encoding="utf-8") as file:
        field_names = [
            "sku",
            "title",
            "price",
            "rating",
            "reviews",
            "cover_image",
            "photos_seller",
            "videos_seller",
            "color",
            "material",
        ]
        writer = csv.DictWriter(file, fieldnames=field_names, delimiter=";")
        writer.writeheader()
        for sku in data:
            writer.writerow(sku)


async def parse_ozon(sku_list: list) -> None:
    """Принимает список sku продуктов, размещенных на ozon.ru, результаты парсинга сохраняет в csv-файл"""

    index = 0
    last_success = 0
    result = list()
    while index < len(sku_list):
        if index - last_success <= ERRORS_LIMIT:
            session = await get_session()
            async with session:
                while index < len(sku_list):
                    if index - last_success <= ERRORS_LIMIT:
                        try:
                            responses = await get_responses(session, sku_list[index])
                            result.append(parse_product(sku_list[index], *responses))
                            index += 1
                            last_success += 1
                            await asyncio.sleep(REQUEST_INTERVAL)
                        except SessionExpiredError:
                            await get_cookies()
                            logger.warning("Куки обновлены")
                            break
                        except Exception as exc:
                            logger.critical(
                                "Ошибка при извлечении информации о продукте <%s>:\n%s", sku_list[index], exc
                            )
                            index += 1
                    else:
                        logger.critical("Превышен лимит невалидных запросов")
                        break
        else:
            write_data_to_scv(result, BACKUP_CSV)
            logger.error("Полученные данные сохранены в файл %s", BACKUP_CSV)
            break
    write_data_to_scv(result, BASE_CSV)
    logger.warning("Данные успешно получены и сохранены в файл %s", BASE_CSV)


if __name__ == "__main__":
    asyncio.run(parse_ozon(SKU_LIST))
