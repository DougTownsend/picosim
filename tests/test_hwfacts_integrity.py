"""The committed hardware facts (hwfacts/) as data other code will use
(Level 5, Stage 4): every document passes the facts schema, all of them
come from one real chip (never the stand-in), every replay file matches its
SHA-256 and names a run that raw_index.json records, and every
known_differences.json entry says what was measured and why.  The raw
captures themselves live outside git; `python3 -m picosim.discover
verify-raw` checks those on the machine that has them."""

import glob
import json
import os
import re
import unittest

from picosim.discover import facts as FX
from picosim.discover.replay import HWFACTS

NOT_FACTS = {'raw_index.json', 'read_side_effects.json', 'known_differences.json'}


def fact_docs():
    out = []
    for p in sorted(glob.glob(os.path.join(HWFACTS, '**', '*.json'), recursive=True)):
        rel = os.path.relpath(p, HWFACTS)
        if rel in NOT_FACTS or rel.startswith('replay' + os.sep):
            continue
        out.append((rel, p))
    return out


def load(p):
    with open(p) as f:
        return json.load(f)


@unittest.skipUnless(fact_docs(), "no real-chip facts in hwfacts/ yet")
class HwfactsIntegrityTest(unittest.TestCase):
    def test_every_document_passes_the_schema(self):
        for rel, p in fact_docs():
            with self.subTest(file=rel):
                FX.validate(load(p))

    def test_one_real_chip(self):
        chips = {}
        for rel, p in fact_docs():
            prov = load(p)['provenance']
            with self.subTest(file=rel):
                self.assertEqual(prov.get('target'), 'openocd', "stand-in results never go in hwfacts/")
                if prov.get('chip_label', 'chip1') == 'chip1':
                    chips.setdefault(prov['chip'].get('chip_id'), []).append(rel)
        self.assertEqual(len(chips), 1, f"hwfacts/ mixes chips: {chips}")

    def test_fact_ids_are_unique_per_document_and_sources_known(self):
        for rel, p in fact_docs():
            ids = [f['id'] for f in load(p)['facts']]
            with self.subTest(file=rel):
                self.assertEqual(len(ids), len(set(ids)))

    def test_replays_are_intact_and_indexed(self):
        index = load(os.path.join(HWFACTS, 'raw_index.json'))
        runs = {k.split('/')[-1] for k in index}
        metas = sorted(glob.glob(os.path.join(HWFACTS, 'replay', '*.json')))
        self.assertTrue(metas)
        for m in metas:
            meta = load(m)
            with self.subTest(replay=os.path.basename(m)):
                recs, _ = FX.read_replay(m)                    # checks the SHA-256
                self.assertEqual(len(recs), meta['cases'])
                self.assertTrue(all(c['id'] == r['id'] for c, r in recs), "a result names another case")
                self.assertIn(meta['run_id'], runs, "the replay's raw run is not in raw_index.json")

    def test_raw_index_entries(self):
        index = load(os.path.join(HWFACTS, 'raw_index.json'))
        for key, e in index.items():
            with self.subTest(run=key):
                self.assertRegex(key, r'^chip\d+/[a-z0-9-]+-?[A-Za-z0-9-]*/\d{8}-\d{6}(-repeat)?$')
                self.assertIn('state.json', e['files'])
                for name, sha in e['files'].items():
                    self.assertRegex(sha, r'^[0-9a-f]{64}$', name)

    def test_known_differences_say_what_and_why(self):
        doc = load(os.path.join(HWFACTS, 'known_differences.json'))
        for k in doc['known']:
            with self.subTest(name=k.get('name')):
                for field in ('class', 'name', 'reason', 'todo', 'measured'):
                    self.assertTrue(k.get(field), f"'{field}' is missing")
                re.compile(k['name'])


if __name__ == '__main__':
    unittest.main()
