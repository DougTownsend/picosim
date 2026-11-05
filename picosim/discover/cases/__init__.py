"""One module per discovery campaign (see picosim/discover/campaign.py)."""

from importlib import import_module

ORDER = ('identity', 'memmap', 'registers', 'encodings', 'timing', 'fuzz', 'flashed', 'peripherals')
LETTERS = {'identity': '0', 'memmap': 'A', 'registers': 'C', 'encodings': 'B', 'timing': 'D',
           'fuzz': 'E', 'flashed': 'F', 'peripherals': 'G'}


def module(name):
    return import_module(f'.{name}', __name__)
