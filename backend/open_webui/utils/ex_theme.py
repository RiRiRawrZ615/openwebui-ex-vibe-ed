"""Theme engine core (pure).

No theme system exists in this fork. Measured: custom_css and customCSS are 0 hits in the
tree, and customStyle has 2 hits, both inside the vendored swagger-ui-bundle.js. So this
is built, not extended.

What the engine does is derive tokens from a seed, not paste colors in blindly:
  - a seed accent and a surface produce a ramp the components already read,
  - every pairing that carries text is checked for contrast, and a pairing that fails is
    reported as a failure rather than shipped,
  - gradients are stop lists, validated as hex values, so a typo cannot become a broken
    declaration in the page,
  - a wallpaper is a path or a URL, and a path outside the allowed roots is refused, the
    same rule the model check uses.

Contrast is measured, using WCAG relative luminance, so the number is not a taste call.
The ratio threshold is stated as a choice (WCAG AA for normal text is 4.5:1), which is a
standard, not an assumption of mine.

Branding, stated precisely: LICENSE clause 4 prohibits altering or replacing Open WebUI
branding, with an exception for deployments under fifty users in a rolling thirty days.
A personal fork is inside that exception, so swapping the icon here is permitted for this
use. That is a fact about the license text, not a guess about enforcement. The engine still
writes fork icons to a fork-owned path and never overwrites static/favicon.png, so a
multi-user deployment stays clean without needing a judgement call.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")

# WCAG AA for normal text is 4.5:1. Kept as a named constant so a failure can say which
# standard it was measured against.
MIN_CONTRAST_AA = 4.5
MIN_CONTRAST_LARGE = 3.0

# Fork-owned asset dir. Upstream branding assets stay where upstream put them.
FORK_ICON_DIR = "static/ex_assets"
UPSTREAM_BRANDING = ("static/favicon.png", "static/splash.png", "static/splash-dark.png")

RAMP_STOPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)

BUILTIN_THEMES = ("system", "light", "oled-dark", "her")

# Named presets. A preset is data, not a branch: the picker sends a seed and a gradient, and
# build_theme measures the result like any other theme. Nothing here can produce a palette
# that skipped the contrast check.
#
# Each entry is (name, accent, surface, gradient stops, one-line intent). The intent string is
# what the picker shows, so the list is self-describing rather than a row of bare hex codes.
THEME_PRESETS = (
    ("navy", "#24406b", "#12203a", ("#0b1729", "#101d2e", "#17293d"),
     "deep navy, the default"),
    ("midnight", "#1b2c4d", "#0d1526", ("#070f1c", "#101c33", "#1b2c4d"),
     "near-black blue for dark rooms"),
    ("slate", "#2f4a6b", "#1a2536", ("#111a27", "#1c2a3d", "#33506f"),
     "desaturated blue-grey, lowest chroma"),
    ("ocean", "#1e4f6b", "#0f2431", ("#071720", "#12313f", "#1e5a78"),
     "teal-leaning navy"),
    ("indigo", "#3a3a7a", "#1a1a3d", ("#0e0e24", "#1d1d47", "#3f3f8a"),
     "violet-leaning navy"),
)

# Default theme as RiRi asked for it: navy, not cobalt. She called cobalt too bright, so
# the seed is the darker navy and the gradient runs to a lighter blue.
DEFAULT_THEME_SEED = "#24406b"
DEFAULT_SURFACE = "#12203a"
DEFAULT_TEXT = "#e6ecf5"
# The one canonical default gradient. Four places describe it and they had drifted apart:
# this constant, ex_settings.DEFAULTS['themeGradient'], src/ex_theme.css and the TS mirror
# in src/lib/utils/exSettings.ts. Because this engine applies the gradient at runtime from
# Python, this value is the one the user actually sees, so it is the value the others must
# match.
#
# History: the far stop used to be '#3a6ba5', a saturated cobalt, and it was the brightest
# thing on screen. Pulled right down to a desaturated slate so the gradient reads as a navy
# wash whose far corner is lit relative to the background, without shouting. That is the
# invariant the theme tests now assert: the far stop is lighter than the near stop.
DEFAULT_GRADIENT = ("#0a1421", "#101d2e", "#17293d")
ADJUST_CEILING = 60  # slider range, in lightness percent
ADJUST_DEFAULT = 0


def normalize_hex(value: str) -> str | None:
    """Normalize to #rrggbb, or None when the value is not a plain hex color."""
    if not value:
        return None
    match = HEX.match(value.strip())
    if not match:
        return None
    return "#" + match.group(1).lower()


def to_rgb(hex_value: str) -> tuple[int, int, int] | None:
    norm = normalize_hex(hex_value)
    if not norm:
        return None
    r = norm[1:3]
    g = norm[3:5]
    b = norm[5:7]
    return int(r, 16), int(g, 16), int(b, 16)


def luminance(hex_value: str) -> float | None:
    """WCAG relative luminance. Returns None for a value that is not a hex color."""
    rgb = to_rgb(hex_value)
    if not rgb:
        return None
    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> float | None:
    a = luminance(foreground)
    b = luminance(background)
    if a is None or b is None:
        return None
    lighter, darker = max(a, b), min(a, b)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def adjust(hex_value: str, delta: float) -> str | None:
    """Lightness slider. Positive lightens, negative darkens, clamped to the range.

    Clamped at both ends so a slider pushed to the ceiling cannot produce an out-of-gamut
    value or a color that reads as pure black or pure white, which is what makes a theme
    unreadable rather than just darker.
    """
    rgb = to_rgb(hex_value)
    if not rgb:
        return None
    delta = max(-float(ADJUST_CEILING), min(float(ADJUST_CEILING), float(delta)))
    amount = delta / 100.0
    if amount >= 0:
        out = tuple(int(v + (255 - v) * amount) for v in rgb)
    else:
        out = tuple(int(v * (1 + amount)) for v in rgb)
    return "#{:02x}{:02x}{:02x}".format(*out)


def mix(hex_value: str, target: str, amount: float) -> str | None:
    """Blend toward a target, used to build a ramp from one seed."""
    a = to_rgb(hex_value)
    b = to_rgb(target)
    if not a or not b:
        return None
    amount = max(0.0, min(1.0, float(amount)))
    out = tuple(round(x + (y - x) * amount) for x, y in zip(a, b))
    return "#{:02x}{:02x}{:02x}".format(*out)


def ramp(seed: str, dark: bool) -> dict:
    """A ramp from one seed color, on the scale the components already read.

    For a dark theme the ramp runs toward near-black; for a light theme toward near-white.
    Nothing here decides which stop a component uses, so the ramp is a table, not a claim
    about what a component will look like.
    """
    seed = normalize_hex(seed)
    if not seed:
        return {"seed": None, "stops": {}, "dark": dark}
    target = "#0b0b0b" if dark else "#fafafa"
    stops = {}
    for stop in RAMP_STOPS:
        # In a dark theme the low numbers stay near the seed and the high numbers go dark;
        # in a light theme that is mirrored. Amount is derived from the stop number only.
        amount = stop / 1000 if dark else 1 - stop / 1000
        stops[stop] = mix(seed, target, amount)
    return {"seed": seed, "stops": stops, "dark": dark}


# Scale factors for the four tokens the app actually paints surfaces with. These exist
# because ramp() is the wrong tool for them: mixing toward #0b0b0b desaturates the hue as it
# darkens, so a navy seed reaches the dark end as neutral charcoal. That is precisely the
# failure this fork is fixing, and reusing the general ramp would reintroduce it.
#
# Scaling every channel by the same factor preserves hue exactly (each channel ratio is
# unchanged), so the result is the same color at a different brightness rather than a
# different color that happens to be darker. No clamping is reached for any sane accent.
APP_RAMP_FACTORS = ((800, 1.0), (850, 0.82), (900, 0.62), (950, 0.45))


def app_ramp(accent: str) -> dict:
    """The four surface tokens the app resolves `dark:bg-gray-*` from.

    This is the lever. Measured in this tree: every panel is `dark:bg-gray-900`, which
    compiles to `background-color: var(--color-gray-900)`. Retinting these four values
    retints the sidebar, the modals, the message bubbles and the settings panels at once,
    without naming a single component.

    Returned as #rrggbb because the frontend writes them with style.setProperty, which the
    browser must be able to parse from the string alone.
    """
    normalized = normalize_hex(accent)
    if not normalized:
        return {}
    rgb = to_rgb(normalized)
    if not rgb:
        return {}
    out = {}
    for stop, factor in APP_RAMP_FACTORS:
        scaled = tuple(min(255, max(0, round(channel * factor))) for channel in rgb)
        out[stop] = "#{:02x}{:02x}{:02x}".format(*scaled)
    return out


def gradient(stops: list[str], angle_deg: int = 135) -> dict:
    """Validate a gradient. A stop that is not a plain hex color is refused, not passed
    through, so a typo cannot land in the page as a broken declaration.
    """
    clean = [normalize_hex(s) for s in stops]
    bad = [original for original, value in zip(stops, clean) if value is None]
    valid = [value for value in clean if value]
    angle = int(angle_deg)
    if not 0 <= angle <= 360:
        angle = 135
    return {
        "stops": valid,
        "refused": bad,
        "angle": angle,
        "css": f"linear-gradient({angle}deg, {', '.join(valid)})" if len(valid) >= 2 else "",
    }


def wallpaper(value: str, roots: list[Path] | tuple[Path, ...]) -> dict:
    """A wallpaper is a URL or a file path. Paths outside the allowed roots are refused.

    data: URLs are refused on purpose. A data URL is a way of smuggling a file that was
    never in a root, and the module's rule is that nothing outside a root is accepted.
    """
    value = (value or "").strip()
    if not value:
        return {"value": "", "kind": "none", "accepted": False, "reason": "no wallpaper given"}

    if value.startswith("data:"):
        return {"value": value, "kind": "data", "accepted": False, "reason": "data URLs are refused"}

    if value.startswith(("http://", "https://")):
        if not value.startswith("https://"):
            return {"value": value, "kind": "url", "accepted": False, "reason": "plain http is refused"}
        return {"value": value, "kind": "url", "accepted": True, "reason": "https url"}

    try:
        resolved = Path(value).expanduser().resolve()
    except (OSError, ValueError):
        return {"value": value, "kind": "path", "accepted": False, "reason": "path does not resolve"}

    if not roots:
        return {"value": str(resolved), "kind": "path", "accepted": False, "reason": "no roots configured"}

    for root in roots:
        try:
            r = Path(root).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if resolved == r or r in resolved.parents or str(resolved).startswith(str(r) + os.sep):
            return {"value": str(resolved), "kind": "path", "accepted": True, "reason": "inside allowed root"}

    return {"value": str(resolved), "kind": "path", "accepted": False, "reason": "path is outside the allowed roots"}


def icon_swap(icon_path: str) -> dict:
    """Report where a fork icon goes, and refuse to overwrite an upstream branding asset.

    License clause 4 allows altering the Open WebUI branding for deployments under fifty
    users in a rolling thirty days, which covers this personal fork. Even so, the engine
    writes to the fork asset dir and never overwrites upstream assets, so the same code is
    correct in a deployment where that exception would not apply.
    """
    value = (icon_path or "").strip()
    if not value:
        return {"requested": "", "accepted": False, "reason": "no icon given"}

    normalized = value.replace("\\", "/")
    if normalized in UPSTREAM_BRANDING or (normalized.startswith("static/") and "ex_assets" not in normalized):
        return {
            "requested": normalized,
            "accepted": False,
            "reason": "upstream branding asset, not overwritten by this engine",
            "suggested": f"{FORK_ICON_DIR}/{Path(normalized).name}",
        }

    if "ex_assets" not in normalized:
        return {"requested": normalized, "accepted": False, "reason": f"fork icons belong under {FORK_ICON_DIR}"}

    return {"requested": normalized, "accepted": True, "path": normalized, "reason": "fork asset path"}


def build_theme(
    name: str,
    accent: str,
    surface: str,
    text: str,
    gradient_stops: list[str] | None = None,
    wallpaper_value: str = "",
    icon_path: str = "",
    roots: list[Path] | tuple[Path, ...] = (),
    adjust_by: float = ADJUST_DEFAULT,
) -> dict:
    """Compose a theme from seeds, and refuse to claim it is readable without measuring.

    Every text pairing that this theme defines is checked. A failing pairing is put in
    `failures`, and the theme is marked as not ready, because a theme that ships unreadable
    text is not a theme.
    """
    accent = adjust(accent, adjust_by) or normalize_hex(accent)
    dark = (luminance(surface) or 0) < 0.5
    name = (name or "").strip().lower() or "custom"
    grad = gradient(gradient_stops or [accent, surface])
    wall = wallpaper(wallpaper_value, roots)
    icon = icon_swap(icon_path)
    table = ramp(accent, dark)

    # Only pairings that carry text are checked against a threshold. A navy accent used as
    # a panel background against another panel background is decoration; measuring it as if
    # label text sat on it would fail a theme for the wrong reason. The pairing that matters
    # for a button is text on accent.
    pairings = [
        {"pair": "text on surface", "foreground": text, "background": surface, "ratio": contrast(text, surface), "carries_text": True},
        {"pair": "text on accent", "foreground": text, "background": accent, "ratio": contrast(text, accent), "carries_text": True},
        {"pair": "accent on surface", "foreground": accent, "background": surface, "ratio": contrast(accent, surface), "carries_text": False},
    ]
    for pair in pairings:
        if pair["ratio"] is None:
            pair["status"] = "unmeasurable"
            continue
        if not pair["carries_text"]:
            pair["status"] = "decorative"
            continue
        pair["threshold"] = MIN_CONTRAST_AA
        pair["status"] = "pass" if pair["ratio"] >= MIN_CONTRAST_AA else "fail"

    failures = [p for p in pairings if p["status"] == "fail"]
    unmeasurable = [p for p in pairings if p["status"] == "unmeasurable"]

    tokens = {
        "--owv-accent": normalize_hex(accent),
        "--owv-surface": normalize_hex(surface),
        "--owv-text": normalize_hex(text),
        "--owv-gradient": grad["css"],
    }

    return {
        "name": name,
        "mode": "dark" if dark else "light",
        "adjustment": float(adjust_by),
        "tokens": tokens,
        "app_ramp": app_ramp(accent),
        "ramp": table,
        "gradient": grad,
        "wallpaper": wall,
        "icon": icon,
        "pairings": pairings,
        "failures": failures,
        "unmeasurable": unmeasurable,
        "ready": not failures and not grad["refused"],
        "measured": bool(pairings) and not unmeasurable,
    }


def css_tokens(theme: dict) -> str:
    """Render the theme as CSS custom properties.

    Only tokens that measured or validated are emitted. A pairing that failed is not sent to
    the page as a color, because that is how an unreadable theme reaches the screen.

    This is enforced here, not just promised. A theme with a failing text pairing emits nothing,
    so a caller that renders css_tokens cannot accidentally ship an unreadable theme by skipping
    the ready check.
    """
    if theme.get("failures"):
        return ""

    lines = []
    for name, value in (theme.get("tokens") or {}).items():
        if value:
            lines.append(f"\t{name}: {value};")
    for stop, value in sorted((theme.get("ramp") or {}).get("stops", {}).items()):
        if value:
            lines.append(f"\t--owv-ramp-{stop}: {value};")
    wallpaper = theme.get("wallpaper") or {}
    if wallpaper.get("accepted") and wallpaper.get("value"):
        # url() is emitted only for an accepted value, so a refused path or a data URL cannot
        # appear in the page.
        lines.append(f"\t--owv-wallpaper: url({wallpaper['value']});")
    return "\n".join(lines)


def default_theme(roots: list[Path] | tuple[Path, ...] = (), adjust_by: float = ADJUST_DEFAULT) -> dict:
    """The shipped default: navy seed, gradient to a lighter blue, wallpaper off until the
    user picks one. Measured before it is called ready.
    """
    return build_theme(
        "navy",
        DEFAULT_THEME_SEED,
        DEFAULT_SURFACE,
        DEFAULT_TEXT,
        gradient_stops=list(DEFAULT_GRADIENT),
        roots=roots,
        adjust_by=adjust_by,
    )


__all__ = [
    "MIN_CONTRAST_AA",
    "MIN_CONTRAST_LARGE",
    "BUILTIN_THEMES",
    "RAMP_STOPS",
    "FORK_ICON_DIR",
    "UPSTREAM_BRANDING",
    "normalize_hex",
    "to_rgb",
    "adjust",
    "luminance",
    "contrast",
    "mix",
    "ramp",
    "gradient",
    "wallpaper",
    "icon_swap",
    "build_theme",
    "css_tokens",
    "default_theme",
    "DEFAULT_THEME_SEED",
    "DEFAULT_SURFACE",
    "DEFAULT_TEXT",
    "app_ramp",
    "THEME_PRESETS",
    "ADJUST_CEILING",
    "ADJUST_DEFAULT",
]
