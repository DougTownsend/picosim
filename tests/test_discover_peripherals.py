"""Campaign G: the reference models against hand-worked datasheet values,
its cases against the safety rules, and an end-to-end stand-in run in
which every store the core makes to a peripheral is checked again."""

import argparse
import json
import os
import tempfile
import unittest
from unittest import mock

from picosim.discover import interp_model as M
from picosim.discover import safety as S
from picosim.discover.cases import peripherals as G

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False

M32 = 0xFFFFFFFF


class DividerModelTest(unittest.TestCase):
    """RP2040 datasheet 2.3.1.5, worked by hand."""

    def test_values(self):
        cases = [  # dividend, divisor, signed, quotient, remainder
            (7, 2, False, 3, 1),
            (7, 2, True, 3, 1),
            (-7 & M32, 2, True, -3 & M32, -1 & M32),       # rounds toward zero; remainder takes the dividend's sign
            (7, -2 & M32, True, -3 & M32, 1),
            (-7 & M32, -2 & M32, True, 3, -1 & M32),
            (0x80000000, M32, True, 0x80000000, 0),       # INT_MIN / -1 overflows back to INT_MIN
            (M32, 2, False, 0x7FFFFFFF, 1),
            (5, 0, False, M32, 5),                        # / 0: all ones, remainder = dividend
            (5, 0, True, M32, 5),                         # signed / 0: -1 for a dividend >= 0 ...
            (-5 & M32, 0, True, 1, -5 & M32),             # ... and +1 for a negative one
            (0, 0, True, M32, 0),
        ]
        for p, q, signed, quo, rem in cases:
            with self.subTest(p=hex(p), q=hex(q), signed=signed):
                self.assertEqual(M.div_model(p, q, signed), (quo, rem))


class InterpModelTest(unittest.TestCase):
    def test_datasheet_examples(self):
        """Every example the datasheet and pico-examples publish."""
        ex = G.datasheet_examples()
        self.assertGreater(len(ex), 40)
        for name, num, cfg, pop, want in ex:
            got = G.interp_predict(num, cfg, pop)
            for field, value in want.items():
                with self.subTest(example=name, field=field):
                    self.assertEqual(got[field], value)

    def test_ctrl_write_masks(self):
        """Lane 1 has no BLEND/CLAMP; interpolator 1 has CLAMP, not BLEND."""
        for (num, lane), mask in M.CTRL_WRITABLE.items():
            i = M.Interp(num)
            i.write(M.CTRL_LANE0 if lane == 0 else M.CTRL_LANE1, M32)
            got = i.ctrl_read(lane)
            with self.subTest(interp=num, lane=lane):
                self.assertEqual(got & ~mask & 0x01FFFFFF, 0)


def _ctx(kind, root, pins=frozenset(), target=None):
    from picosim.discover.campaign import Context
    opts = argparse.Namespace(target=kind, pins=pins, limit=None, fresh=True, repeat=False,
                              chip_label='chip1', yes=True, stages=None)
    c = Context(target, opts, log=lambda s: None)
    c.kind = kind
    return c


class DryRunTest(unittest.TestCase):
    def test_no_pin_driven_and_wireless_pins_untouched(self):
        lay = G.LAY
        wireless = {G.pad_addr(p) for p in S.PICO_W_PINS} | {G.ctrl_addr(p) for p in S.PICO_W_PINS}
        stages = G.dry_cases(_ctx('standin', None))
        self.assertNotIn('gpio-drive', [s for s, _, _ in stages])     # only with --pins
        n = 0
        for stage, cases, kw in stages:
            for c in cases:
                S.check_case(c, lay, frozenset())
                for a, _, _ in c.get('writes', ()):
                    self.assertNotIn(a & ~3, wireless, f"{stage} case {c['id']} writes 0x{a:08X}")
                n += 1
        self.assertGreater(n, 900)

    def test_side_effect_reads_are_declared(self):
        """A case that reads QUOTIENT, a spinlock, SYST_CSR ... says so; one
        that does not say so has no such read."""
        side = set(S.read_side_effects())
        for stage, cases, kw in G.dry_cases(_ctx('standin', None)):
            for c in cases:
                touched = {a & ~3 for a in c.get('reads', ())} & side
                if touched:
                    self.assertTrue(c.get('side_effect_ok'), f"{stage} case {c['id']}")

    def test_pins_add_the_drive_stage(self):
        stages = dict((s, cases) for s, cases, _ in G.dry_cases(_ctx('standin', None, frozenset({2}))))
        self.assertIn('gpio-drive', stages)
        for c in stages['gpio-drive']:
            S.check_case(c, G.LAY, frozenset({2}))
            with self.assertRaises(S.SafetyError):
                S.check_case(c, G.LAY, frozenset())


def audit_writes(chip):
    """Record every store the core makes outside RAM (Unicorn sees what the
    case code really does, not what it declared)."""
    import unicorn as U
    seen = []

    def hook(uc, access, addr, size, value, ud):
        if addr < 0x20000000 or addr >= 0x40000000:
            seen.append((addr, size, value & M32))
    chip.uc.hook_add(U.UC_HOOK_MEM_WRITE, hook)
    return seen


def check_audit(test, seen, pins=frozenset()):
    test.assertTrue(seen, "the hook saw no peripheral store")
    for addr, size, value in sorted(set(seen)):
        with test.subTest(addr=hex(addr), value=hex(value)):
            S.check_write(addr, size, value, pins, why='audit')


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn")
class StandInTest(unittest.TestCase):
    def setUp(self):
        from picosim.hwtest.target import Target
        self.root = tempfile.mkdtemp()
        self.t = Target('standin')
        self.patches = [mock.patch('picosim.discover.campaign.cache_dir', lambda *p: os.path.join(self.root, *p)),
                        mock.patch('picosim.discover.facts.root', lambda kind: os.path.join(self.root, 'facts'))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.t.close()

    def facts(self, name):
        with open(os.path.join(self.root, 'facts', 'peripherals', name)) as f:
            return json.load(f)

    def test_run(self):
        seen = audit_writes(self.t.standin.chip)
        G.run(_ctx('standin', self.root, target=self.t))
        check_audit(self, seen)
        sio = {f['id']: f['value'] for f in self.facts('sio.json')['facts']}
        # the stand-in runs interp_model itself, so these check the case code and its decoding
        self.assertEqual(sio['sio.div.differences'], 0)
        self.assertEqual(sio['sio.interp.differences'], 0)
        self.assertEqual(sio['sio.spinlock.differences'], 0)
        self.assertEqual(sio['sio.div.result_cycles'],
                         {'csr_from_k': M.DIV_CYCLES, 'quotient_from_k': M.DIV_CYCLES,
                          'remainder_from_k': M.DIV_CYCLES})
        for f in self.facts('systick.json')['facts']:
            if f['id'].startswith('systick.') and 'agrees' in f.get('detail', {}):
                self.assertTrue(f['detail']['agrees'], f['id'])
        gpio = {f['id']: f['value'] for f in self.facts('gpio.json')['facts']}
        self.assertEqual(gpio['gpio.pins_surveyed'], 30 - len(S.PICO_W_PINS))
        self.assertEqual(gpio['gpio.pins_not_following_pulls'], [])
        with open(os.path.join(self.root, 'standin', 'peripherals', 'status.json')) as f:
            self.assertEqual(json.load(f)['state'], 'done')


if __name__ == '__main__':
    unittest.main()
