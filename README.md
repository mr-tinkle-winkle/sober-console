# sober-console

An F9-style developer-console log overlay for [Sober](https://sober.vinegarhq.org/) (Roblox on
Linux). Sober runs the Android Roblox client, which has no F9 console, and `/console` does not
work in games with custom chat -- so this reads Sober's log file and shows it in an overlay that
looks like Roblox's developer console.

Structured to match the `afterglow` / `puppetry` / `conduit` flake pattern for inclusion in a
NixOS system flake.

## What it does

- **Only runs while Sober is open.** `sober-console watch` is a tiny Qt-free watcher (plain
  Python, no Qt loaded) that polls for Sober's systemd scope. While Sober runs it keeps the overlay process
  alive; when Sober closes, it stops it. Nothing Qt-related, no input reads and no log tailing
  exist while Sober is closed.
- **F9 toggles it, but only while Sober is focused.** The key is read passively from
  `/dev/input` via evdev (so Sober still sees F9 too), and a `kdotool` query checks that Sober's
  window is the focused one before toggling.
- **Overlays Sober.** It is a layer-shell `overlay` surface (the same mechanism as afterglow's
  clip indicator), so it sits above a fullscreen game. It is placed relative to Sober's window.
- **Drag it by the title bar.** The position is remembered (saved as anchor + offsets, see
  Placement).
- **Hides when Sober isn't the active window** -- but only if the overlay is on Sober's monitor;
  on another monitor it stays up. It comes back when Sober is focused again. Toggle in
  Settings (`hide_when_unfocused`).
- **Looks like the dev console:** dark panel, "Developer Console" header, colour-coded Messages /
  Info / Warnings / Errors, timestamps.
- **Takes the mouse while hovered.** Wheel scrolling and clicks over the overlay go to it, not
  the game. Scrolling up pauses auto-follow; "Jump to latest" resumes it.
- **Select and copy:** drag to select. `Copy` copies the selection (or everything shown if
  nothing is selected). Ctrl+C works too, and "copy on select" is optional.
- **Filters** (see below) and **placement options** including which monitor it opens on.

## Add it to your flake

Same shape as your other apps:

```nix
inputs = {
    # ...
    sober-console = {
        url = "github:mr-tinkle-winkle/sober-console";
        inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
};

# in nixosSystem's modules = [ ... ]:
inputs.sober-console.nixosModules.default
{
    services.sober-console = {
        enable = true;
        user = "mrtw";
    };
}
```

The module installs the package, adds `user` to the `input` group (needed to see the F9 key
press), and defines a per-user systemd service started with the graphical session.
Sober itself must be installed as the Flatpak (`org.vinegarhq.Sober`).

```
systemctl --user status sober-console       # the watcher
journalctl --user -u sober-console -f       # watcher + overlay logs
```

If the service never starts (it needs `graphical-session.target`, which Plasma reaches when
started via systemd), add `sober-console watch` to KDE's Autostart instead.

## Using it

Open Sober, focus it, press **F9**. Esc (while the overlay has keyboard focus), F9 or the `x`
button closes it.

Control commands, for a KDE shortcut or scripting (they don't load Qt, so they're instant):

```
sober-console toggle | show | hide | status | reload | quit
sober-console inspect        # how your real log is being parsed (see "Tuning")
sober-console focus          # what the focused-window detection sees
sober-console config path    # where the config file is
```

`toggle` works regardless of focus, so a KDE custom shortcut running `sober-console toggle` is
the fallback if F9 detection ever misbehaves.

**Don't bind the KDE shortcut to the same key as `toggle_key`** (F9 by default): the overlay
reads that key itself, so both would fire and the second one closes what the first opened.
Use a different key for the shortcut, or just use F9 without one.

### Tabs

- **Client Log** -- the log, with level checkboxes (Messages / Info / Warnings / Errors) and a
  search box.
- **Filters** -- noise filtering (below).
- **Settings** -- monitor, anchor, offsets, size, opacity, font size, toggle-key rebinding,
  keyboard behaviour. Everything is saved to the config file as you change it.

## Filters

A line is shown only if it passes all of these:

| Filter | What it does |
|---|---|
| Level checkboxes | Messages / Info / Warnings / Errors |
| Search box | `foo bar` (all words), `-foo` (exclude), `"a phrase"`, `/regex/` -- case-insensitive |
| Hide engine noise | Eight groups (Sober lifecycle, HTTP, networking, graphics, audio, asset loading, telemetry, Android chatter). **Best-guess patterns** -- see "Tuning" |
| Sources seen | Every source tag actually found in your log, with counts; untick to hide one. This adapts to your real log |
| Always hide | Your own list of texts/regexes, one per line |
| Script output only | Keeps only what scripts print (`FLog::Output`-style), plus warnings and errors from anywhere |
| Collapse repeats | Identical consecutive lines become one line with `x N` |
| Auto-mute chatty sources | Hides any one source that makes up more than 20% of the log |

Presets (Everything / Quiet / Script output only) set these in one click.

Two guarantees: **warnings and errors are never hidden by noise groups, muted sources or
auto-muting** (only by their own checkbox, "always hide", or the search box), and the footer always
shows `N shown / M hidden`, so filtering is never silent.

## Placement

Settings > Position. Everything is relative to a *reference area*: Sober's window when the overlay is
on Sober's monitor, otherwise the whole monitor.

- **Monitor:** `Same monitor as Sober` (default), `Other monitor`, `Primary monitor`, or a specific
  connector (`DP-1`, `HDMI-A-1`, ...) -- click the button to cycle through them.
- **Anchor:** top-left (default), top-right, bottom-left, bottom-right, center.
- **Offset X / Y** in pixels from that anchor, **Width / Height** as a % of the reference area.
- **Place relative to Sober's window** off = relative to the whole monitor instead.

**Dragging:** grab the title bar (anywhere that isn't a button) and move it. On release the
position is saved as the nearest corner of the reference area plus offsets from it, so the
Anchor / Offset steppers update to match and it lands in the same place next time (and keeps
hugging that corner if Sober's window is a different size). The size still comes from Width /
Height. In layer-shell mode the overlay is a transparent monitor-sized surface with only the
panel receiving input, so clicks outside the panel go straight to the game.

**Hide when Sober isn't the active window** (Settings, on by default): checked about once a
second with a single cheap `kdotool getactivewindow` call (a full probe only for a window not
seen before, and every few seconds for Sober's position). It hides only when it positively sees
another window active, only when the overlay is on Sober's monitor, and never while the overlay
itself has the keyboard. Hiding this way is automatic: it reappears when Sober is focused again,
whereas closing it yourself (F9, the x button, `sober-console hide`) stays closed until you open
it again. Turn the setting off to skip the polling entirely.

The overlay never leaves its monitor. Defaults (top-left, 16 px in, 46% x 55%) are a **guess** at
where Roblox puts its own console -- I couldn't confirm the real position -- so adjust to taste.

## Config

`~/.config/sober-console/config.toml`, created the first time you change a setting in the overlay
(it isn't rewritten otherwise, so comments in a hand-written file survive until then; after a
change in the overlay it is rewritten without them). Unknown keys are ignored, bad values fall back
to defaults, numbers are clamped. `sober-console reload` re-reads it.

| Key | Default | |
|---|---|---|
| `toggle_key` | `"KEY_F9"` | any evdev key name; also rebindable in Settings |
| `focus_check` | `"kdotool"` | `"off"`: toggle on the key regardless of the focused window |
| `window_match` | `"sober\|roblox"` | regex matched against the focused window's class / title |
| `keyboard` | `"on_demand"` | `"never"`: never take keyboard focus from Sober |
| `auto_copy` | `false` | copy selected text on mouse release |
| `monitor`, `anchor`, `offset_x`, `offset_y`, `width_pct`, `height_pct`, `follow_window` | see above | placement (`anchor` / offsets are what dragging writes) |
| `hide_when_unfocused` | `true` | hide while another window is active (only on Sober's monitor) |
| `opacity_pct`, `font_size`, `show_tags` | `94`, `10`, `false` | look |
| `log_dir` | `""` | empty = `~/.var/app/org.vinegarhq.Sober/data/sober/sober_logs` |
| `max_lines`, `display_max` | `20000`, `5000` | lines kept / lines drawn |
| `levels`, `query`, `dedupe`, `script_only`, `auto_mute_chatty`, `muted_groups`, `muted_tags`, `exclude` | | filter state |

### Keyboard focus trade-off

A layer-shell surface only gets keystrokes if it asks for them. With `keyboard = "on_demand"`
(default) clicking the overlay lets you type in the search box and press Ctrl+C -- but that takes
keyboard focus from Sober until you click back into the game. With `"never"` the game keeps focus
at all times; you can still select text and use the `Copy` button, but the search box can't be
typed in.

## Tuning

Two things in here are guesses until they've seen your actual log: the line parser and the noise
patterns. Run

```
sober-console inspect
```

while or after playing. It prints the log file in use, level and source-tag counts, the share of
lines with no tag or timestamp, and raw-line -> parsed-line samples. If lines are being
misread, or a noise group hides too much / too little, paste that output and the patterns in
`sober_console/logparse.py` and `sober_console/noise.py` can be fixed against real data.

`Settings > Show each line's source tag` prints the tag in front of every line, handy when
deciding what to mute.

## What is and isn't verified

Written and tested **without access to a Wayland/KDE session, a Sober install, a real Sober log,
input devices, or a working Nix build**. Honest breakdown:

**Tested (126 automated tests, plus rendered screenshots of every page):**
log parsing across the formats it knows, all filter logic, the ring buffer and de-duplication, log
tailing (rotation, truncation, partial lines, symlinks), placement/monitor maths, config
load/save/repair, the watcher's start/stop/respawn rules, the CLI and control socket (including a
real UI process driven through the CLI), the widgets and their wiring (offscreen), and the toggle
key's logic with fake devices.

**Not verified -- most likely places for a first-run surprise:**

- **The layer-shell overlay itself** (position over Sober, above fullscreen, receiving pointer
  input, keyboard on demand). The C++ shim uses the same calls as afterglow's working shim plus
  `KeyboardInteractivityOnDemand`, which I checked against KDE's `layer-shell-qt` header, but it has
  not been compiled or run.
- **`kdotool` output.** The window class/pid/geometry parsing assumes kdotool's documented output;
  whether `getwindowpid` exists depends on your kdotool version. Each field is optional, but if
  Sober's window isn't recognised F9 does nothing -- `sober-console focus` shows exactly what
  was seen. The overlay's footer also reports when the focused window can't be read at all.
- **Reading `/dev/input`.** Needs the `input` group (the module adds it; log out and in once).
- **The log format.** Parsing is tolerant (unrecognised lines still show as plain info lines), but
  the noise groups and "Script output only" assume Roblox-style `[FLog::...]` tags that may differ
  inside Sober's log. Hence `sober-console inspect`.
- **The Nix build and module.** `flake.nix` parses and mirrors afterglow's structure, but was never
  built or evaluated. The systemd unit needs `WAYLAND_DISPLAY` in the user environment, which
  Plasma should provide.
- **Pointer lock.** If a game locks the mouse pointer (first-person / shift-lock), no overlay can
  receive clicks or scrolls until the pointer is free again (e.g. open the Esc menu).
- **Dragging and click-through.** Dragging moves a widget inside a monitor-sized layer surface and
  updates the window mask. That the mask becomes the Wayland input region (so clicks outside the
  panel reach the game) follows how afterglow's indicator works but was only checked against
  Qt's API here, not on KWin. If clicks outside the panel *don't* reach the game while the overlay
  is open, that's the bug to report.
- **The KDE-shortcut flash.** Diagnosed from the code, not reproduced: with no known Sober
  position the first `toggle` placed the overlay on the wrong monitor, then moved it (unmap and
  map again) when the focus probe answered. It now asks for Sober's position first, and any later
  monitor change unmaps and re-maps after a short delay. If it still misbehaves,
  `journalctl --user -u sober-console -f` shows each show / hide with the reason.
- **Auto-hide.** The decision logic is tested with fake probe results; the real thing depends on
  `kdotool getactivewindow` reporting the right window as focus moves around, including when the
  overlay itself has taken keyboard focus.

## Development

```
nix develop
QT_QPA_PLATFORM=offscreen pytest -q          # unit + offscreen UI tests
python -m sober_console run --standalone     # run the overlay without Sober running
```

Layout: `logparse` -> `filters`/`noise` -> `model` (all Qt-free); `tailer`, `sober`, `focus`, `layout`,
`config`, `ipc`, `watcher` (Qt-free); `ui`, `app`, `hotkey`, `layershell` (Qt); `native/` is the
layer-shell shim.
