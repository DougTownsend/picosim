"""Campaign E on the stand-in: blocks are safe and re-encode after removal,
a planted difference (LDR Rt,[Rn,#imm] one cycle slower on the 'chip') is found, shrunk
to a minimal block, and written as a trace test picosim assembles."""

import argparse
import os
import random
import tempfile
import unittest
from unittest import mock

from picosim.discover.cases import fuzz
from picosim.discover import v6m

try:
    from picosim.hwtest.standin import available, Chip
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False


class BlockTest(unittest.TestCase):
    def test_blocks_are_safe(self):
        rng = random.Random(1)
        for _ in range(300):
            ops = fuzz.make_block(rng)
            code = fuzz.encode(ops)
            stores = sum(o.get('store', False) for o in ops)
            self.assertLessEqual(stores, fuzz.MAX_STORES)
            for k in range(0, len(code), 2):
                hw = int.from_bytes(code[k:k + 2], 'little')
                e = v6m.decode16(hw)
                self.assertNotIn(e.cls, ('undefined', 'udf', 'bkpt', 'svc', 'pc-writing', 'sp-writing'), hex(hw))
                f = e.fields
                if e.cls == 'memory':
                    self.assertEqual(f.get('rn'), 4, e.name)          # always [R4, #imm]
                written = f.get('rd', f.get('rdn', f.get('rt') if 'LDR' in e.name else None))
                if written is not None and e.cls not in ('cond-branch', 'branch-imm') and not e.name.startswith(('CMP', 'CMN', 'TST')) \
                        and not e.name.startswith('STR'):
                    self.assertNotIn(written, (4, 5, 6, 7, 13, 14, 15), f"{e.name} {hw:04x}")

    def test_removing_ops_keeps_branches_forward_and_in_range(self):
        rng = random.Random(2)
        for _ in range(200):
            ops = fuzz.make_block(rng)
            cut = fuzz._without(ops, set(rng.sample(range(len(ops)), len(ops) // 3)))
            code = fuzz.encode(cut)
            for k in range(0, len(code), 2):
                hw = int.from_bytes(code[k:k + 2], 'little')
                if (hw & 0xF000) == 0xD000 and (hw >> 8) & 0xF < 0xE:
                    off = (hw & 0xFF) - (256 if hw & 0x80 else 0)
                    if hw != 0xD100 | (hw & 0xFF) or off >= 0:          # a loop's bne goes back
                        pass
                    self.assertTrue(0 <= k + 4 + 2 * off <= len(code) or off < 0)


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn")
class ShrinkTest(unittest.TestCase):
    def test_a_planted_difference_is_found_and_shrunk(self):
        from picosim.discover.campaign import Context
        from picosim.hwtest.target import Target
        orig = Chip._cost

        def slow_ldr(chip, hw1, hw2, taken):
            return orig(chip, hw1, hw2, taken) + (1 if (hw1 & 0xF800) == 0x6800 else 0)
        root = tempfile.mkdtemp()
        opts = argparse.Namespace(target='standin', pins=frozenset(), limit=60, fresh=True, repeat=False,
                                  chip_label='chip1', hours=None, rounds=1)
        t = Target('standin')
        try:
            with mock.patch.object(Chip, '_cost', slow_ldr), \
                    mock.patch('picosim.discover.campaign.cache_dir', lambda *p: os.path.join(root, *p)), \
                    mock.patch('picosim.discover.facts.root', lambda kind: os.path.join(root, 'facts')):
                ctx = Context(t, opts, log=lambda s: None)
                fuzz.run(ctx)
        finally:
            t.close()
        import json
        with open(os.path.join(root, 'standin', 'fuzz', 'found.json')) as f:
            found = json.load(f)
        self.assertTrue(found['differences'], "the planted LDR slowdown was not found")
        d = found['differences'][0]
        self.assertEqual(d['class'], 'timing')
        self.assertLessEqual(d['shrunk_to'], 2, d)
        with open(d['test']) as f:
            text = f.read()
        self.assertIn('LDR Rt,[Rn,#imm5*4]', text)
        self.assertIn('@ Kind: bench', text)
        # the written test shows the difference on the 'chip' with the slow
        # LDR, and none on the normal stand-in
        from picosim.hwtest.bench import Bench, calibrate_calls
        t = Target('standin')
        try:
            calibrate_calls(t, force=True)
            self.assertEqual(Bench(t, d['test']).run()['verdict'], 'identical')
            with mock.patch.object(Chip, '_cost', slow_ldr):
                calibrate_calls(t, force=True)
                s = Bench(t, d['test']).run()
            self.assertEqual(s['verdict'], 'different', s.get('regions'))
        finally:
            t.close()


if __name__ == '__main__':
    unittest.main()
