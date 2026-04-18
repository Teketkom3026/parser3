"""pymorphy3 shared instance (heavy to init)."""
from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def get_morph():
    try:
        from pymorphy3 import MorphAnalyzer
        return MorphAnalyzer()
    except Exception:
        # Fallback: no-op analyzer
        class _Noop:
            def parse(self, w):
                class _P:
                    normal_form = w.lower()
                    tag = type("T", (), {"POS": None})()
                    def inflect(self, _): return None
                return [_P()]
        return _Noop()
