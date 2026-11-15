"""Conservative matching of specification references to uploaded filenames."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from collections.abc import Iterable


_WORD = r"[\w]"


def _normalized(value: str) -> str:
    """Normalize presentation differences without deleting identifier syntax."""
    return unicodedata.normalize("NFKC", value).casefold().strip()


def has_document_reference(reference: str, document_names: Iterable[str]) -> bool:
    """Return whether a reference appears as a whole token in any filename stem.

    This deliberately avoids substring matching: a reference ``AB.2`` must not
    be satisfied by ``AB.20`` or ``XAB.2A``. Punctuation inside a designation is
    kept literal, so dots, dashes and slashes cannot become wildcards.
    """
    if not isinstance(reference, str):
        return False
    token = _normalized(reference)
    if not token:
        return False
    pattern = re.compile(rf"(?<!{_WORD}){re.escape(token)}(?!{_WORD})")
    return any(pattern.search(_normalized(Path(name).stem)) for name in document_names)
