from backend.deduper.deduper import dedup, dedup_key, _completeness


def _c(**kw):
    base = {"domain": "test.ru", "full_name": "", "person_email": "",
            "person_phone": "", "company_email": "", "company_phone": "",
            "position_raw": "", "position_canonical": ""}
    base.update(kw)
    return base


class TestDedupKey:
    def test_fio_is_primary_key(self):
        a = _c(full_name="Иванов Иван", person_email="ivan@test.ru")
        b = _c(full_name="Иванов Иван", person_phone="79001234567")
        assert dedup_key(a) == dedup_key(b)

    def test_different_fio_different_key(self):
        a = _c(full_name="Иванов Иван")
        b = _c(full_name="Петров Пётр")
        assert dedup_key(a) != dedup_key(b)

    def test_same_fio_different_domain(self):
        a = _c(full_name="Иванов Иван", domain="alpha.ru")
        b = _c(full_name="Иванов Иван", domain="beta.ru")
        assert dedup_key(a) != dedup_key(b)

    def test_no_fio_falls_back_to_email(self):
        a = _c(person_email="info@test.ru")
        b = _c(company_email="info@test.ru")
        assert dedup_key(a) == dedup_key(b)

    def test_no_fio_no_email_falls_back_to_phone(self):
        a = _c(person_phone="79001234567")
        b = _c(company_phone="79001234567")
        assert dedup_key(a) == dedup_key(b)


class TestCompleteness:
    def test_email_beats_phone(self):
        a = _c(full_name="Иванов Иван", person_email="ivan@test.ru")
        b = _c(full_name="Иванов Иван", person_phone="79001234567")
        assert _completeness(a) > _completeness(b)

    def test_phone_beats_position_only(self):
        a = _c(full_name="Иванов Иван", person_phone="79001234567")
        b = _c(full_name="Иванов Иван", position_canonical="Директор")
        assert _completeness(a) > _completeness(b)


class TestDedup:
    def test_merges_same_fio_keeps_email_record(self):
        a = _c(full_name="Иванов Иван", person_email="ivan@test.ru")
        b = _c(full_name="Иванов Иван", person_phone="79001234567")
        result = dedup([a, b])
        assert len(result) == 1
        assert result[0]["person_email"] == "ivan@test.ru"

    def test_different_fio_not_merged(self):
        a = _c(full_name="Иванов Иван")
        b = _c(full_name="Петров Пётр")
        assert len(dedup([a, b])) == 2

    def test_same_fio_different_domain_not_merged(self):
        a = _c(full_name="Иванов Иван", domain="alpha.ru")
        b = _c(full_name="Иванов Иван", domain="beta.ru")
        assert len(dedup([a, b])) == 2

    def test_three_duplicates_one_survives(self):
        a = _c(full_name="Иванов Иван")
        b = _c(full_name="Иванов Иван", person_phone="79001234567")
        c = _c(full_name="Иванов Иван", person_email="ivan@test.ru")
        result = dedup([a, b, c])
        assert len(result) == 1
        assert result[0]["person_email"] == "ivan@test.ru"

    def test_empty_input(self):
        assert dedup([]) == []

    def test_case_insensitive_fio(self):
        a = _c(full_name="Иванов Иван")
        b = _c(full_name="иванов иван")
        assert len(dedup([a, b])) == 1
