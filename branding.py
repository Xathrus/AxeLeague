"""Venue branding: logo upload and page color scheme.

Colors are stored in the settings table as brand_* keys and injected as CSS
variable overrides in base.html. Two shades are derived automatically so
admins only pick five colors: raised panels lighten the panel color, and
dim text blends the text color toward the background.
"""
import os
import re

import db as dbmod

# key -> (CSS variable, default, label shown in the form)
FIELDS = {
    "bg":    ("--bg",    "#1c1410", "Background"),
    "panel": ("--panel", "#2a201a", "Cards & panels"),
    "line":  ("--line",  "#4a3a2c", "Borders & lines"),
    "ink":   ("--ink",   "#f0e6d6", "Text"),
    "gold":  ("--gold",  "#d9a441", "Accent"),
}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_NAME = "Abilene Axe League"

# Preset themes selectable with one click on the Branding page
PRESETS = {
    "classic": {  # the default stained-wood look (clears overrides)
        "label": "Stained Wood (default)",
        "colors": None,
    },
    "rwb": {
        "label": "Red, White & Blue",
        "colors": {
            "bg":    "#141a26",   # deep navy-grey
            "panel": "#1f2a3d",   # steel blue
            "line":  "#41527a",
            "ink":   "#eef1f5",   # off-white
            "gold":  "#d94a4a",   # bold red accent
        },
    },
}
LOGO_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}
LOGO_DIR = os.path.join(os.path.dirname(dbmod.DB_PATH), "branding")


def valid_hex(v):
    return bool(HEX_RE.match(v or ""))


def _mix(a, b, t):
    """Blend hex color a toward b by t (0..1)."""
    av = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    bv = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(av, bv))


def get_settings(db):
    return {r["key"]: r["value"] for r in
            db.execute("SELECT key, value FROM settings").fetchall()}


def set_setting(db, key, value):
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
               " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))


def delete_setting(db, key):
    db.execute("DELETE FROM settings WHERE key=?", (key,))


def get_branding(db):
    """Everything the templates need: colors (with derived shades), whether
    anything is customized, and the logo file if one was uploaded."""
    s = get_settings(db)
    colors = {}
    custom = False
    for key, (var, default, label) in FIELDS.items():
        v = s.get("brand_" + key, default)
        if not valid_hex(v):
            v = default
        if v.lower() != default.lower():
            custom = True
        colors[key] = v
    css_vars = {FIELDS[k][0]: v for k, v in colors.items()}
    css_vars["--panel-2"] = _mix(colors["panel"], colors["ink"], 0.07)
    css_vars["--ink-dim"] = _mix(colors["ink"], colors["bg"], 0.32)
    logo = s.get("brand_logo")
    if logo and not os.path.exists(os.path.join(LOGO_DIR, logo)):
        logo = None
    name = (s.get("brand_name") or "").strip()
    return {
        "colors": colors,
        "css_vars": css_vars,
        "custom": custom,
        "logo": logo,
        "logo_version": s.get("brand_logo_version", "0"),
        "fields": FIELDS,
        "name": name or DEFAULT_NAME,
        "name_custom": bool(name),
        "presets": PRESETS,
    }


def save_logo(db, file_storage):
    """Validate and store the uploaded logo; returns (ok, error)."""
    name = (file_storage.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in LOGO_EXTS:
        return False, ("That file type isn't supported. Use one of: "
                       + ", ".join(sorted(LOGO_EXTS)) + ".")
    os.makedirs(LOGO_DIR, exist_ok=True)
    # remove any previous logo so old extensions don't linger
    remove_logo(db, commit=False)
    fname = "logo." + ext
    file_storage.save(os.path.join(LOGO_DIR, fname))
    set_setting(db, "brand_logo", fname)
    prev = get_settings(db).get("brand_logo_version", "0")
    set_setting(db, "brand_logo_version", str(int(prev) + 1))
    db.commit()
    return True, None


def remove_logo(db, commit=True):
    s = get_settings(db)
    old = s.get("brand_logo")
    if old:
        try:
            os.remove(os.path.join(LOGO_DIR, old))
        except OSError:
            pass
    delete_setting(db, "brand_logo")
    if commit:
        db.commit()
