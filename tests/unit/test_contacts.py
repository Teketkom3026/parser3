"""Contact extraction: position-line detection, high-score keep, name-only pass."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.extractor.contacts import _is_pos_line, extract_raw_contacts


def test_is_pos_line_accepts_real_positions():
    # включая склонённую форму «директора» (П.1.1 — \w* в _POS_RE)
    for s in ("Генеральный директор", "начальник отдела образования",
              "Заместитель директора по АХЧ", "Главный бухгалтер"):
        assert _is_pos_line(s), s


def test_is_pos_line_rejects_cta_and_embedded_fio():
    """П.3: CTA/маркетинг и строки с ФИО внутри — не должность."""
    for s in ("Вы можете связаться с менеджером",
              "Если директор недоступен",
              "С 19 лет работаю инженером",
              "Руководитель ОДОД – Соколова Людмила Анатольевна",
              "Директор Иванов Иван Иванович"):
        assert not _is_pos_line(s), s


def test_high_score_keeps_contact_without_personal_phone():
    """П.1.1/П.1.2: на странице руководства контакт с ФИО+должностью остаётся без тел/почты."""
    html = "<div class=card><div>Иванов Иван Иванович</div><div>Генеральный директор</div></div>"
    assert len(extract_raw_contacts(html, "https://x.ru/rukovodstvo", 20)) == 1
    # на главной (score 0) без контакта — дропается
    assert len(extract_raw_contacts(html, "https://x.ru/", 0)) == 0


def test_name_only_pass_only_on_high_score():
    """П.1.1: ФИО + телефон без должности — контакт только на high-score странице."""
    html = "<div>Иванов Иван Иванович</div><div>+7 (812) 555-12-34</div>"
    assert len(extract_raw_contacts(html, "https://x.ru/contacts", 5)) == 1
    assert len(extract_raw_contacts(html, "https://x.ru/", 0)) == 0
