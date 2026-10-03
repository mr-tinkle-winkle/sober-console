from collections import Counter
from datetime import datetime

from sober_console.filters import FilterState, chatty_tags, compile_query
from sober_console.logparse import parse_line
from sober_console.model import ConsoleModel


def L(text):
    return parse_line(text)


NET = "2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Network] replicator tick"
OUT = "2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Output] hello world"
ERR = "2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Error] bad thing"


# ---- search syntax -----------------------------------------------------------------
def test_query_and_semantics_and_exclusion():
    f = FilterState(query="hello -world")
    assert not f.matches(L(OUT))                  # has "world"
    assert FilterState(query="hello").matches(L(OUT))
    assert not FilterState(query="hello zzz").matches(L(OUT))


def test_query_quoted_phrase_and_regex():
    assert FilterState(query='"hello world"').matches(L(OUT))
    assert FilterState(query="/hel+o\\s+w/").matches(L(OUT))
    assert not FilterState(query="/^zzz/").matches(L(OUT))


def test_unbalanced_quote_while_typing_does_not_crash():
    assert compile_query('"hello wor') is not None
    FilterState(query='"hello wor').matches(L(OUT))


def test_bad_regex_falls_back_to_literal():
    f = FilterState(query="/[unclosed/")
    assert f.matches(L("text with /[unclosed/ in it"))
    assert not f.matches(L("text without it"))


def test_quoted_regex_with_spaces():
    assert FilterState(query='"/hello\\s+world/"').matches(L(OUT))


# ---- levels, muting, serious lines ---------------------------------------------------
def test_level_toggle():
    assert not FilterState(levels={"error"}).matches(L(OUT))
    assert FilterState(levels={"error"}).matches(L(ERR))


def test_muted_group_hides_noise_but_never_errors():
    f = FilterState(muted_groups={"network"})
    assert not f.matches(L(NET))
    assert f.matches(L(OUT))
    err_in_noise = L("2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Network] Fatal: connection lost")
    assert err_in_noise.level == "error"
    assert f.matches(err_in_noise)


def test_muted_tag_never_hides_warnings():
    f = FilterState(muted_tags={"FLog::Network"})
    assert not f.matches(L(NET))
    assert f.matches(L("2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Network] Warning: lag"))


def test_script_only_keeps_script_output_and_serious_lines():
    f = FilterState(script_only=True)
    assert f.matches(L(OUT))
    assert not f.matches(L(NET))
    assert f.matches(L(ERR))


def test_always_hide_patterns_apply_even_to_errors():
    f = FilterState(exclude=["bad thing"])
    assert not f.matches(L(ERR))


def test_invalid_exclude_pattern_is_treated_literally():
    f = FilterState(exclude=["[oops"])
    assert f.matches(L(OUT))


def test_lifecycle_group_catches_sober_startup_lines():
    f = FilterState(muted_groups={"lifecycle"})
    assert not f.matches(L("info: app: lifecycle: app_start delta=1ms"))


# ---- chatty detection -----------------------------------------------------------------
def test_chatty_tags_threshold_and_script_exemption():
    c = Counter({"FLog::Network": 300, "FLog::Output": 400, "FLog::Misc": 20})
    assert chatty_tags(c, 720) == frozenset({"FLog::Network"})
    assert chatty_tags(c, 100) == frozenset()          # too little data to judge


# ---- model -----------------------------------------------------------------------------
def test_model_ingest_ops_and_dedupe():
    m = ConsoleModel(FilterState(dedupe=True))
    ops = m.ingest([OUT, OUT, OUT, ERR])
    kinds = [k for k, _ in ops]
    assert kinds == ["append", "bump", "bump", "append"]
    assert [e.count for e in m.view] == [3, 1]


def test_model_dedupe_off():
    m = ConsoleModel(FilterState(dedupe=False))
    m.ingest([OUT, OUT])
    assert len(m.view) == 2


def test_model_hidden_counter_and_rebuild_after_filter_change():
    m = ConsoleModel(FilterState())
    m.ingest([OUT, NET, NET, ERR])
    assert (m.shown, m.hidden) == (4, 0)
    m.filters.muted_groups = {"network"}
    entries = m.rebuild()
    assert [e.line.tag for e in entries] == ["FLog::Output", "FLog::Error"]
    assert m.hidden == 2


def test_model_skips_blank_lines_and_stamps_live_lines_only():
    m = ConsoleModel()
    m.ingest(["", "   ", "plain text"], now=datetime(2024, 1, 1, 9, 8, 7, 654000))
    assert len(m.lines) == 1 and m.lines[0].time == "09:08:07.654"
    m2 = ConsoleModel()
    m2.ingest(["plain text"], backlog=True)
    assert m2.lines[0].time == ""


def test_model_ring_buffer_evicts_and_keeps_tag_counts_consistent():
    m = ConsoleModel(max_lines=5, display_max=5)
    m.ingest([f"info: net: line {i}" for i in range(12)])
    assert len(m.lines) == 5
    assert m.tag_counts["net"] == 5
    m.ingest([f"info: other: x{i}" for i in range(5)])
    assert "net" not in m.tag_counts and m.tag_counts["other"] == 5


def test_model_continuation_lines_follow_their_parent_level():
    m = ConsoleModel(FilterState(levels={"error"}))
    m.ingest(["error: Script 'A', Line 1 - x", "    at A.b", "info: unrelated"])
    assert [e.line.message for e in m.view] == ["Script 'A', Line 1 - x", "at A.b"]


def test_model_auto_mute_hides_chatty_info_but_not_errors():
    f = FilterState(auto_mute=True, dedupe=False)
    m = ConsoleModel(f)
    m.ingest([f"info: spam: tick {i}" for i in range(260)] + ["info: other: quiet", "error: spam: tick failure"])
    m.rebuild()
    assert "spam" in f.auto_muted
    msgs = [e.line.message for e in m.view]
    assert "quiet" in msgs and "tick failure" in msgs
    assert not any(x.startswith("tick 1") for x in msgs)


def test_visible_text_includes_repeat_count():
    m = ConsoleModel()
    m.ingest([OUT, OUT])
    assert "hello world  x2" in m.visible_text()
