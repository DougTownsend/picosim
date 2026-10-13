"""
Program settings: values a student can change from the GUI without editing
the program.

A program declares a setting with an ``.equ`` line whose comment starts with
``<-``; the rest of the comment is the help text shown next to it::

    .equ    LED_PIN,        15      @ <- the GPIO number of your LED
    .equ    BUTTON_TO_GND,  1       @ <- 1: button connects the pin to GND; 0: button connects the pin to 3V3

Help text made of ``value: meaning`` pairs separated by ``;`` becomes a
choice list in the GUI; anything else is a number box.

The values a student picks are stored per program in the user's data folder
(never in the program file).  ``effective_source()`` returns the file to
assemble: the program itself when nothing is changed, otherwise a copy with
the chosen values filled in, kept in the user's cache folder.
"""

import hashlib
import json
import os
import re

_SETTING = re.compile(r'^(\s*\.equ\s+)(\w+)(\s*,\s*)([^@\n]*?)(\s*@\s*<-\s*)(.*)$', re.M)
_CHOICE = re.compile(r'^\s*(-?(?:0x[0-9a-fA-F]+|\d+))\s*:\s*(.+?)\s*$')


def _user_dir(kind):
    root = os.environ.get('PICOSIM_SETTINGS_DIR')    # tests keep their own copy
    if root:
        return os.path.join(root, kind)
    try:
        from platformdirs import user_cache_dir, user_data_dir
        return (user_cache_dir if kind == 'cache' else user_data_dir)("picosim")
    except ImportError:
        return os.path.expanduser("~/.cache/picosim" if kind == 'cache' else "~/.local/share/picosim")


def _store_file():
    return os.path.join(_user_dir('data'), 'program_settings.json')


def _load_store():
    try:
        with open(_store_file(), encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_store(data):
    path = _store_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _choices(help_text):
    """[(value, meaning), ...] when the help text is 'v: meaning; v: meaning'."""
    parts = [p for p in help_text.split(';') if p.strip()]
    out = []
    for p in parts:
        m = _CHOICE.match(p)
        if not m:
            return []
        out.append((m.group(1), m.group(2)))
    return out if len(out) >= 2 else []


def declared(path):
    """The settings declared in the program at *path*, in file order:
    [{name, default, help, choices}].  Empty for files without any."""
    if not path.endswith('.s'):
        return []
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            src = f.read()
    except OSError:
        return []
    out = []
    for m in _SETTING.finditer(src):
        help_text = m.group(6).strip()
        out.append({'name': m.group(2), 'default': m.group(4).strip(), 'help': help_text,
                    'choices': [{'value': v, 'label': l} for v, l in _choices(help_text)]})
    return out


def chosen(path):
    """{name: value} the student has changed for *path* (only declared names)."""
    names = {s['name'] for s in declared(path)}
    saved = _load_store().get(os.path.abspath(path), {})
    return {k: v for k, v in saved.items() if k in names}


def describe(path):
    """declared() plus each setting's current 'value'."""
    ch = chosen(path)
    return [dict(s, value=ch.get(s['name'], s['default'])) for s in declared(path)]


def _number(text):
    text = str(text).strip()
    try:
        int(text, 0)
    except ValueError:
        return None
    return text


def set_values(path, values):
    """Store the student's *values* {name: text} for *path*.  A value equal to
    the program's own default is forgotten.  Raises ValueError for a value
    that is not a whole number, or not one of a setting's choices."""
    path = os.path.abspath(path)
    decl = {s['name']: s for s in declared(path)}
    store = _load_store()
    mine = dict(store.get(path, {}))
    for name, text in values.items():
        if name not in decl:
            continue
        s = decl[name]
        v = _number(text)
        if v is None:
            raise ValueError(f"{name} must be a whole number (like 15 or 0x0F), not '{text}'")
        if s['choices'] and int(v, 0) not in [int(c['value'], 0) for c in s['choices']]:
            allowed = ', '.join(c['value'] for c in s['choices'])
            raise ValueError(f"{name} must be one of {allowed}")
        if int(v, 0) == _int_or_none(s['default']):
            mine.pop(name, None)
        else:
            mine[name] = v
    if mine:
        store[path] = mine
    else:
        store.pop(path, None)
    _save_store(store)


def _int_or_none(text):
    try:
        return int(text, 0)
    except ValueError:
        return None


def clear(path):
    """Forget every value the student changed for *path*."""
    store = _load_store()
    if store.pop(os.path.abspath(path), None) is not None:
        _save_store(store)


def effective_source(path):
    """The file to assemble for *path*: the program itself if no setting is
    changed, else a copy (same file name) with the chosen values filled in."""
    path = os.path.abspath(path)
    ch = chosen(path)
    if not ch:
        return path
    with open(path, encoding='utf-8', errors='replace') as f:
        src = f.read()

    def fill(m):
        name = m.group(2)
        if name not in ch:
            return m.group(0)
        return m.group(1) + name + m.group(3) + ch[name] + m.group(5) + m.group(6)

    src = _SETTING.sub(fill, src)
    tag = hashlib.sha1(path.encode()).hexdigest()[:12]
    out_dir = os.path.join(_user_dir('cache'), 'with_settings', tag)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, os.path.basename(path))
    old = None
    if os.path.exists(out):
        with open(out, encoding='utf-8', errors='replace') as f:
            old = f.read()
    if old != src:             # leave the mtime alone when nothing changed
        with open(out, 'w', encoding='utf-8') as f:
            f.write(src)
    return out
