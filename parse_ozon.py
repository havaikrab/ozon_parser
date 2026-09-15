import json
import logging
import urllib

from curl_cffi import requests

from get_cookies import COOKIES_FILE, HEADERS_FILE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_session() -> requests.Session:
    """Создает объект сессии авторизованного пользователя"""

    with open(COOKIES_FILE, "r", encoding="utf-8") as cookies_file:
        cookies = json.load(cookies_file)
    with open(HEADERS_FILE, "r", encoding="utf-8") as headers_file:
        headers = json.load(headers_file)
    session = requests.Session(impersonate="chrome")
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


sess = get_session()


sku = "5701336276"
qwert = "7439399860.jpg"

base_product_path = f"/product/{sku}/"
base_encoded_url = urllib.parse.quote(base_product_path, safe="")
base_api_url = f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url={base_encoded_url}"
base_response = sess.get(base_api_url)
print(f"Статус: {base_response.status_code}")


product_path = f"/product/{sku}/?layout_container=pdpPage2column&layout_page_index=2"
encoded_url = urllib.parse.quote(product_path, safe="")
api_url = f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url={encoded_url}"
response = sess.get(api_url)
print(f"Статус: {response.status_code}")

result = {"sku": sku}


# title
meta_list = base_response.json().get("seo", dict()).get("meta")
for meta in meta_list:
    if "property" in meta and meta["property"] == "og:title":
        title = meta.get("content", "")
        if title == "":
            logger.error("Название продукта не было найдено по стандартному пути")
        result["title"] = title


# price
PRICE_KEY = "cardPrice"  # 'cardPrice', 'price', 'originalPrice', 'showOriginalPrice', 'pricePerUnit'
price_dict = dict()
base_widget_states = base_response.json().get("widgetStates", dict())
price_keys = filter(lambda x: "webprice" in x.lower(), base_widget_states.keys())
for i in price_keys:
    try:
        inner_price_dict = json.loads(base_widget_states[i])
        price_dict.update(inner_price_dict)
    except Exception:
        continue
price_str = price_dict.get(PRICE_KEY, "")
result["price"] = get_number_from_string(price_str)

# rating, reviews
rating_keys = filter(lambda x: "singleproductscore" in x.lower(), base_widget_states.keys())
rating_data = dict()
for r in rating_keys:
    try:
        rt = json.loads(base_widget_states[r])
        rating_data.update(rt)
    except Exception:
        continue
rating_list = rating_data.get("text", "").split("•")
if len(rating_list) == 2:
    result["rating"] = get_number_from_string(rating_list[0])
    result["reviews"] = int(get_number_from_string(rating_list[1]))
else:
    logger.error("Не удалось обнаружить сведения об отзывах на продукт")
    result["rating"] = 0.0
    result["reviews"] = 0

# cover, images
gallery_keys = filter(lambda x: "gallery" in x.lower(), base_widget_states.keys())
gallery_data = dict()
for g in gallery_keys:
    try:
        gd = json.loads(base_widget_states[g])
        gallery_data.update(gd)
    except Exception:
        continue
cover_image = gallery_data.get("coverImage", "")
if cover_image == "":
    logger.error("Не удалось получить ссылку на главное изображение продукта")
result["cover_image"] = cover_image
images = gallery_data.get("images", list())
images_count = len([x for x in images if "src" in x])
result["photos_seller"] = images_count
videos_seller = gallery_data.get("videos", list())
result["videos_seller"] = len(videos_seller)


# color, material
widget_states = response.json().get("widgetStates", dict())
characteristics_key = next(filter(lambda x: "characteristics" in x.lower(), widget_states.keys()))
result["material_list"] = list()
try:
    short_characteristics = (
        json.loads(widget_states[characteristics_key]).get("characteristics", list())[0].get("short", list())
    )
    color_characteristics = next(filter(lambda x: x["key"].lower() == "color", short_characteristics)).get(
        "values", list()
    )
    result["color"] = ", ".join([i["text"] for i in color_characteristics])
except Exception:
    logger.error("Сведения о цвете продукта на были найдены по стандартному пути")
try:
    short_characteristics = (
        json.loads(widget_states[characteristics_key]).get("characteristics", list())[0].get("short", list())
    )
    material_characteristics = filter(lambda x: "material" in x["key"].lower(), short_characteristics)
    for m in material_characteristics:
        result["material_list"].extend(m.get("values", list()))
    result["material"] = ", ".join([i.get("text") for i in result.pop("material_list", dict())])
except Exception:
    logger.error("Сведения о материале продукта на были найдены по стандартному пути")

print(result)

# # art_set, article, mpn, manufacturer_sku
#
# for k, v in base_widget_states.items():
#     if 'LS24D400GAIXCI' in str(v).upper():
#         print(k)
#         for p, e in json.loads(v).items():
#             if 'LS24D400GAIXCI' in str(e).upper():
#                 print('    ', p)
#                 print('        ', e)
