"""
Filtering of parsed log lines (pure Python, no Qt).

A line is shown when ALL of these hold:

  * its level is enabled (Messages / Info / Warnings / Errors)
  * it is not in a muted noise group, muted source tag, or matched by an "always hide" pattern
  * "script only" mode (if on): it is script output, or a warning/error from any source
  * it satisfies the search box

Warnings and errors are never hidden by noise groups, muted tags or auto-muting -- only by
their own level checkbox, "always hide" patterns, or the search box.  So turning on a noisy
filter can't silently swallow a problem.

Search syntax:  ``foo bar``   both words must appear (case-insensitive)
                ``-foo``      the word must NOT appear
                ``"a phrase"`` quoted phrase
                ``/regex/``   regular expression
"""
from __future__ import annotations

import re
import shlex
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from .logparse import LEVELS, LogLine, is_output_tag
from .noise import GROUPS_BY_ID, NoiseGroup


@dataclass(frozen=True)
class Query:
    include: tuple = ()      # tuples of (kind, value): ("lit", str) or ("re", Pattern)
    exclude: tuple = ()

    def matches(self, haystack: str, lowered: str) -> bool:
        for kind, val in self.include:
            if kind == "lit":
                if val not in lowered:
                    return False
            elif not val.search(haystack):
                return False
        for kind, val in self.exclude:
            if kind == "lit":
                if val in lowered:
                    return False
            elif val.search(haystack):
                return False
        return True

    @property
    def empty(self) -> bool:
        return not self.include and not self.exclude


def _term(token: str):
    if len(token) >= 3 and token.startswith("/") and token.endswith("/"):
        try:
            return ("re", re.compile(token[1:-1], re.IGNORECASE))
        except re.error:
            pass
    return ("lit", token.lower())


@lru_cache(maxsize=64)
def compile_query(text: str) -> Query:
    text = (text or "").strip()
    if not text:
        return Query()
    try:
        lexer = shlex.shlex(text, posix=True)
        lexer.whitespace_split = True
        lexer.escape = ""                   # backslashes belong to /regex/ tokens, not shlex
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:                      # unbalanced quote while typing
        tokens = text.split()
    include, exclude = [], []
    for tok in tokens:
        if len(tok) > 1 and tok.startswith("-"):
            exclude.append(_term(tok[1:]))
        elif tok:
            include.append(_term(tok))
    return Query(tuple(include), tuple(exclude))


@lru_cache(maxsize=32)
def _compile_patterns(patterns: tuple) -> tuple:
    out = []
    for p in patterns:
        p = p.strip()
        if not p:
            continue
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            out.append(re.compile(re.escape(p), re.IGNORECASE))
    return tuple(out)


@lru_cache(maxsize=32)
def _compile_groups(group_ids: frozenset) -> tuple:
    pats = []
    for gid in sorted(group_ids):
        g: "NoiseGroup | None" = GROUPS_BY_ID.get(gid)
        if g:
            pats.extend(g.compiled())
    return tuple(pats)


@dataclass
class FilterState:
    levels: set = field(default_factory=lambda: set(LEVELS))
    query: str = ""
    muted_groups: set = field(default_factory=set)
    muted_tags: set = field(default_factory=set)
    exclude: list = field(default_factory=list)       # "always hide" patterns
    script_only: bool = False
    dedupe: bool = True
    auto_mute: bool = False
    auto_muted: frozenset = frozenset()              # computed from the buffer, see below

    def matches(self, ln: LogLine) -> bool:
        if ln.level not in self.levels:
            return False

        serious = ln.level in ("warning", "error")

        pats = _compile_patterns(tuple(self.exclude))
        if pats and any(p.search(ln.raw) for p in pats):
            return False

        if not serious:
            if ln.tag and ln.tag in self.muted_tags:
                return False
            if self.auto_mute and ln.tag and ln.tag in self.auto_muted:
                return False
            if self.muted_groups:
                for p in _compile_groups(frozenset(self.muted_groups)):
                    if p.search(ln.raw):
                        return False
            if self.script_only and not is_output_tag(ln.tag):
                return False

        q = compile_query(self.query)
        if not q.empty:
            lowered = ln.raw.lower()
            if not q.matches(ln.raw, lowered):
                return False
        return True


def from_config(cfg) -> FilterState:
    """Build a FilterState from a ``Config`` (duck-typed, so this module needn't import it)."""
    return FilterState(
        levels=set(cfg.levels),
        query=cfg.query,
        muted_groups=set(cfg.muted_groups),
        muted_tags=set(cfg.muted_tags),
        exclude=list(cfg.exclude),
        script_only=cfg.script_only,
        dedupe=cfg.dedupe,
        auto_mute=cfg.auto_mute_chatty,
    )


def chatty_tags(counts: Counter, total: int, *, min_total: int = 200, share: float = 0.20) -> frozenset:
    """Source tags responsible for more than ``share`` of the buffer, excluding script output.
    Used by "auto-mute chatty sources"."""
    if total < min_total:
        return frozenset()
    return frozenset(
        tag for tag, n in counts.items()
        if tag and n / total > share and not is_output_tag(tag)
    )
