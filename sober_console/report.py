"""
``sober-console inspect``: shows how a real Sober log is being parsed, so the parser and the
noise filters can be tuned against it (paste the output back if something looks wrong).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .logparse import LEVELS, parse_line
from .tailer import LogTailer


def build_report(path: "Path | None", log_dir: Path, sample: int = 8, max_lines: int = 4000) -> str:
    out = []
    if path is None:
        t = LogTailer(log_dir)
        res = t.poll()
        path = res.path
        if path is None:
            return f"No log file found in {log_dir}\n(is Sober installed as a Flatpak? set `log_dir` in the config)"
    out.append(f"Log directory : {path.parent}")
    out.append(f"Log file      : {path.name}  ({path.stat().st_size:,} bytes)")

    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    raw = [r for r in raw if r.strip()]
    parsed, prev = [], None
    for r in raw:
        prev = parse_line(r, prev)
        parsed.append(prev)
    n = len(parsed)
    out.append(f"Lines parsed  : {n:,} (the last {max_lines:,} at most)")
    if not n:
        return "\n".join(out + ["(the file is empty)"])

    levels = Counter(p.level for p in parsed)
    out.append("By level      : " + ", ".join(f"{lv}={levels.get(lv, 0)}" for lv in LEVELS))
    untagged = sum(1 for p in parsed if not p.tag)
    untimed = sum(1 for p in parsed if not p.time)
    out.append(f"No source tag : {untagged:,} ({100 * untagged // n}%)    No timestamp: {untimed:,} ({100 * untimed // n}%)")
    out.append("")
    out.append("Top source tags:")
    for tag, c in Counter(p.tag for p in parsed if p.tag).most_common(15):
        out.append(f"  {c:6,}  {tag}")
    out.append("")
    out.append("Raw line  ->  [level] tag | message")
    for group, label in ((parsed[:sample], "first"), (parsed[-sample:], "last")):
        out.append(f"-- {label} {len(group)} --")
        for p in group:
            out.append(f"{p.raw[:150]}\n    -> [{p.level}] {p.tag or '-'} | {p.message[:100]}")
    return "\n".join(out)
