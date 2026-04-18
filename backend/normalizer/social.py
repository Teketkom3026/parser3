"""Social link filter."""
from __future__ import annotations

import re
from typing import List


_PATTERNS = [
    r"https?://(?:www\.)?vk\.com/[\w\-./]+",
    r"https?://(?:www\.)?t\.me/[\w\-./]+",
    r"https?://(?:www\.)?facebook\.com/[\w\-./]+",
    r"https?://(?:www\.)?instagram\.com/[\w\-./]+",
    r"https?://(?:www\.)?linkedin\.com/(?:in|company)/[\w\-./]+",
    r"https?://(?:www\.)?youtube\.com/(?:channel|user|c)/[\w\-./]+",
    r"https?://(?:www\.)?ok\.ru/[\w\-./]+",
    r"https?://(?:www\.)?twitter\.com/[\w\-./]+",
    r"https?://(?:www\.)?x\.com/[\w\-./]+",
    r"https?://(?:www\.)?dzen\.ru/[\w\-./]+",
]

_BAD_PATHS = {
    "vk.com": re.compile(r"^(js|rtrg|share|widget|audio|video|doc|photo|market|images|lib|assets|cdn|away|wall)", re.I),
    "t.me": re.compile(r"^(share|joinchat/[^/]+$|proxy|addlist)", re.I),
    "facebook.com": re.compile(r"^(tr|sharer|dialog|plugins)", re.I),
    "linkedin.com": re.compile(r"^(share|sharing)", re.I),
    "twitter.com": re.compile(r"^(share|intent)", re.I),
    "x.com": re.compile(r"^(share|intent)", re.I),
}

_ASSET_SUFFIX = (".js", ".css", ".php", ".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp")


def _is_profile_url(url: str) -> bool:
    m = re.match(r"https?://(?:www\.)?([^/]+)/([^/?#]*)", url)
    if not m:
        return False
    host, path = m.group(1).lower(), m.group(2)
    if not path:
        return False
    bad = _BAD_PATHS.get(host)
    if bad and bad.match(path):
        return False
    if path.lower().endswith(_ASSET_SUFFIX):
        return False
    return True


def extract_social_links(html_or_text: str) -> List[str]:
    if not html_or_text:
        return []
    seen, out = set(), []
    for pat in _PATTERNS:
        for m in re.finditer(pat, html_or_text, re.IGNORECASE):
            url = m.group(0).rstrip("/").split("?")[0].split("#")[0]
            if not _is_profile_url(url):
                continue
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
    return out
