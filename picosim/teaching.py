"""
Facts that picosim's teaching text quotes, worked out from the simulator
itself, so the guide cannot drift from what the simulator does.

The server sends them to the GUI once, as a 'facts' message, when it connects.
"""

from . import cycles


def cycle_counts():
    """{cycle-table row name: clock cycles}, e.g. {'B label': 5, …}."""
    return {name: n for name, n, _, _ in cycles.rows()}


def facts():
    return {'type': 'facts', 'cycles': cycle_counts()}
