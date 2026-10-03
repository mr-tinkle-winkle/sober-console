"""
Command line entry point.  Heavy imports (Qt) happen only inside the subcommands that need
them, so ``watch`` and the control commands stay light.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, ipc


def _explain_no_overlay() -> str:
    """The overlay process isn't running -- say which part of the chain is actually missing
    (this is NOT the same as "Sober isn't running")."""
    from . import sober, watcher

    sober_up, watcher_up = sober.running(), watcher.is_running()
    if watcher_up and sober_up:
        return ("Sober is running but the overlay hasn't started yet (the watcher checks every 2s). "
                "If this persists, see: journalctl --user -u sober-console")
    if watcher_up:
        return "the overlay isn't running because Sober isn't; the watcher will start it once Sober opens."
    if sober_up:
        return ("Sober is running, but nothing is watching for it, so the overlay never started. "
                "Start the watcher with `sober-console watch` (or `systemctl --user start sober-console`).")
    return ("the overlay isn't running: Sober is closed and the watcher isn't running either. "
            "Start the watcher with `sober-console watch`, or enable services.sober-console.")


def _cmd_control(command: str) -> int:
    reply = ipc.send_command(command)
    if reply is None:
        print(f"sober-console: {_explain_no_overlay()}", file=sys.stderr)
        return 1
    print(reply)
    return 0


def _cmd_inspect(args) -> int:
    from . import config, report, sober

    cfg = config.load()
    log_dir = Path(cfg.log_dir).expanduser() if cfg.log_dir else sober.default_log_dir()
    path = Path(args.file).expanduser() if args.file else None
    print(report.build_report(path, log_dir, sample=args.sample))
    return 0


def _cmd_focus(_args) -> int:
    from . import config, focus, sober

    cfg = config.load()
    print(f"Sober running : {sober.running()}   pids: {sorted(sober.pids())}")
    print(f"kdotool       : {'found' if focus.kdotool_available() else 'NOT FOUND'}")
    info = focus.query_active()
    if info is None:
        print("active window : (could not query -- is this a KDE Wayland/X11 session with kdotool?)")
        return 1
    print(f"active window : class={info.wclass!r} name={info.name!r} pid={info.pid} geometry={info.geometry}")
    print(f"counts as Sober's window: {focus.is_sober_window(info, sober.pids(), cfg.window_match)}"
          f"   (window_match = {cfg.window_match!r})")
    return 0


def _cmd_config(args) -> int:
    from . import config

    if args.what == "path":
        print(config.config_path())
    else:
        import tomli_w
        from dataclasses import asdict

        print(tomli_w.dumps(asdict(config.load())))
    return 0


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(prog="sober-console", description="An F9-style log overlay for Sober (Roblox on Linux).")
    p.add_argument("--version", action="version", version=f"sober-console {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("watch", help="start/stop the overlay automatically as Sober opens/closes (what the service runs; default)")
    r = sub.add_parser("run", help="run the overlay process directly")
    r.add_argument("--standalone", action="store_true", help="do not exit when Sober is not running (for trying it out)")
    for name, text in (("toggle", "show or hide the overlay"), ("show", "show the overlay"), ("hide", "hide the overlay"),
                       ("status", "print the overlay's state"), ("reload", "re-read the config file"),
                       ("quit", "stop the overlay until Sober is restarted")):
        sub.add_parser(name, help=text)
    i = sub.add_parser("inspect", help="show how your Sober log is being parsed")
    i.add_argument("--file", help="inspect this file instead of the newest Sober log")
    i.add_argument("--sample", type=int, default=8, help="how many first/last lines to show (default 8)")
    sub.add_parser("focus", help="debug: show what the focused-window detection sees")
    c = sub.add_parser("config", help="print the config path or the effective settings")
    c.add_argument("what", choices=("path", "show"), nargs="?", default="path")

    args = p.parse_args(argv)
    cmd = args.cmd or "watch"

    if cmd == "watch":
        from . import watcher
        return watcher.watch()
    if cmd == "run":
        from . import app
        return app.run(standalone=getattr(args, "standalone", False))
    if cmd in ipc.COMMANDS:
        return _cmd_control(cmd)
    if cmd == "inspect":
        return _cmd_inspect(args)
    if cmd == "focus":
        return _cmd_focus(args)
    if cmd == "config":
        return _cmd_config(args)
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
