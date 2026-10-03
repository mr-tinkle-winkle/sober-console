import os
import time

from sober_console.tailer import LogTailer


def write(path, text, mode="a"):
    with open(path, mode, encoding="utf-8") as fh:
        fh.write(text)


def test_first_attach_reads_backlog_then_only_new_lines(tmp_path):
    log = tmp_path / "latest.log"
    write(log, "one\ntwo\n", "w")
    t = LogTailer(tmp_path)
    r = t.poll()
    assert r.lines == ["one", "two"] and r.backlog and not r.reset
    assert t.poll().lines == []
    write(log, "three\n")
    r = t.poll()
    assert r.lines == ["three"] and not r.backlog


def test_partial_line_is_held_until_newline(tmp_path):
    log = tmp_path / "latest.log"
    write(log, "complete\npart", "w")
    t = LogTailer(tmp_path)
    assert t.poll().lines == ["complete"]
    write(log, "ial\n")
    assert t.poll().lines == ["partial"]


def test_large_backlog_is_trimmed_to_whole_lines(tmp_path):
    log = tmp_path / "latest.log"
    write(log, "".join(f"line number {i:04d}\n" for i in range(1000)), "w")
    t = LogTailer(tmp_path, backlog_bytes=200)
    lines = t.poll().lines
    assert 0 < len(lines) < 30
    assert all(l.startswith("line number ") and len(l) == 16 for l in lines)   # no torn first line
    assert lines[-1] == "line number 0999"


def test_new_session_file_triggers_reset_and_reads_from_start(tmp_path):
    old = tmp_path / "2024-01-01.log"
    write(old, "old session\n", "w")
    t = LogTailer(tmp_path, rescan_every=0)
    t.poll()
    new = tmp_path / "2024-01-02.log"
    write(new, "new session\n", "w")
    future = time.time() + 5
    os.utime(new, (future, future))
    r = t.poll()
    assert r.reset and r.lines == ["new session"] and not r.backlog


def test_truncation_in_place_resets(tmp_path):
    log = tmp_path / "latest.log"
    write(log, "aaaaaaaaaaaaaaa\nbbbbbbbbbbbbbbb\n", "w")
    t = LogTailer(tmp_path, rescan_every=1000)
    t.poll()
    write(log, "fresh\n", "w")
    r = t.poll()
    assert r.reset and r.lines == ["fresh"]


def test_missing_directory_is_quiet(tmp_path):
    t = LogTailer(tmp_path / "does-not-exist")
    r = t.poll()
    assert r.lines == [] and not r.reset


def test_crlf_and_bad_utf8_do_not_crash(tmp_path):
    log = tmp_path / "latest.log"
    log.write_bytes(b"windows line\r\nbad \xff byte\n")
    t = LogTailer(tmp_path)
    lines = t.poll().lines
    assert lines[0] == "windows line" and "byte" in lines[1]


def test_symlinked_latest_does_not_cause_spurious_reset(tmp_path):
    real = tmp_path / "2024-01-01.log"
    write(real, "a\n", "w")
    link = tmp_path / "latest.log"
    link.symlink_to(real)
    t = LogTailer(tmp_path, rescan_every=0)
    t.poll()
    write(real, "b\n")
    r = t.poll()
    assert not r.reset and r.lines == ["b"]
