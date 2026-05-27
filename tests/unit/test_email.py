from backend.normalizer.email import classify_email, split_emails, _matches_fio


class TestMatchesFio:
    def test_full_surname_match(self):
        assert _matches_fio("ivanov", "Иванов Иван") is True

    def test_surname_in_compound_local(self):
        assert _matches_fio("ivan.ivanov", "Иванов Иван") is True

    def test_initial_surname(self):
        assert _matches_fio("i.ivanov", "Иванов Иван") is True
        assert _matches_fio("iivanov", "Иванов Иван") is True

    def test_surname_initial(self):
        assert _matches_fio("ivanov.i", "Иванов Иван") is True
        assert _matches_fio("ivanovi", "Иванов Иван") is True

    def test_full_concatenation(self):
        assert _matches_fio("ivanovivan", "Иванов Иван") is True
        assert _matches_fio("ivanivanov", "Иванов Иван") is True

    def test_no_match(self):
        assert _matches_fio("ceo", "Дагмар Буквова") is False
        assert _matches_fio("info", "Иванов Иван") is False
        assert _matches_fio("director", "Иванов Иван") is False

    def test_empty_fio(self):
        assert _matches_fio("ivanov", "") is False

    def test_short_part_ignored(self):
        # Single-letter initials should not trigger a match on their own
        assert _matches_fio("i", "Иванов Иван") is False


class TestClassifyEmail:
    def test_general_locals_always_general(self):
        assert classify_email("info@company.ru") == "general"
        assert classify_email("info@company.ru", "Иванов Иван") == "general"

    def test_with_fio_personal_when_matches(self):
        assert classify_email("ivanov@company.ru", "Иванов Иван") == "personal"
        assert classify_email("i.ivanov@company.ru", "Иванов Иван") == "personal"

    def test_with_fio_general_when_no_match(self):
        # ceo@farmak.cz — the bug case from the email
        assert classify_email("ceo@farmak.cz", "Дагмар Буквова") == "general"
        assert classify_email("director@company.ru", "Иванов Иван") == "general"

    def test_without_fio_unknown_local_is_personal(self):
        assert classify_email("director@company.ru") == "personal"
        assert classify_email("ivanov@company.ru") == "personal"

    def test_invalid_email(self):
        assert classify_email("") == "personal"
        assert classify_email("notanemail") == "personal"


class TestSplitEmails:
    def test_splits_with_fio(self):
        emails = ["ivanov@c.ru", "info@c.ru", "director@c.ru"]
        general, personal = split_emails(emails, full_name="Иванов Иван")
        assert "ivanov@c.ru" in personal
        assert "info@c.ru" in general
        assert "director@c.ru" in general  # no FIO match → general

    def test_splits_without_fio(self):
        emails = ["ivanov@c.ru", "info@c.ru"]
        general, personal = split_emails(emails)
        assert "info@c.ru" in general
        assert "ivanov@c.ru" in personal  # no FIO → GENERAL_LOCALS fallback only
