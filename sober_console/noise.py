"""
Built-in "noise" groups: families of log lines that usually are not what you opened the
console for.  Each group can be hidden with one click on the Filters page.

These patterns are best guesses written without access to a real Sober log, so treat them as
a starting point.  The Filters page also lists every source tag it actually sees (with line
counts) and lets you mute those individually -- that adapts to your real log, these do not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NoiseGroup:
    id: str
    label: str
    patterns: tuple

    def compiled(self) -> "list[re.Pattern]":
        return [re.compile(p, re.IGNORECASE) for p in self.patterns]


NOISE_GROUPS: tuple = (
    NoiseGroup("lifecycle", "Sober startup / lifecycle", (
        r"\blifecycle\b",
        r"\bdelta=[\d.eE+\-]+ms\b",
    )),
    NoiseGroup("http", "HTTP requests & responses", (
        r"\bHttp(Response|Request)\b",
        r"FLog::Http",
        r"\bstatus:\s?\d{3}\b",
    )),
    NoiseGroup("network", "Networking / replication", (
        r"FLog::(Network|Replicator|Raknet|Streaming|Connection)",
        r"\braknet\b",
    )),
    NoiseGroup("graphics", "Graphics / rendering", (
        r"FLog::(Graphics|Render|Vulkan|OpenGL|Gfx|Gpu)",
        r"\b(vulkan|opengl|gles|egl|shader|swapchain|vsync|framebuffer)\b",
    )),
    NoiseGroup("audio", "Audio", (
        r"FLog::(Audio|Sound)",
        r"\b(openal|oboe|aaudio|audiotrack)\b",
    )),
    NoiseGroup("assets", "Asset / content loading", (
        r"FLog::(Asset|ContentProvider|DataModel)",
        r"\brbxasset(id)?://",
    )),
    NoiseGroup("telemetry", "Telemetry / analytics", (
        r"FLog::(Telemetry|Analytics|Event|Lms|Metrics)",
        r"\b(telemetry|analytics|ephemeralcounter)\b",
    )),
    NoiseGroup("android", "Android system chatter", (
        r"^[VDIWEF]/(chromium|libEGL|OpenGLRenderer|Choreographer|ViewRootImpl|InputDispatcher|AudioTrack|Adreno|gralloc)",
    )),
)

GROUPS_BY_ID = {g.id: g for g in NOISE_GROUPS}

# Presets: which groups they mute, and whether they restrict to script output.
PRESETS: tuple = (
    ("all", "Everything", (), False),
    ("quiet", "Quiet (hide engine noise)", tuple(g.id for g in NOISE_GROUPS), False),
    ("script", "Script output only", (), True),
)
