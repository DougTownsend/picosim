"""
The teacher's settings for Ask picosim, in a small JSON file:

    {"ai": "off", "model": "claude-opus-5-5", "max_ai_per_hour": 20,
     "helpline": "", "log_unanswered": false}

ai               "on" lets questions the built-in answers cannot handle go to
                 Claude (needs the [ai] extra and an API key in the
                 environment); "off" (the default) keeps everything offline
model            the Claude model to ask
max_ai_per_hour  how many questions may go to Claude per hour
helpline         a crisis line the wellbeing reply names (e.g. "988 in the US")
log_unanswered   keep a local, redacted list of questions the bot could not
                 answer, for the teacher to add new phrasings

There is deliberately no setting for full answers: the bot only ever gives
hints, never a finished solution.

Where it is read from, first found wins: --teacher-config=path (picosim
passes it on as PICOSIM_TEACHER), PICOSIM_TEACHER, PICOSIM_CHAT_CONFIG,
then chat.json in picosim's user data folder.  A student who owns the
machine can edit it, so it is a speed bump, not security.
"""

import json
import os

from ..settings import _user_dir

DEFAULTS = {'ai': 'off', 'model': 'claude-opus-5-5', 'max_ai_per_hour': 20,
            'helpline': '', 'log_unanswered': False}


def path():
    for var in ('PICOSIM_TEACHER', 'PICOSIM_CHAT_CONFIG'):
        p = os.environ.get(var)
        if p:
            return os.path.abspath(os.path.expanduser(p))
    return os.path.join(_user_dir('data'), 'chat.json')


def load():
    """The settings, with defaults for anything missing or malformed."""
    cfg = dict(DEFAULTS)
    try:
        with open(path(), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return cfg
    if isinstance(data, dict):
        if str(data.get('ai', '')).lower() in ('on', 'off'):
            cfg['ai'] = str(data['ai']).lower()
        if isinstance(data.get('model'), str) and data['model'].strip():
            cfg['model'] = data['model'].strip()
        try:
            cfg['max_ai_per_hour'] = max(0, int(data.get('max_ai_per_hour', cfg['max_ai_per_hour'])))
        except (TypeError, ValueError):
            pass
        if isinstance(data.get('helpline'), str):
            cfg['helpline'] = data['helpline'].strip()
        cfg['log_unanswered'] = bool(data.get('log_unanswered', False))
    return cfg


def write_template():
    """Create the config file with the defaults if it does not exist; returns its path."""
    p = path()
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(DEFAULTS, f, indent=2)
            f.write('\n')
    return p


def data_file(name):
    """A file next to the config, for reports, feedback counts and the unanswered log."""
    return os.path.join(_user_dir('data'), name)
