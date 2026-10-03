"""
Finding Sober: is it running, which processes belong to it, where its log lives.

Sober is a Flatpak (org.vinegarhq.Sober).  Flatpak puts each running app in its own systemd
user scope, ``app-flatpak-org.vinegarhq.Sober-<n>.scope``, so "is Sober running" is a glob of
the cgroup tree -- no subprocess, cheap enough to poll every couple of seconds.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

APP_ID = "org.vinegarhq.Sober"


def scope_dirs(uid: "int | None" = None) -> "list[Path]":
    uid = os.getuid() if uid is None else uid
    pattern = (
        f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/"
        f"app.slice/app-flatpak-{APP_ID}-*.scope"
    )
    return [Path(p) for p in sorted(glob.glob(pattern))]


def running(uid: "int | None" = None) -> bool:
    return bool(scope_dirs(uid))


def pids(uid: "int | None" = None) -> "set[int]":
    out: "set[int]" = set()
    for d in scope_dirs(uid):
        try:
            out.update(int(x) for x in (d / "cgroup.procs").read_text().split())
        except (OSError, ValueError):
            continue
    return out


def default_log_dir() -> Path:
    return Path.home() / ".var" / "app" / APP_ID / "data" / "sober" / "sober_logs"
