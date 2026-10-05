"""
Quote-aware text scanning: the layer that decides what is a word of its own.

The lowest of the front-end modules. It owns the three-quote scanner
(``_skip_quoted`` and the splitters built on it), ``strip_comments``, and the
clause-head recognition ``split_clauses`` / ``clause_heads`` do, so a pattern or a
RETURN never writes its own scanner. ``TranslateError`` and the clause-head tables
live here for the same reason: an unterminated literal is refused by this layer
before anything above it has run, and which words are clause heads is a fact about
the text, not about the plan.

Nothing here knows a pattern, a RETURN or a plan exists, and nothing in it raises
anything but ``TranslateError``.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

#: Clause heads this front-end reads. ``ORDER`` is ``ORDER BY``; ``OFFSET`` is the
#: ``LIMIT m OFFSET n`` spelling a hand-typed query may use.
_SUPPORTED_HEADS = frozenset({"MATCH", "WHERE", "RETURN", "ORDER", "SKIP", "LIMIT", "OFFSET"})

#: Heads recognised well enough to know they are a clause, and refused. The value is the
#: phrase the refusal names, so the message says what arrived rather than "unsupported".
_REFUSED_HEADS: Dict[str, str] = {
    "OPTIONAL": "OPTIONAL MATCH",
    "WITH": "WITH",
    "UNWIND": "UNWIND",
    "HAVING": "HAVING",
    "CALL": "CALL { } subqueries",
    "UNION": "UNION",
    "FOREACH": "FOREACH",
    "LOAD": "LOAD CSV",
    "USE": "USE",
    "CREATE": "CREATE",
    "MERGE": "MERGE",
    "DELETE": "DELETE",
    "DETACH": "DETACH DELETE",
    "SET": "SET",
    "REMOVE": "REMOVE",
    "DROP": "DROP",
    "ALTER": "ALTER",
    "INSERT": "INSERT",
    "UPDATE": "UPDATE",
}

_QUOTES = "\"'`"
#: A word only starts a clause where the previous character ended a word. Searched, not
#: matched: ``re.match`` anchors at the front, and the text to test is everything before
#: the word, so an anchored "last character" test has to be a search.
_ENDS_WORD = re.compile(r"[.\w]$")
#: ``STARTS WITH`` / ``ENDS WITH``: the ``WITH`` belongs to the operator, not to a clause.
_OPERATOR_WITH = re.compile(r"(?:^|[^\w.])(?:starts|ends)[ \t\r\n]+$", re.IGNORECASE)


class TranslateError(Exception):
    """The statement is not something this front-end can plan. Names what it found."""


def _skip_quoted(text: str, i: int) -> int:
    """Index just past the run that opens at ``text[i]``, in any of the three quote styles."""
    quote = text[i]
    j = i + 1
    n = len(text)
    while j < n:
        ch = text[j]
        if quote == "`":
            if ch == "`":
                if j + 1 < n and text[j + 1] == "`":
                    j += 2
                    continue
                return j + 1
        elif ch == "\\":
            j += 2
            continue
        elif ch == quote:
            return j + 1
        j += 1
    what = "backtick" if quote == "`" else "string literal"
    raise TranslateError("the statement ends inside an unterminated %s run" % what)


def _index_outside(text: str, ch: str, start: int = 0) -> int:
    """The index of the first ``ch`` that is not inside a quoted run, or -1."""
    i = start
    while i < len(text):
        cur = text[i]
        if cur in _QUOTES:
            i = _skip_quoted(text, i)
            continue
        if cur == ch:
            return i
        i += 1
    return -1


def _split_outside(text: str, sep: str) -> List[str]:
    """Split on every ``sep`` that is not inside a quoted run."""
    parts: List[str] = []
    start = 0
    while True:
        i = _index_outside(text, sep, start)
        if i < 0:
            parts.append(text[start:])
            return parts
        parts.append(text[start:i])
        start = i + 1


def _split_once(text: str, sep: str) -> Tuple[str, Optional[str]]:
    """``(before, after)`` on the first unquoted ``sep``, or ``(text, None)``."""
    i = _index_outside(text, sep)
    if i < 0:
        return text, None
    return text[:i], text[i + 1 :]


def _split_on_word(text: str, word: str) -> Tuple[str, Optional[str]]:
    """
    ``(before, after)`` on the first ``word`` standing alone outside quotes.

    For ``AS``: a property called ``cost_as_total`` and a literal ``'AS new'`` both have
    to keep meaning what they say, so the split is by word and not by substring.
    """
    pattern = re.compile(r"(?<![.\w])%s(?![.\w])" % re.escape(word), re.IGNORECASE)
    i = 0
    while i < len(text):
        if text[i] in _QUOTES:
            i = _skip_quoted(text, i)
            continue
        match = pattern.match(text, i)
        if match:
            return text[: match.start()].strip(), text[match.end() :].strip()
        i += 1
    return text.strip(), None


def _bracket_body(text: str, opener: str, closer: str) -> Optional[str]:
    """
    The content of the first balanced ``opener ... closer`` pair, quotes left intact.

    ``[r:T1|T2]`` and ``count(n)`` both come from this. Depth counts unquoted openers,
    so a ``]`` inside a quoted relationship type cannot close the bracket early.
    """
    start = _index_outside(text, opener)
    if start < 0:
        return None
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch in _QUOTES:
            i = _skip_quoted(text, i)
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
            if depth < 0:
                raise TranslateError("unbalanced %r in %r" % (closer, text))
        i += 1
    raise TranslateError("unbalanced %r in %r" % (opener, text))


def strip_comments(text: str) -> str:
    """Drop ``// ...`` and ``/* ... */``, leaving quoted runs alone."""
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _QUOTES:
            j = _skip_quoted(text, i)
            out.append(text[i:j])
            i = j
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise TranslateError("the statement ends inside an unterminated block comment")
            i = end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_clauses(text: str) -> List[Tuple[str, str]]:
    """
    Break a statement into ``(head, body)`` pairs, in order, heads upper-cased.

    A word is a head only where it stands alone (so ``n.limit`` is not ``LIMIT`` and
    ``reorder`` is not ``ORDER``), outside every quoted run (so ``= 'RETURN me'`` is not
    ``RETURN``) and inside no bracket (so a list member that spells a clause is not
    one). ``WITH`` right after ``STARTS`` or ``ENDS`` is the operator's own word. Text
    ahead of the first head is dropped; both refused and supported heads come back,
    because refusing is the caller's decision, not the scanner's.
    """
    clauses: List[Tuple[str, str]] = []
    head: Optional[str] = None
    buf: List[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _QUOTES:
            j = _skip_quoted(text, i)
            buf.append(text[i:j])
            i = j
            continue
        if ch in "([{":
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            buf.append(ch)
            i += 1
            continue
        if depth == 0 and ch.isalpha():
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            upper = text[i:j].upper()
            if (
                upper in _SUPPORTED_HEADS
                or upper in _REFUSED_HEADS
            ) and _stands_alone(text, i) and not (upper == "WITH" and _OPERATOR_WITH.search("".join(buf))):
                if head is not None:
                    clauses.append((head, "".join(buf)))
                head = upper
                buf = []
                i = j
                continue
            buf.append(text[i:j])
            i = j
            continue
        buf.append(ch)
        i += 1
    if head is not None:
        clauses.append((head, "".join(buf)))
    return clauses


def _stands_alone(text: str, i: int) -> bool:
    """Whether the word starting at ``i`` begins where a word ended, rather than inside one."""
    return i == 0 or not _ENDS_WORD.search(text[:i])


def clause_heads(text: str) -> List[str]:
    """
    The heads of a statement's clauses, upper-cased, in order.

    Empty for text that has no clause in it, and for text that cannot be scanned at all
    — an unterminated quote is a syntax error for ``translate`` to name, not for a
    caller that is only asking whether this looks like a read query.
    """
    try:
        return [head for head, _ in split_clauses(strip_comments(text))]
    except TranslateError:
        return []


def refusal_phrase(head: str) -> Optional[str]:
    """
    The words a refusal should use for a clause head, or None if this one is not refused.

    ``OPTIONAL`` on its own tells a person less than ``OPTIONAL MATCH`` does, and the
    phrase is already in the table ``translate`` refuses with, so the guard that runs
    before it reads the same one rather than keeping a second spelling.
    """
    return _REFUSED_HEADS.get((head or "").upper())
