"""
The hardware test suite: the .s files in hwtests/ (next to examples/).

Each file starts with comment lines that say what it is:

    @ Title: Add, subtract and the flags
    @ About: what it checks, in a sentence or two
    @ Kind: trace          (step-by-step comparison) or bench (timing)
    @ Input: "abc\\n"       (optional: what getchar returns, in order)
"""

import os
import re

KEYS = ('Title', 'About', 'Kind', 'Input')


def suite_dir():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        'hwtests')


def header(path):
    """{'path', 'title', 'about', 'kind', 'input'} from a file's opening comments."""
    info = {'path': path, 'title': os.path.splitext(os.path.basename(path))[0], 'about': '',
            'kind': 'trace', 'input': ''}
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line.startswith('@'):
                    break
                for key in KEYS:
                    tag = f'@ {key}:'
                    if line.startswith(tag):
                        val = line[len(tag):].strip()
                        if key == 'Input':
                            m = re.fullmatch(r'"(.*)"', val)
                            val = (m.group(1) if m else val).encode().decode('unicode_escape')
                        if key == 'Kind':
                            val = 'bench' if val.lower().startswith('bench') else 'trace'
                        info[key.lower()] = val
    except OSError:
        pass
    return info


def tests():
    d = suite_dir()
    try:
        names = sorted(f for f in os.listdir(d) if f.endswith('.s'))
    except OSError:
        return []
    return [header(os.path.join(d, f)) for f in names]
