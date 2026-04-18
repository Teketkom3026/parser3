"""Phone extraction/normalization via phonenumbers."""
from __future__ import annotations

import re
from typing import List


def extract_phones(text: str, default_region: str = "RU") -> List[str]:
    if not text:
        return []
    results, seen = [], set()
    try:
        import phonenumbers
        for match in phonenumbers.PhoneNumberMatcher(text, default_region):
            num = match.number
            if phonenumbers.is_valid_number(num):
                fmt = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
                if fmt not in seen:
                    seen.add(fmt)
                    results.append(fmt)
    except Exception:
        # Fallback pattern
        for m in re.finditer(r"\+?7[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}", text):
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) == 11 and digits.startswith(("7", "8")):
                e164 = "+7" + digits[1:]
                fmt = f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
                if e164 not in seen:
                    seen.add(e164)
                    results.append(fmt)
    return results
