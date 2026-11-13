"""Intelligent offline search layer for MailDaan.

Search is always account-specific and fully offline. A layered strategy is
used, cheapest and most reliable layers first:

  1. Direct indexed/exact matches — SQLite FTS5 (unicode61 tokenizer) over
     a normalized search representation of every message.
  2. Normalized and partial matches — normalized substring (LIKE) over the
     same normalized representation.
  3. Keyboard-layout-corrected matches — deterministic conversion between
     the standard Persian (ISIRI) and English (QWERTY) layouts.
  4. Fuzzy suggestions — RapidFuzz against a bounded vocabulary extracted
     from the account archive ("Did you mean …?").

Only normalized search representations are used; the original stored email
content is never modified.
"""

import re
import unicodedata

from rapidfuzz.distance import DamerauLevenshtein

# --------------------------------------------------------------- normalization

# Arabic presentation/letter variants folded to their modern Persian forms.
_LETTER_MAP = str.maketrans({
    "\u064A": "\u06CC",  # Arabic yeh  -> Farsi yeh
    "\u0649": "\u06CC",  # alef maksura -> Farsi yeh
    "\u0626": "\u06CC",  # yeh with hamza -> Farsi yeh
    "\u0643": "\u06A9",  # Arabic kaf -> keheh
    "\u0623": "\u0627",  # alef with hamza above -> alef
    "\u0625": "\u0627",  # alef with hamza below -> alef
    "\u0671": "\u0627",  # alef wasla -> alef
    "\u0624": "\u0648",  # waw with hamza -> waw
    "\u0629": "\u0647",  # ta marbuta -> heh
    "\u0640": "",        # Arabic tatweel (stretching)
    "\u200C": "",        # zero-width non-joiner (nim-faseleh)
    "\u200D": "",        # zero-width joiner
    "\u200E": "",        # left-to-right mark
    "\u200F": "",        # right-to-left mark
    "\u00AD": "",        # soft hyphen
})

# Combining marks: Arabic diacritics (harakat) and Quranic annotation signs.
_DIACRITIC_RE = re.compile(
    "[\u064B-\u0652\u0670\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED\u08F0-\u08F3]"
)

# Persian (۰-۹) and Arabic-Indic (٠-٩) digits to ASCII.
_DIGIT_MAP = str.maketrans({
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})

_CONTROL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f]")

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def normalize_text(value) -> str:
    """Normalize searchable text (Persian/Arabic variants + case + digits).

    Applies NFKC/NFC, folds Persian/Arabic letter variants, strips common
    diacritics, zero-width marks and tatweel, maps digit variants and
    case-folds Latin text. Storage uses the result as a *search
    representation only*; original message content is untouched.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.translate(_LETTER_MAP)
    text = _DIACRITIC_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    text = text.translate(_DIGIT_MAP)
    text = unicodedata.normalize("NFC", text)
    return text.casefold()


def message_search_text(msg: dict) -> str:
    """Build the normalized searchable representation of one message."""
    parts = [
        msg.get("sender_name") or "", msg.get("sender_email") or "",
        msg.get("subject") or "", msg.get("plain_body") or "",
    ]
    for rec in msg.get("recipients") or []:
        parts.append(rec.get("name") or "")
        parts.append(rec.get("email") or "")
    return normalize_text(" \n ".join(parts))


# ------------------------------------------------------ keyboard layout maps

# Standard Persian keyboard (ISIRI) mapping: QWERTY key -> Persian letter.
# Based on the Windows "Persian" / "Persian (Standard)" layouts.
_EN_TO_FA = {
    "q": "ض", "w": "ص", "e": "ث", "r": "ق", "t": "ف", "y": "غ", "u": "ع",
    "i": "ه", "o": "خ", "p": "ح", "[": "ج", "]": "چ",
    "a": "ش", "s": "س", "d": "ی", "f": "ب", "g": "ل", "h": "ا", "j": "ت",
    "k": "ن", "l": "م", ";": "ک", "'": "گ", "\\": "پ",
    "z": "ظ", "x": "ط", "c": "ز", "v": "ر", "b": "ذ", "n": "د", "m": "پ",
    ",": "و", "`": "÷",
}

_FA_TO_EN = {ch: key for key, ch in _EN_TO_FA.items()}

_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)
_PERSIAN_RE = re.compile("[؀-ۿ]")


def _convert_layout(text: str, table: dict) -> str:
    """Deterministic per-character layout conversion.

    Characters that do not exist in the source layout (spaces, punctuation,
    digits, letters of the other script) are preserved unchanged.
    """
    return "".join(table.get(ch, ch) for ch in text)


def keyboard_candidates(raw_query: str):
    """Plausible keyboard-layout corrections of a query.

    Returns a list of (display_text, normalized_text) pairs in priority
    order. Email addresses and URLs are never converted.
    """
    out = []
    if "@" in raw_query or "/" in raw_query:
        return out
    if _LATIN_RE.search(raw_query):
        conv = _convert_layout(raw_query, _EN_TO_FA)
        out.append((conv, normalize_text(conv)))
    if _PERSIAN_RE.search(raw_query):
        conv = _convert_layout(raw_query, _FA_TO_EN)
        out.append((conv, normalize_text(conv)))
    return out


# ----------------------------------------------------------------- FTS helpers

def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_fragment(norm: str) -> dict:
    """Normalized partial (substring) match fragment for messages.search_all."""
    return {
        "sql": "m.search_all LIKE ? ESCAPE '\\'",
        "params": ["%" + escape_like(norm) + "%"],
    }


def fts_expression(norm: str):
    """FTS5 MATCH expression: AND-combined prefix terms, or None."""
    terms = [tok for tok in _WORD_RE.findall(norm) if tok]
    if not terms:
        return None
    return " ".join(tok + "*" for tok in terms)


def fts_fragment(expr: str) -> dict:
    return {
        "sql": "m.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?)",
        "params": [expr],
    }


FALSE_FRAGMENT = {"sql": "0", "params": []}


# ---------------------------------------------------------------- search plan

class SearchBackend:
    """Database access used by the search planner (one per archive)."""

    def __init__(self, conn, fts_ok: bool):
        self.conn = conn
        self.fts_ok = fts_ok
        self._vocab = None

    def count_like(self, norm: str) -> int:
        frag = like_fragment(norm)
        row = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE " + frag["sql"].replace(
                "m.search_all", "search_all"), frag["params"]).fetchone()
        return row[0] if row else 0

    def count_fts(self, expr: str):
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM search_fts WHERE search_fts MATCH ?",
                (expr,)).fetchone()
            return row[0] if row else 0
        except Exception:  # malformed MATCH expression -> treat as no hits
            return 0

    def vocabulary(self):
        if self._vocab is None:
            terms = set()

            def add(value):
                value = normalize_text(value)
                if len(value) >= 3:
                    terms.add(value)
                for tok in _WORD_RE.findall(value):
                    if len(tok) >= 3:
                        terms.add(tok)

            for row in self.conn.execute(
                    "SELECT sender_email, sender_name FROM messages LIMIT 2000"):
                for value in row:
                    add(value or "")
            for row in self.conn.execute(
                    "SELECT email, name FROM recipients LIMIT 1500"):
                for value in row:
                    add(value or "")
            for row in self.conn.execute("SELECT subject FROM messages LIMIT 3000"):
                add(row[0] or "")
            self._vocab = sorted(terms)[:6000]
        return self._vocab

    def fuzzy_best(self, norm: str):
        """Closest vocabulary term within a conservative edit distance.

        Handles missing/extra/substituted letters and adjacent
        transpositions. Very short queries are never matched aggressively
        and the allowed distance grows with query length.
        """
        vocab = self.vocabulary()
        if not vocab or len(norm) < 4:
            return None
        max_dist = 1 if len(norm) <= 5 else 2
        best, best_d = None, max_dist + 1
        for term in vocab:
            if term == norm:
                continue
            dist = DamerauLevenshtein.distance(norm, term, score_cutoff=best_d)
            if dist < best_d:
                best, best_d = term, dist
        return best


def plan(backend: SearchBackend, raw_query: str, suggest: bool = True) -> dict:
    """Resolve a user query into a WHERE fragment and an optional suggestion.

    Returns {"fragment": dict|None, "suggestion": dict|None}. The caller
    merges the fragment into the message query. ``suggest=False`` runs the
    direct layers only (used e.g. for Excel export scope).
    """
    norm = normalize_text(raw_query)
    if not norm:
        return {"fragment": None, "suggestion": None}

    # Layer 1: indexed FTS matches (word/prefix).
    if backend.fts_ok:
        expr = fts_expression(norm)
        if expr and backend.count_fts(expr) > 0:
            return {"fragment": fts_fragment(expr), "suggestion": None}

    # Layer 2: normalized partial (substring) matches.
    if backend.count_like(norm) > 0:
        return {"fragment": like_fragment(norm), "suggestion": None}

    if not suggest:
        return {"fragment": FALSE_FRAGMENT, "suggestion": None}

    # Layer 3: keyboard-layout-corrected matches.
    for text, cand in keyboard_candidates(raw_query):
        if not cand or cand == norm:
            continue
        if backend.count_like(cand) > 0:
            return {
                "fragment": FALSE_FRAGMENT,
                "suggestion": {"type": "keyboard", "text": text},
            }

    # Layer 4: fuzzy suggestions for typographical errors.
    if len(norm) >= 4:
        best = backend.fuzzy_best(norm)
        if best:
            return {
                "fragment": FALSE_FRAGMENT,
                "suggestion": {"type": "fuzzy", "text": best},
            }

    return {"fragment": FALSE_FRAGMENT, "suggestion": None}
