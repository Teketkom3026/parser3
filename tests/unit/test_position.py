"""Position normalization tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.normalizer.position import normalize_position, _clean
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


def test_dash_does_not_eat_letter():
    """П.3: тире-разделитель схлопывается в пробел, первая буква слова не съедается."""
    assert _clean("Менеджер – продажи") == "Менеджер продажи"
    assert _clean("Инженер - наладчик") == "Инженер наладчик"


def test_deputy_modifier_not_doubled():
    """П.3: «Заместитель директора …» не превращается в «Заместителю заместителя …»."""
    c = normalize_position("Заместитель директора по информатизации").canonical
    assert c == "Заместителю директора по информатизации"
    assert "заместителя" not in c.lower()


def test_executive_director_goes_to_others():
    """DX1: исполнительные директора → лист «Остальные», не в Ген.директора.

    Покрывает точную фразу, хвост (через ceo раньше уходил в Ген.директора),
    англ. варианты и canonical (не должен подменяться на «Генеральный директор»).
    """
    for raw in (
        "Исполнительный директор",
        "Исполнительный директор компании",
        "Исполнительный директор по развитию",
        "Executive Director",
        "Managing Director",
    ):
        norm = normalize_position(raw)
        assert norm.matched_id == "executive_director", raw
        assert route(norm, person_full_name="Иванов Иван") == "Остальные", raw
        assert "генеральн" not in norm.canonical.lower(), raw


def test_no_doubled_po_clause():
    """BUG-004: каноникал с «по X» + сырой хвост «по Y» не дают «по X по Y»."""
    assert normalize_position("Директор по финансам и экономике").canonical == \
        "Директору по финансам и экономике"
    # обычный «роль + по Y» по-прежнему работает
    assert normalize_position("Менеджер по продажам муки").canonical == \
        "Менеджеру по продажам муки"
