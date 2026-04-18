"""Position normalization tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.normalizer.position import normalize_position
from backend.classifier.sheet_router import route


def test_ceo_routes_to_ceo_sheet():
    norm = normalize_position("Генеральный директор")
    assert norm is not None
    sheet = route(norm, person_full_name="Иванов Иван Иванович")
    assert sheet == "Генеральные директора"


def test_deputy_ceo_not_in_ceo_sheet():
    norm = normalize_position("Заместитель генерального директора")
    assert norm is not None
    sheet = route(norm, person_full_name="Петров Петр")
    assert sheet != "Генеральные директора"


def test_tech_director_goes_to_chief_engineers():
    norm = normalize_position("Технический директор")
    assert norm is not None
    sheet = route(norm, person_full_name="Сидоров Сидор")
    assert sheet == "Главные инженеры"


def test_chief_accountant():
    norm = normalize_position("Главный бухгалтер")
    assert norm is not None
    sheet = route(norm, person_full_name="Кузнецов")
    assert sheet == "Главные бухгалтеры"


def test_plain_director_not_ceo():
    # plain "Директор" without гендиректор qualifier should NOT route to CEO sheet
    norm = normalize_position("Директор по развитию")
    sheet = route(norm, person_full_name="Иванов") if norm else "Остальные"
    assert sheet != "Генеральные директора"
