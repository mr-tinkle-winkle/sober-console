import os
import subprocess
import sys

from sober_console import cli, report, watcher
from sober_console.watcher import RESPAWN_DELAY, Watcher, ui_command


class FakeChild:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class Harness:
    def __init__(self):
        self.up = False
        self.now = 0.0
        self.spawned = []
        self.w = Watcher(running=lambda: self.up, spawn=self._spawn, clock=lambda: self.now)

    def _spawn(self):
        c = FakeChild()
        self.spawned.append(c)
        return c

    def step(self, advance=0.0):
        self.now += advance
        self.w.step()


def test_nothing_runs_while_sober_is_closed():
    h = Harness()
    for _ in range(5):
        h.step(2)
    assert h.spawned == []


def test_ui_starts_when_sober_opens_and_stops_when_it_closes():
    h = Harness()
    h.up = True
    h.step()
    h.step(2)
    h.step(2)
    assert len(h.spawned) == 1                       # started once, not once per poll
    h.up = False
    h.step(2)
    assert h.spawned[0].terminated and h.w.child is None


def test_each_sober_session_gets_a_fresh_ui():
    h = Harness()
    h.up = True
    h.step()
    h.up = False
    h.step(2)
    h.up = True
    h.step(2)
    assert len(h.spawned) == 2


def test_crash_while_sober_runs_restarts_after_a_delay():
    h = Harness()
    h.up = True
    h.step()
    h.spawned[0].returncode = 1                      # crashed
    h.step(1)
    assert len(h.spawned) == 1                       # too soon
    h.step(RESPAWN_DELAY)
    assert len(h.spawned) == 2


def test_clean_exit_is_not_restarted_until_sober_restarts():
    h = Harness()
    h.up = True
    h.step()
    h.spawned[0].returncode = 0                      # e.g. `sober-console quit`
    for _ in range(4):
        h.step(RESPAWN_DELAY)
    assert len(h.spawned) == 1
    h.up = False
    h.step(2)
    h.up = True
    h.step(2)
    assert len(h.spawned) == 2


def test_ui_command_prefers_the_wrapper_path(monkeypatch):
    monkeypatch.setenv("SOBER_CONSOLE_BIN", "/nix/store/x/bin/sober-console")
    assert ui_command() == ["/nix/store/x/bin/sober-console", "run"]
    monkeypatch.delenv("SOBER_CONSOLE_BIN")
    assert ui_command()[1:] == ["-m", "sober_console", "run"]


# ---- inspect report ---------------------------------------------------------------------------
LOG = "\n".join([
    "info: app: lifecycle: app_start delta=0.1ms",
    "2024-05-01T12:00:00.100Z,1.0,0001,6 [FLog::Output] hello",
    "2024-05-01T12:00:00.200Z,1.0,0001,6 [FLog::Network] tick",
    "2024-05-01T12:00:00.300Z,1.0,0001,6 [FLog::Error] boom",
    "    Stack Begin",
    "something unrecognisable",
]) + "\n"


def test_report_summarises_levels_tags_and_unparsed_share(tmp_path):
    (tmp_path / "latest.log").write_text(LOG)
    text = report.build_report(None, tmp_path, sample=2)
    assert "latest.log" in text and "Lines parsed  : 6" in text
    assert "error=2" in text and "output=1" in text         # the stack line inherits "error"
    assert "FLog::Network" in text and "FLog::Output" in text
    assert "[error] FLog::Error | Stack Begin" in text          # a sampled continuation line inherits its parent
    assert "[info] - | something unrecognisable" in text


def test_report_explains_a_missing_log_dir(tmp_path):
    assert "No log file found" in report.build_report(None, tmp_path / "nope")


def test_inspect_command_runs_end_to_end(tmp_path, monkeypatch, capsys):
    (tmp_path / "x.log").write_text(LOG)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert cli.main(["inspect", "--file", str(tmp_path / "x.log")]) == 0
    assert "Top source tags" in capsys.readouterr().out


def test_watcher_lock_reports_whether_a_watcher_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert not watcher.is_running()
    held = watcher.acquire_lock()
    assert held is not None and watcher.is_running()
    assert watcher.acquire_lock() is None                    # a second watcher can't start
    held.close()
    assert not watcher.is_running()


def _explain(monkeypatch, tmp_path, sober_up, watcher_up):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr("sober_console.sober.running", lambda *a: sober_up)
    monkeypatch.setattr("sober_console.watcher.is_running", lambda: watcher_up)
    return cli._explain_no_overlay()


def test_no_overlay_message_says_which_part_is_missing(monkeypatch, tmp_path):
    # the case from real use: Sober is open, but the watcher had been stopped with Ctrl-C
    msg = _explain(monkeypatch, tmp_path, sober_up=True, watcher_up=False)
    assert "nothing is watching" in msg and "sober-console watch" in msg
    assert "will start it once Sober opens" in _explain(monkeypatch, tmp_path, False, True)
    assert "hasn't started yet" in _explain(monkeypatch, tmp_path, True, True)
    both_down = _explain(monkeypatch, tmp_path, False, False)
    assert "Sober is closed" in both_down and "watcher isn't running" in both_down


def test_control_commands_report_when_nothing_is_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr("sober_console.sober.running", lambda *a: True)
    monkeypatch.setattr("sober_console.watcher.is_running", lambda: False)
    assert cli.main(["toggle"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("sober-console: ") and "nothing is watching" in err


def test_config_path_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["config", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "sober-console" / "config.toml")


def test_cli_works_as_a_module_and_stays_qt_free_for_control_commands(tmp_path):
    env = {**os.environ, "XDG_RUNTIME_DIR": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)}
    code = ("import sys; from sober_console import cli; cli.main(['status']); "
            "assert not any(m.startswith('PySide6') for m in sys.modules), 'Qt was imported'")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
