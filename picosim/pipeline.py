"""
The RP2040's 2-stage pipeline, cycle by cycle, for the GUI.

The core still runs one whole instruction at a time.  Completed
instructions are laid out on the Cortex-M0+'s 2-stage pipeline with
timing.schedule (datasheet Table 81 cycle counts; the next sequential
instruction is fetched, and discarded when a branch is taken), and a cursor
`t` is the pipeline cycle on display.  Moving the cursor past the last
instruction laid out runs the next one, so the registers show the state
after the instruction in Execute has finished.

Used by the server (the main window's pipeline view) and by the walkthrough
(the user guide's pipeline walkthrough).
"""

from . import timing

KEEP = 64           # rows kept
TRAIL = 8           # cycles in the view's instruction strip


class Pipeline:
    def __init__(self, core, asm):
        self.core = core
        self.asm = asm
        self.reset()

    def _completed(self):
        c = self.core
        return c.steps - (1 if c.in_insn else 0)

    def reset(self):
        """Start an empty pipeline at the next instruction boundary."""
        self.rows = []             # timing.schedule rows, absolute cycles
        self.total = 0             # cycles laid out so far
        self.t = -1                # cycle shown; -1: the first fetch
        self.seq = self._completed()
        self.seq0 = self.seq

    def sync(self):
        """Lay out the instructions completed since the last call."""
        c = self.core
        new = self._completed() - self.seq
        if new <= 0:
            return
        recs = [r for r in c.recent(min(new, 512)) if r['seq'] > self.seq]
        sch = timing.schedule('pico2', recs, 1, self.asm, t0=self.total)
        self.rows.extend(sch['rows'])
        del self.rows[:-KEEP]
        self.total = sch['total']
        self.seq += new

    def step(self, run_insn):
        """Advance one cycle; `run_insn()` runs the next instruction and
        returns False if it could not.  Returns False if the cursor did not move."""
        t = self.t + 1
        while t >= self.total:
            if not run_insn():
                return False
            self.sync()
        self.t = t
        return True

    def step_insn(self, run_insn):
        """Advance to the first cycle of the next instruction to execute."""
        for r in self.rows:
            if not r['flushed'] and r['start'] > self.t:
                self.t = r['start']
                return True
        before = self.total
        if not run_insn():
            return False
        self.sync()
        if self.total > before:
            self.t = before
        return True

    def follow(self):
        """Show the last cycle laid out (after running at full speed)."""
        self.sync()
        self.t = self.total - 1

    def draining(self):
        """Cycles are left to show after the program has halted."""
        return self.t < self.total - 1

    def at_breakpoint(self, breakpoints):
        """A breakpointed instruction is in Fetch and executes next."""
        c = self.core
        return self.t == self.total - 1 and not c.in_insn and c.pc in breakpoints

    def view(self, t=None):
        """The pipeline at cycle t (default: the cursor): the instruction in
        each stage, and the last TRAIL cycles as a strip of instructions."""
        c = self.core
        asm = self.asm
        t = self.t if t is None else t
        rows = list(self.rows)
        if not c.halted:
            nxt = c.insn_addr if c.in_insn else c.pc
            rows.append({'seq': None, 'addr': nxt, 'text': asm.get(nxt, '?'), 'row': '',
                         'start': self.total, 'cycles': 0, 'exec': 0, 'refill': 0,
                         'timed': True, 'stages': {self.total - 1: 'F'}, 'flushed': False,
                         'pending': True, 'sio': 0, 'apb': 0})
        fc = [min(k for k, v in r['stages'].items() if v == 'F') for r in rows]

        def e_at(u):
            for i, r in enumerate(rows):
                if r['stages'].get(u) == 'E':
                    return i
            return None

        def f_at(u):
            # the instruction behind the one executing: fetched in this
            # cycle, or already fetched and waiting while a multi-cycle
            # instruction executes
            e = e_at(u)
            for i in range(0 if e is None else e + 1, len(rows)):
                if fc[i] >= u:
                    return i
            return None

        def tok(i):
            r = rows[i]
            try:
                hw1 = c.read16(r['addr'])
                hw2 = c.read16(r['addr'] + 2) if timing.is_wide(hw1) else 0
                group, row = timing.pico_row(hw1, hw2)[:2]
            except (RuntimeError, ValueError, IndexError):
                group, row = '', ''
            return {'id': fc[i], 'addr': r['addr'], 'text': ' '.join(str(r['text']).split()),
                    'row': r['row'] or row, 'group': group, 'flushed': r['flushed'],
                    'pending': r.get('pending', False), 'start': r['start'],
                    'cycles': r['cycles'], 'exec': r['exec'], 'refill': r['refill'],
                    'timed': r['timed'], 'sio': r['sio'], 'apb': r['apb']}

        def slots(u):
            e, f = e_at(u), f_at(u)
            out = {'E': None, 'F': None}
            if e is not None:
                r = rows[e]
                k = u - r['start'] + 1
                out['E'] = dict(tok(e), k=k, refilling=k > r['exec'],
                                redirect=r['refill'] > 0 and k == r['exec'])
            if f is not None:
                held = e is not None and u - 1 >= rows[e]['start'] and f_at(u - 1) == f
                out['F'] = dict(tok(f), status='held' if held else 'fetch')
            return out, e, f

        now, _, _ = slots(t)
        cells = {}
        for u in range(t - TRAIL + 1, t + 1):
            s, e, f = slots(u)
            if e is not None:
                cells.setdefault(e, []).append([u, 'R' if s['E']['refilling'] else 'E'])
            if f is not None:
                cells.setdefault(f, []).append([u, 'W' if s['F']['status'] == 'held' else 'F'])
        trail = [dict(tok(i), cells=cells[i]) for i in sorted(cells, key=lambda i: fc[i])]
        # instructions that finished before cycle t
        done = self.seq - self.seq0 - sum(
            1 for r in self.rows if not r['flushed'] and r['start'] + r['cycles'] - 1 >= t)
        return {'t': t, 'cycle': t + 1, 'insns': max(0, done), 'E': now['E'], 'F': now['F'],
                'trail': trail, 'first': t - TRAIL + 1,
                'more': not c.halted or t < self.total - 1}
