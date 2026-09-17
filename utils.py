import json
from typing import Iterable


def search_key(collection: Iterable, key_part: str) -> list:
    """Отбирает из коллекции элементы, содержащие искомую подстроку"""

    filtered_list = list()
    if isinstance(collection, dict):
        for key in collection.keys():
            value = collection[key]
            if key_part.lower() in str(value).lower():
                filtered_list.append(value)
    if isinstance(collection, str):
        filtered_list.append(collection)
    if isinstance(collection, list):
        for index in collection:
            if key_part.lower() in str(index).lower():
                filtered_list.append(index)
    return filtered_list


def repeat_search_key(elements_list: list, key_part: str) -> list:
    """Рекурсивно перебирает элементы списка и возвращает список строк, содержащих искомую подстроку"""

    strings = list()
    is_again = False
    for element in elements_list:
        if not isinstance(element, str):
            strings.extend(search_key(element, key_part))
            is_again = True
        else:
            try:
                strings.append(json.loads(element))
                is_again = True
            except Exception:
                strings.append(element)
    if is_again:
        return repeat_search_key(strings, key_part)
    return elements_list
