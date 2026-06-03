"""FIO validation tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.normalizer.fio import is_valid_person_name, normalize_fio, split_fio_raw


def test_rejects_english_noise():
    assert not is_valid_person_name("About Us")
    assert not is_valid_person_name("Our Team")
    assert not is_valid_person_name("Contact Us")


def test_accepts_russian_name():
    assert is_valid_person_name("Иванов Иван Иванович")
    assert is_valid_person_name("Петров Петр")


def test_split_fio():
    parts = split_fio_raw("Иванов Иван Иванович")
    assert parts is not None
    last, first, middle = parts
    assert last == "Иванов"
    assert first == "Иван"
    assert middle == "Иванович"


def test_morph_gate_drops_institutional():
    """П.4: институциональные фразы (прилагательное+существительное) — не ФИО."""
    for s in ("Российской Федерации", "Администрации Санкт-",
              "Образовательного учреждения"):
        assert not is_valid_person_name(s), s


def test_morph_gate_keeps_real_names():
    """П.4: реальные имена сохраняются, даже с редкой фамилией (имя/отчество → Name/Patr)."""
    for s in ("Бельская Марина Владимировна", "Чоп Ольга Александровна",
              "Иванов Иван Иванович", "Петров Петр"):
        assert is_valid_person_name(s), s


def test_two_identical_words_not_fio():
    """П.4: два одинаковых слова подряд — не ФИО."""
    assert split_fio_raw("Переговоры Переговоры") is None
    assert split_fio_raw("Иванов Иванов") is None


def test_two_token_order():
    """П.7: фамилия определяется pymorphy + суффиксами, оба порядка корректны."""
    assert split_fio_raw("Мария Иванова") == ("Иванова", "Мария", "")
    assert split_fio_raw("Иванова Мария") == ("Иванова", "Мария", "")
    assert split_fio_raw("Иван Иванов") == ("Иванов", "Иван", "")
    # имя на -ина не должно приниматься за фамилию в порядке «Фамилия Имя»
    assert split_fio_raw("Чоп Марина") == ("Чоп", "Марина", "")


def test_gender_detection():
    """BUG-005: пол по отчеству (3 токена) и по женской фамилии (2 токена)."""
    assert normalize_fio("Иванов Иван Иванович").gender == "М"
    assert normalize_fio("Петрова Мария Сергеевна").gender == "Ж"
    # 2 токена, имя не на а/я — пол по суффиксу фамилии (оживлённая мёртвая ветка)
    assert normalize_fio("Симакова Нелли").gender == "Ж"
