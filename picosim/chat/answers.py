"""
Answers built from the knowledge pack: a control's how-to (from its own
tooltip), a guide document, a "that's Level N" teaser, the examples, the
levels, number conversions and features picosim does not have.
"""

import os
import re

from .catalog import CONTROLS, NOT_BUILT, CHIP_LACKS, C_LANG, C_REPLY, FEATURE_LEVEL
from .live import show_me
from .reply import Reply
from .retrieve import excerpt, plain, quick, terms, slug

LEVEL_NAMES = {1: "Basics", 2: "Memory & I/O", 3: "Inside the CPU", 4: "Pipeline & performance"}


def level_name(kb, n):
    for e in (kb.pack.get('level_info') or []) if kb else []:
        if int(e.get('n', 0)) == n:
            return e.get('name', LEVEL_NAMES.get(n, ''))
    return LEVEL_NAMES.get(n, '')


def teaser(kb, n, what, first_sentence, topic=None, intent='above_level'):
    t = f"That's part of **Level {n}** ({level_name(kb, n)})."
    if first_sentence:
        t = first_sentence.rstrip() + ' ' + t
    t += " Switch with the **Level** menu, top right, when your class gets there."
    r = Reply(t, intent).act('set_level', n, f"Switch to Level {n}")
    r.teaser = True
    if topic:
        r.link(topic, kb.topic_name(topic) if kb else topic)
    return r


def _first_sentence(text, limit=220):
    t = ' '.join(text.replace('\n', ' ').split())
    m = re.search(r'^(.{30,}?[.!?])(\s|$)', t)
    s = m.group(1) if m else t
    return s if len(s) <= limit else s[:limit].rsplit(' ', 1)[0] + ' …'


# ── controls ────────────────────────────────────────────────────────────────

def tooltip(kb, cid):
    for c in (kb.pack.get('controls') or []) if kb else []:
        if c.get('id') == cid:
            return c.get('tooltip', '')
    return ''


_KEYS = re.compile(r"\s*\((?:(?:Ctrl|Cmd|Space|F\d{1,2}|[A-Z0-9]|or|/|\+|−| )+)\)")


def howto(kb, cid, ctx, srv=None):
    c = CONTROLS[cid]
    level = c.get('level', 1)
    tip = c.get('text') or tooltip(kb, cid)
    tip = '. '.join(_KEYS.sub('', l).strip().rstrip('.') for l in tip.split('\n') if l.strip())
    tip = ' '.join(tip.split())
    if not tip and not c.get('extra') and kb is not None:      # a panel: what its guide page says first
        d = kb.doc(f"{c['guide']}#summary", 4)
        tip = quick(d.text, 280) if d else ''
        if tip.endswith('.'):
            tip = tip[:-1]
    key = f" ({c['key']})" if c.get('key') else ''
    body = f"**{c['name']}**{key}: {tip}" if tip else f"**{c['name']}**{key}."
    if tip and not tip.endswith(('.', ')', '!', '?')):
        body += '.'
    if c.get('extra'):
        body += ' ' + c['extra']
    if tip and re.sub(r"\*", '', tip)[:len(c['name']) + 2].lower().startswith(c['name'].lower()):
        body = tip + '.'                 # the page's first sentence already names it
    if level > ctx.level:
        r = teaser(kb, level, c['name'], body, c['guide'], intent=f"howto:{cid}")
        return r
    r = Reply(body, f"howto:{cid}", slots={'control': cid})
    if ctx.known and not ctx.shows(cid):
        why = c.get('hidden')
        if cid in ('flash', 'connect') and ctx.mode == 'pico':
            why = None
        if cid == 'diagram' and ctx.level >= 3:
            why = None
        if why:
            r.text += '\n' + why
        elif cid in ('cycles',):
            r.text += "\nPress **Show CPU Diagram** first."
    show_me(r, ctx, cid)
    return r.link(c['guide'], kb.topic_name(c['guide']) if kb else c['guide'])


# ── documents ───────────────────────────────────────────────────────────────

def doc_reply(kb, d, ctx, confidence=1.0):
    """A quick answer in words from a guide document; the rest of it is behind
    More detail, and the page is linked at the end as a reference."""
    if d.kind == 'card':
        body = f"**{d.title.splitlines()[0]}**: {quick(card_text(d.text))}"
    else:
        body = quick(d.text)
        if d.kind in ('section', 'row') and d.title and len(body) < 120 and d.title.lower() not in body[:80].lower():
            body = f"**{d.title.splitlines()[0]}**: {body}"
    detail = ''
    if len(d.text) > len(body) + 20:
        detail = excerpt(d.text, 1400)
    elif d.kind == 'summary':
        full = kb.doc(f"{d.topic}#{slug(d.topic_name)}", ctx.level)
        if full and full.text != d.text:
            detail = excerpt(full.text, 1400)
    r = Reply(body, f"doc:{d.id}", source='guide', detail=detail, confidence=confidence)
    r.link(d.topic, d.topic_name)
    for l in d.links[:3]:
        if l in kb.topics and l != d.topic:
            r.link(l, kb.topic_name(l))
    return r


def lead(d):
    """A document's quick answer in one or two sentences (for teasers)."""
    if d.kind == 'card':
        return f"**{d.title.splitlines()[0]}**: {quick(card_text(d.text), 220)}"
    if d.kind == 'glossary':
        return quick(d.text, 220)
    return quick(d.text, 220)


def card_text(text):
    """What a datapath card or FSM state does, without its title, kind line
    and sub-headings: the "What it does" paragraph, or the first real sentence."""
    m = re.search(r"\*\*What it does\*\*\s*\n(.+?)(?:\n\s*\n|\n\*\*|$)", text, re.S)
    if m:
        return m.group(1).strip()
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for l in lines[1:]:
        if len(l) > 40 and not l.startswith('**') and ('.' in l or ':' in l):
            return l
    return '\n'.join(lines[1:]) or text


def search_reply(kb, q, ctx):
    """(reply, coverage, top results) from the guide at the student's level, or
    a teaser when a higher level answers it much better."""
    here = _prefer(kb.search(q.terms, ctx.level, 8), q)
    if ctx.level < 4:
        above = _prefer(kb.search(q.terms, 4, 6), q)
        if above:
            s4, c4, d4 = above[0]
            lv = kb.first_level.get(d4.id, 1)
            best_here = here[0] if here else (0, 0, None)
            if lv > ctx.level and c4 >= 0.75 and (best_here[1] < 0.6 or s4 > best_here[0] * 1.6):
                return teaser(kb, lv, d4.title, lead(d4), d4.topic, intent=f"doc:{d4.id}"), c4, above
    if not here:
        return None, 0.0, []
    s, cov, d = here[0]
    return doc_reply(kb, d, ctx, cov), cov, here


_DEFINE = re.compile(r"^(what|whats|who) (is|are|does) (an? |the )?[\w./+-]+( [\w./+-]+)?( mean| stand for| do)?\??$|"
                     r"^(define|meaning of|explain) ")
_KIND_WEIGHT = {'glossary': 1.6, 'card': 1.3, 'summary': 1.2, 'insn': 1.3, 'row': 0.85}


def _prefer(results, q):
    """For "what is X" questions, definitions (glossary, cards) come first."""
    if not results or not _DEFINE.search(q.text):
        return results
    out = [(s * _KIND_WEIGHT.get(d.kind, 1.0), c, d) for s, c, d in results]
    out.sort(key=lambda x: -x[0])
    return out


def pinned_doc(kb, did, ctx):
    """A document a rule names, at the student's level (or a teaser for it)."""
    lv = kb.first_level.get(did)
    if lv is None:
        return None
    d = kb.doc(did, max(ctx.level, lv))
    if lv > ctx.level:
        return teaser(kb, lv, d.title, lead(d), d.topic, intent=f"doc:{d.id}")
    return doc_reply(kb, d, ctx)


def did_you_mean(kb, results, ctx):
    """Not sure: the likeliest answer in words, said as a guess, with the
    other close matches as chips."""
    best = results[0][2]
    seen = [best.topic]
    chips = []
    for s, c, d in results[1:]:
        if d.topic in seen:
            continue
        seen.append(d.topic)
        label = d.title if d.kind != 'summary' else d.topic_name
        chips.append({'label': label.splitlines()[0][:48], 'doc': d.id})
        if len(chips) == 2:
            break
    title = best.title.splitlines()[0] if best.kind != 'summary' else best.topic_name
    r = Reply(f"I'm not completely sure what you're asking, but this may help: **{title}**. {quick(best.text, 260)}" +
              ("\nIf you meant something else, pick one below or ask another way." if chips else
               "\nIf you meant something else, try asking another way."),
              'did_you_mean', policy='abstain', confidence=0.4, detail=excerpt(best.text, 1400))
    r.link(best.topic, best.topic_name)
    r.suggestions = chips
    return r


# ── examples and levels ─────────────────────────────────────────────────────

EXAMPLE_WORDS = {
    '01': "print letter character hello putchar movs first",
    '02': "add subtract sum numbers arithmetic adds subs digit",
    '03': "loop count counter compare branch cmp bne repeat",
    '04': "memory list array load store ldr str word data sum total",
    '05': "led blink output gpio pin delay loop light",
    '06': "input type key keyboard echo getchar read console",
    '07': "function call bl return lr stack push pop subroutine",
    '08': "button input led gpio pin read switch press poll",
}


def examples_answer(kb, q, ctx):
    exs = (kb.pack.get('examples') or []) if kb else []
    if not exs:
        return None
    best = None
    qt = set(q.terms)
    for e in exs:
        words = terms(e.get('title', '') + ' ' + e.get('about', '') + ' ' + EXAMPLE_WORDS.get(e.get('file', '')[:2], ''))
        score = len(qt & set(words))
        if best is None or score > best[0]:
            best = (score, e)
    if best[0] == 0:
        listing = '\n'.join(f"• **{e['title']}** (Level {e['level']}): {e['about']}" for e in exs
                            if int(e.get('level', 1)) <= ctx.level)
        r = Reply("The examples at your level, from **Examples** in the toolbar:\n" + listing, 'examples')
        return show_me(r, ctx, 'examples', "Show me Examples").link('examples', "Level 1 examples")
    e = best[1]
    lv = int(e.get('level', 1))
    r = Reply(f"**{e['title']}** ({e['file']}): {e['about']} Open it from **Examples** in the toolbar and step "
              "through it." + (f" It's a Level {lv} example." if lv > ctx.level else ''), 'examples')
    show_me(r, ctx, 'examples', "Show me Examples")
    return r.link('examples' if lv == 1 else 'examples2', "Level 1 examples" if lv == 1 else "Level 2 examples")


def levels_answer(kb, q, ctx):
    info = (kb.pack.get('level_info') or []) if kb else []
    nums = sorted({int(n) for n in re.findall(r"\blevels? ([1-4])\b|\band ([1-4])\b", q.text) for n in n if n})
    if len(nums) >= 2:
        rows = '\n'.join(f"• **Level {x['n']} · {x['name']}**: {x['about']}" for x in info if int(x['n']) in nums)
        r = Reply("Each level adds to the one before:\n" + rows + "\nMove up when your teacher says your class is ready "
                  "for it; changing the level never changes your program.", 'levels')
        return show_me(r, ctx, 'level', "Show me the Level menu").link('start', "Start here")
    m = re.search(r"\blevel ([1-4])\b", q.text)
    want = int(m.group(1)) if m else None
    if want is None:
        for pat, n in FEATURE_LEVEL:
            if re.search(pat, q.text):
                want = n
                break
    if want is not None:
        e = next((x for x in info if int(x['n']) == want), {'name': LEVEL_NAMES[want], 'about': '', 'news': '', 'topic': 'start'})
        here = " You're there already." if want <= ctx.level else ''
        t = f"**Level {want} · {e['name']}**: {e['about']}.{here}"
        if e.get('news'):
            t += f"\nIt adds {plain(e['news'])}"
        r = Reply(t, 'levels', slots={'level': want})
        if want != ctx.level:
            r.act('set_level', want, f"Switch to Level {want}")
        return r.link(e.get('topic') or 'start', kb.topic_name(e.get('topic') or 'start') if kb else 'Start here')
    rows = '\n'.join(f"• **Level {x['n']} · {x['name']}**: {x['about']}" + ("   ← you are here" if int(x['n']) == ctx.level else '')
                     for x in info) or '\n'.join(f"• Level {n} · {name}" for n, name in LEVEL_NAMES.items())
    r = Reply("The **Level** menu (top right) decides how much of picosim you see; each level adds to the one "
              "before:\n" + rows + "\nYour teacher will say when to move up. Changing it never changes your program.",
              'levels')
    return show_me(r, ctx, 'level', "Show me the Level menu").link('start', "Start here")


# ── things picosim or the chip does not have ────────────────────────────────

def missing(q):
    for pat, text in CHIP_LACKS:
        if re.search(pat, q.text):
            return Reply(text, 'chip_lacks').link('instructions', "Instructions")
    if re.search(C_LANG, q.text):
        return Reply(C_REPLY, 'not_built:c').link('programs', "Writing programs")
    for pat, name, instead in NOT_BUILT:
        if re.search(pat, q.text):
            return Reply(f"That's not in picosim (yet): it doesn't simulate {name}. {instead}", f"not_built",
                         slots={'feature': name}).link('programs', "Writing programs")
    return None


# ── number conversion ───────────────────────────────────────────────────────

def convert(q):
    m = re.search(r"'(.)'", q.raw) or re.search(
        r"\b(?:ascii|code|value)\s+(?:code\s+)?(?:of|for)\s+(?:the\s+)?(?:letter\s+|character\s+)?([a-z])\b", q.raw, re.I)
    if m:
        val = ord(m.group(1))
    else:
        nums = re.findall(r"0x[0-9a-f]+|0b[01]+|\b\d+\b", q.text)
        if not nums:
            return None
        val = int(nums[0], 0) if not nums[0].isdigit() else int(nums[0])
    if val > 0xFFFFFFFF:
        return None
    parts = [f"{val} in decimal", f"0x{val:X} in hex", f"0b{val:b} in binary"]
    if 32 <= val < 127:
        parts.append(f"the character '{chr(val)}' in ASCII")
    elif val == 10:
        parts.append("the new-line character in ASCII")
    r = Reply(' = '.join(parts) + '.', 'convert')
    if 16 <= val < 0x100:
        r.text += f" (Each hex digit is 4 bits: 0x{val:X} = {val >> 4} × 16 + {val & 15}.)"
    return r.link('numbers', "Hex, binary and ASCII")
