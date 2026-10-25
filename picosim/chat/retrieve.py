"""
Searching what picosim already says: the knowledge pack.

The GUI builds the pack from the user guide (guide.gd's knowledge_pack)
and sends it once when it connects.  It holds every guide topic as it reads
at each Level (1 to 4) with all its sections unfolded, the toolbar's
controls with their tooltips, the example programs and what each Level
adds.  Here each topic is cut into small typed documents:

    summary   a topic's short version
    section   one sub-heading of a topic and the text under it
    row       one row of a table (an instruction, a Troubleshooting message, a key)
    glossary  one Glossary entry
    card      one datapath / pipeline component card, or one FSM state
    insn      one sentence about an instruction (explain.ABOUT)

and indexed with BM25, one index per Level, so a Level 1 student is only
ever answered from what Level 1 shows.  A document that first appears at a
higher Level is how the bot says "that's part of Level 3".
"""

import math
import re
from collections import Counter

from ..explain import ABOUT, LITERAL_ABOUT
from .normalize import STOP, MNEMONICS, clean, stem, _TOKEN

K1, B = 1.4, 0.72
CARD_TOPICS = ('components', 'pipecomponents', 'states')
ROW_TOPICS_SKIP = ('walkthrough', 'pipewalk')


# ── BBCode to plain text ─────────────────────────────────────────────────────

_TAG = re.compile(r"\[/?(?:color|font_size|u|i|indent|center|right|left|fill|p|font|bgcolor|outline_size|"
                  r"outline_color|cell|table|ul|ol|hr|img|lb|rb|s)(?:[= ][^\]]*)?\]")


def _cells(table_body):
    return re.findall(r"\[cell[^\]]*\](.*?)\[/cell\]", table_body, re.S)


def plain(bb, links=None):
    """Readable text from guide BBCode: **bold**, `code`, bullets and table
    rows on their own lines.  Link targets are added to `links` (a list)."""
    t = bb.replace('[lb]', '\x01').replace('[rb]', '\x02').replace('​', '')

    def table(m):
        n = int(m.group(1))
        cells = [_inline(c, links).strip() for c in _cells(m.group(2))]
        rows = [cells[i:i + n] for i in range(0, len(cells), n)]
        return '\n' + '\n'.join('• ' + ' — '.join(c for c in r if c) for r in rows if any(r)) + '\n'
    t = re.sub(r"\[table=(\d+)\](.*?)\[/table\]", table, t, flags=re.S)
    t = re.sub(r"\[ul\](.*?)\[/ul\]", lambda m: '\n'.join('• ' + x for x in m.group(1).split('\n') if x.strip()),
               t, flags=re.S)
    t = re.sub(r"\[ol[^\]]*\](.*?)\[/ol\]",
               lambda m: '\n'.join(f"{i + 1}. {x}" for i, x in enumerate(y for y in m.group(1).split('\n') if y.strip())),
               t, flags=re.S)
    t = _inline(t, links)
    t = re.sub(r"[ \t]+\n", '\n', t)
    t = re.sub(r"\n{3,}", '\n\n', t)
    return t.replace('\x01', '[').replace('\x02', ']').strip()


def _inline(t, links):
    def url(m):
        if links is not None and m.group(1) not in links:
            links.append(m.group(1))
        return m.group(2)
    t = re.sub(r"\[url=([^\]]+)\](.*?)\[/url\]", url, t, flags=re.S)
    t = re.sub(r"\[/?b\]", '**', t)
    t = re.sub(r"\[/?code\]", '`', t)
    t = _TAG.sub('', t)
    t = t.replace('****', '').replace('``', '')
    return t


# ── cutting topics into documents ────────────────────────────────────────────

_SPLIT = re.compile(r"\[hr[^\]]*\]|\n?\[color=[^\]]+\]\[b\](.+?)\[/b\]\[/color\]\n|"
                    r"\[font_size=\d+\]\[b\](.+?)\[/b\]\[/font_size\]\n*")


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40] or 'x'


class Doc:
    __slots__ = ('id', 'topic', 'topic_name', 'kind', 'title', 'text', 'level', 'links', 'aliases', 'terms', 'tf', 'n')

    def __init__(self, id, topic, topic_name, kind, title, text, level, links=(), aliases=''):
        self.id, self.topic, self.topic_name, self.kind = id, topic, topic_name, kind
        self.title, self.text, self.level = title, text, level
        self.links = list(links)
        self.aliases = aliases


def _bold_only(cell):
    c = cell.strip()
    return c.startswith('[b]') and c.endswith('[/b]') and c.count('[b]') == 1


def topic_docs(t, level):
    """The documents of one topic (dict from the pack) at one Level."""
    tid, name = t['id'], t['name']
    out = []
    if not t.get('full') and not t.get('sum'):
        return out
    if t.get('sum'):
        links = []
        out.append(Doc(f"{tid}#summary", tid, name, 'summary', name, plain(t['sum'], links), level, links))
    full = t.get('full', '')
    if tid in CARD_TOPICS:               # one card per block or state, between rules
        chunks = re.split(r"\[hr[^\]]*\]", full)
        links = []
        intro = plain(re.sub(r"\[font_size=\d+\]\[b\].+?\[/b\]\[/font_size\]", '', chunks[0], count=1), links)
        if intro:
            out.append(Doc(f"{tid}#intro", tid, name, 'section', name, intro, level, links))
        for chunk in chunks[1:]:
            m = re.search(r"\[font_size=\d+\]\[b\](.+?)\[/b\]\[/font_size\]", chunk)
            title = plain(m.group(1)) if m else plain(chunk).split('\n')[0][:60]
            links = []
            text = plain(chunk[m.end():] if m else chunk, links)
            out.append(Doc(f"{tid}#{slug(title)}", tid, name, 'card', title, title + '\n' + text, level, links))
        return out
    parts = _SPLIT.split(full)
    # parts: text, (hr) None None, text, sub, head, text, ...
    head = name
    chunks = [(None, parts[0])]
    i = 1
    while i < len(parts):
        sub, big = parts[i], parts[i + 1]
        title = sub or big
        chunks.append((title, parts[i + 2] if i + 2 < len(parts) else ''))
        i += 3
    seen = Counter()
    for title, body in chunks:
        links = []
        text = plain(body, links)
        if title is None:
            title = name if not out else name + " (introduction)"
        title_plain = plain(title)
        if title_plain == name and not text:
            continue
        if not text and len(title_plain) < 60:
            continue                     # a heading for the sub-headings under it
        kind = 'section'
        k = slug(title_plain)
        seen[k] += 1
        did = f"{tid}#{k}" + (f"-{seen[k]}" if seen[k] > 1 else '')
        if text:
            out.append(Doc(did, tid, name, kind, title_plain, text, level, links))
        # table rows as their own documents
        if tid not in ROW_TOPICS_SKIP:
            for m in re.finditer(r"\[table=(\d+)\](.*?)\[/table\]", body, re.S):
                n = int(m.group(1))
                cells = _cells(m.group(2))
                rows = [cells[j:j + n] for j in range(0, len(cells), n)]
                if n < 2 or len(rows) < 2:
                    continue
                for r_i, r in enumerate(rows):
                    if r_i == 0 and all(_bold_only(c) for c in r):
                        continue
                    links = []
                    cells_p = [_inline(c.replace('[lb]', '\x01').replace('[rb]', '\x02'), links).strip()
                               .replace('\x01', '[').replace('\x02', ']') for c in r]
                    first = cells_p[0].strip('*` ')
                    if not re.search(r'\w', first):
                        more = next((c for c in cells_p[1:] if re.search(r'\w', c)), '')
                        first = (first + ' ' + ' '.join(more.strip('*` ').split()[:4])).strip()
                    if not first:
                        continue
                    kind_r = 'glossary' if tid == 'glossary' else 'row'
                    rid = f"{tid}#row-{slug(first)}"
                    seen[rid] += 1
                    if seen[rid] > 1:
                        rid += f"-{seen[rid]}"
                    out.append(Doc(rid, tid, name, kind_r, first,
                                   (f"**{first}**: " if kind_r == 'glossary' else '') +
                                   (' — '.join(c for c in cells_p[1:] if c) if kind_r == 'glossary' else
                                    ' — '.join(c for c in cells_p if c)),
                                   level, links))
    return out


def insn_docs():
    out = [Doc(f"insn#{m}", 'instructions', 'Instructions', 'insn', m.upper(), text, 1, ['instructions'], aliases=m)
           for m, text in ABOUT.items()]
    out.append(Doc("insn#ldr-literal", 'loadstore', 'Loads and stores', 'insn', 'LDR Rd, =value',
                   LITERAL_ABOUT, 2, ['loadstore'], aliases='ldr literal pool'))
    return out


# ── tokens and BM25 ──────────────────────────────────────────────────────────

def terms(text):
    return [stem(w) for w in _TOKEN.findall(clean(text)) if w not in STOP and not w.isdigit()]


class Index:
    def __init__(self, docs, weights=None):
        self.docs = docs
        self.df = Counter()
        for d in docs:
            ts = terms(d.title) * 3 + terms(d.aliases) * 3 + terms(d.topic_name) + terms(d.text)
            d.terms = ts
            d.tf = Counter(ts)
            d.n = len(ts)
            self.df.update(set(ts))
        self.N = max(1, len(docs))
        self.avg = sum(d.n for d in docs) / self.N if docs else 1.0
        self.weights = weights or {}

    def idf(self, t):
        n = self.df.get(t, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def search(self, qterms, k=8, kinds=None):
        """[(score, coverage, doc)] best first.  Coverage is the share of the
        question's weighted terms that the document contains."""
        qt = list(dict.fromkeys(qterms))
        if not qt:
            return []
        w = {t: self.weights.get(t, 1.0) for t in qt}
        total = sum(self.idf(t) * w[t] for t in qt) or 1.0
        out = []
        for d in self.docs:
            if kinds and d.kind not in kinds:
                continue
            s = 0.0
            hit = 0.0
            for t in qt:
                f = d.tf.get(t)
                if not f:
                    continue
                idf = self.idf(t)
                s += w[t] * idf * f * (K1 + 1) / (f + K1 * (1 - B + B * d.n / self.avg))
                hit += idf * w[t]
            if s > 0:
                if d.kind == 'summary':
                    s *= 1.08
                out.append((s, hit / total, d))
        out.sort(key=lambda x: -x[0])
        return out[:k]


class Knowledge:
    """Every document at every Level, with one index per Level."""

    def __init__(self, pack=None):
        self.pack = pack or {}
        self.by_level = {}
        self.first_level = {}            # doc id → the lowest Level it appears at
        self.topics = {}                 # topic id → (name, lowest Level)
        self.vocab = set()
        self.glossary_terms = set()
        levels = self.pack.get('levels', {})
        for L in range(1, 5):
            docs = []
            for t in levels.get(str(L), levels.get(L, [])):
                for d in topic_docs(t, L):
                    docs.append(d)
                    self.first_level.setdefault(d.id, L)
                    if d.kind == 'glossary':
                        self.glossary_terms.update(terms(d.title))
                self.topics.setdefault(t['id'], (t['name'], max(L, int(t.get('level', 1)))))
            for d in insn_docs():
                if d.level <= L:
                    docs.append(d)
                    self.first_level.setdefault(d.id, d.level)
            self.by_level[L] = docs
        for docs in self.by_level.values():
            for d in docs:
                for w in _TOKEN.findall(clean(d.title + ' ' + d.text)):
                    if len(w) >= 5 and w.isalpha():
                        self.vocab.add(w)
        self.stems = {stem(w) for w in self.vocab}
        weights = {m: 5.0 for m in MNEMONICS}
        for g in self.glossary_terms:
            weights.setdefault(g, 3.0)
        self.index = {L: Index(docs, weights) for L, docs in self.by_level.items()}

    @property
    def loaded(self):
        return bool(self.pack.get('levels'))

    def topic_level(self, tid):
        return self.topics.get(tid, ('', 1))[1]

    def topic_name(self, tid):
        return self.topics.get(tid, (tid, 1))[0]

    def doc(self, did, level=4):
        for L in range(min(4, level), 0, -1):
            for d in self.by_level.get(L, []):
                if d.id == did:
                    return d
        for L in range(level + 1, 5):
            for d in self.by_level.get(L, []):
                if d.id == did:
                    return d
        return None

    def search(self, qterms, level, k=8, kinds=None):
        idx = self.index.get(max(1, min(4, level)))
        return idx.search(qterms, k, kinds) if idx else []


def quick(text, limit=300):
    """A quick answer from a document: its first sentence or two (whole
    sentences, at most about `limit` characters), without a leading heading."""
    t = re.sub(r"^\*\*[^*\n]{1,80}\*\*\n", '', text.strip())
    lines = [l.strip() for l in t.split('\n') if l.strip()]
    if lines and lines[0].startswith('• '):
        out = []
        for l in lines:
            if not l.startswith('• ') or len('\n'.join(out + [l])) > limit and out:
                break
            out.append(l)
        return '\n'.join(out)
    # a line that introduces a list (ends in ":") takes its first two items along as the example
    kept = []
    for i, l in enumerate(lines):
        if l.startswith('• '):
            continue
        if l.endswith(':'):
            items = []
            for nxt in lines[i + 1:i + 3]:
                if not nxt.startswith('• '):
                    break
                items.append(nxt[2:].strip())
            l = l + ' ' + '; '.join(items) + '.' if items else l[:-1] + '.'
        kept.append(l)
    flat = ' '.join(kept) or ' '.join(lines)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z`*(\"“])", flat)
    # "See How a program runs." pointed at another page: the reply links it at the end instead
    sentences = [s for s in sentences if not re.match(r"^(See|Read|More in) [^.]{0,80}\.$", s)] or sentences
    out = sentences[0]
    for s in sentences[1:]:
        if len(out) + 1 + len(s) > limit:
            break
        out += ' ' + s
    return out if len(out) <= limit * 1.6 else excerpt(out, limit)


def excerpt(text, limit=650):
    """The start of a document, cut at a paragraph or sentence, not mid-word."""
    if len(text) <= limit:
        return text
    cut = text.rfind('\n\n', 0, limit)
    if cut < limit * 0.4:
        cut = max(text.rfind('. ', 0, limit), text.rfind('\n', 0, limit))
    if cut < limit * 0.4:
        cut = text.rfind(' ', 0, limit)
    return text[:cut + 1].rstrip() + ' …'
