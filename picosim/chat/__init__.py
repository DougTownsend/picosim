"""
Ask picosim: the in-app help chatbot.

    engine = Engine()
    engine.set_pack(pack)                       # the GUI's knowledge pack
    reply = engine.answer(server, "why did it stop?", context)

The router works in layers, not as one ordered list (see the plan in the
docs and each module):

  1. guards on the raw text: wellbeing, bullying, prompt injection, abuse,
     personal data (safety.py); empty, too long, not English
  2. follow-ups to the last answer (memory.py)
  3. the help policy: a request for a solution gets a hint (policy.py)
  4. rules and entities: live questions about the running program
     (live.py), controls, instructions, registers, flags, labels, levels,
     features picosim lacks (catalog.py, answers.py)
  5. a search of the guide, at the student's level (retrieve.py)
  6. off-topic, decided last, then "not sure" (or Claude, when the teacher
     has turned AI on: claude.py)

Every answer goes through the level filter: nothing above the student's
level is explained in full, and no control the level hides is pointed at.
"""

import re

from .. import explain
from . import answers, catalog, config, live, policy, safety
from .catalog import RULES, FOLLOW, COURSE_WORDS, OFF_TOPIC_WORDS, GENERIC_SHAPE
from .live import Ctx
from .memory import Conversation
from .normalize import Query, MNEMONICS, REGISTERS
from .reply import Reply
from .retrieve import Knowledge, excerpt

MAX_LEN = 2000
ACCEPT = 0.6          # coverage a guide document needs to be the answer
DOUBT = 0.34          # below ACCEPT and above this: "did you mean"

_FOREIGN = set("""que qué como cómo porque por para una el la los las del mi tu es está esta esto hola gracias
pourquoi comment est-ce je tu il elle les des une merci bonjour warum wie was ist nicht ich du der die das danke
olá obrigado não você ciao perché come grazie""".split())
_ENGLISH = set("the is what how why does do my it a to in of and can i you this that not".split())

CONTEXT_CHIPS = {
    'fault': "Why did it crash?",
    'failed': "Why didn't I pass?",
    'waiting': "Why is it waiting?",
    'fresh': "What does Step do?",
}


def not_english(text):
    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if ord(c) > 0x24F) >= max(3, len(letters) // 3):
        return True              # another script: Cyrillic, Greek, CJK, Arabic, …
    words = re.findall(r"[a-zà-ÿ'-]+", text.lower())
    f = sum(1 for w in words if w in _FOREIGN)
    e = sum(1 for w in words if w in _ENGLISH)
    return f >= 2 and f > e


def suggestions_for(srv, ctx):
    """Chips that fit what is on screen."""
    c = srv.core if srv is not None else None
    if c is None:
        return ["How do I start?", "What can you do?"]
    if srv.fault:
        return [CONTEXT_CHIPS['fault'], "What just happened?"]
    if srv.waiting_input:
        return [CONTEXT_CHIPS['waiting']]
    if srv.challenge and c.halted:
        res = srv._checks() or []
        if res and not all(r['ok'] for r in res):
            return [CONTEXT_CHIPS['failed'], "Give me a hint"]
        return ["Did I pass?"]
    if c.steps <= max(3, srv.boot_steps) and ctx.level == 1:
        return [CONTEXT_CHIPS['fresh'], "What happens next?"]
    if c.halted:
        return ["Why did it stop?"]
    return ["What just happened?", "What happens next?"]


class Engine:
    def __init__(self, pack=None):
        self.kb = Knowledge(pack)
        self.conv = Conversation()
        self.cfg = config.load()
        self.ai = None                  # claude.Assistant when the teacher turned AI on (server sets it)
        self._course = set(COURSE_WORDS)
        self._refresh_course()

    def set_pack(self, pack):
        self.kb = Knowledge(pack)
        self._refresh_course()

    def _refresh_course(self):
        self._course = set(COURSE_WORDS) | self.kb.glossary_terms

    def program_loaded(self):
        self.conv.program_loaded()

    def ai_on(self):
        return self.ai is not None and self.ai.enabled()

    # ── the router ───────────────────────────────────────────────────────

    def answer(self, srv, text, ctx=None, doc=None):
        ctx = ctx if isinstance(ctx, Ctx) else Ctx(ctx)
        text = (text or '').strip()
        if doc:
            d = self.kb.doc(doc, ctx.level)
            if d is not None:
                r = answers.doc_reply(self.kb, d, ctx)
                self.conv.remember(text or d.title, r)
                return r
        if not text:
            return Reply("Type a question first.", 'empty', policy='abstain')
        g = safety.check(text)
        if g.kind == 'wellbeing':
            return Reply(safety.wellbeing_reply(self.cfg.get('helpline')), 'wellbeing', policy='safety')
        if g.kind == 'bullying':
            return Reply(safety.BULLYING, 'bullying', policy='safety')
        if g.kind == 'injection':
            r = Reply(safety.INJECTION, 'injection',
                      policy='hint' if re.search(r"answer|solution|code|program|full|give", text.lower()) else 'off-topic')
            r.suggestions = suggestions_for(srv, ctx)
            return r
        if g.kind == 'insult':
            r = Reply(safety.INSULT, 'insult', policy='safety')
            r.suggestions = suggestions_for(srv, ctx) + ["What can you do?"]
            return r
        if g.kind == 'abuse':
            r = Reply(safety.ABUSE, 'abuse', policy='safety')
            r.suggestions = suggestions_for(srv, ctx)
            return r
        notes = []
        if g.personal:
            text = safety.redact(text)
            notes.append(safety.PERSONAL)
        if len(text) > MAX_LEN and not re.search(r"Error:", text):
            return Reply(catalog.TOO_LONG.format(n=MAX_LEN), 'too_long', policy='abstain')
        if not_english(text):
            return Reply(catalog.NOT_ENGLISH, 'not_english', policy='abstain')
        if g.kind == 'distress':
            rest = safety.without_distress(text)
            q = self._query(srv, rest) if rest else None
            if q is None or not self._course_words(q):
                r = Reply(safety.DISTRESS, 'distress', policy='safety')
                r.suggestions = suggestions_for(srv, ctx)
                return r
            r = self._route(srv, q, ctx)
            r.text = safety.DISTRESS.rsplit(' Let', 1)[0] + "\n" + r.text
            r.intent = 'distress+' + r.intent
            self.conv.remember(rest, r)
            return r
        q = self._query(srv, text)
        r = self._route(srv, q, ctx)
        if notes:
            r.text = r.text + "\n" + ' '.join(notes)
        if self.ai_on() and r.policy == 'abstain' and r.intent in ('unknown', 'did_you_mean'):
            r.escalate = True
        elif self.ai_on() and r.policy == 'answer' and r.source == 'guide' and r.confidence < 0.75:
            r.ai_offer = True
        self.conv.remember(text, r)
        return r

    def _query(self, srv, text):
        labels = []
        if srv is not None and srv.prog is not None:
            labels = [n for n in list(srv.prog.label_map) + list(srv.prog.sym_map.values())
                      if not n.startswith(('.', '_')) and len(n) > 1]
        return Query(text, self.kb.vocab, labels, keep=OFF_TOPIC_WORDS, stems=getattr(self.kb, 'stems', ()))

    def _course_words(self, q):
        return bool(q.regs or q.mnems or q.addrs or q.labels or set(q.words) & self._course or
                    set(q.terms) & self._course or catalog.find_control(q.text))

    def _route(self, srv, q, ctx):
        kb = self.kb
        t = q.text

        # 2. follow-ups
        fu = self._follow_up(srv, q, ctx)
        if fu is not None:
            return fu

        # pasted assembler output
        if re.search(r"\.s:\d+:\s*(Error|Warning)", q.raw) or re.search(r"\bError:", q.raw):
            return live.load_error(srv, ctx, pasted=q.raw)

        rules = sorted(((w, i) for i, w, p in RULES if p.search(t)), reverse=True)
        top = rules[0][1] if rules else None
        top_w = rules[0][0] if rules else 0.0

        # 3. the help policy (load errors and faults are mechanical: their exact fix is fine)
        risk = policy.solution_risk(q, srv.challenge if srv is not None and srv.prog is not None else None)
        if srv is not None and risk >= 0.6:
            if getattr(srv, 'last_load_error', None) and (top == 'load_error' or re.search(
                    r"\b(fix|error|errors|mistake|wrong|load|compile|assemble|line)\b", t)):
                return live.load_error(srv, ctx)
            if srv.fault and (top == 'why_fault' or re.search(r"\b(fix|fault|crash|wrong|stop|stopped)\b", t)):
                return live.why_fault(srv, ctx)
        if risk >= 0.6:
            if srv is not None and srv.challenge and srv.core is not None:
                return policy.hint(srv, self.conv)
            return policy.general_hint(kb, q, ctx)

        # 4. rules
        if top_w >= 0.85:
            r = self._rule(top, srv, q, ctx)
            if r is not None:
                return r

        # phrasings one guide section answers exactly
        for pat, did in catalog.DOC_RULES:
            if pat.search(t):
                r = answers.pinned_doc(kb, did, ctx)
                if r is not None:
                    return r

        # features picosim or the chip does not have
        r = answers.missing(q)
        if r is not None:
            return r

        # entities: instructions, registers, flags, labels, addresses
        r = self._entities(srv, q, ctx)
        if r is not None:
            return r

        # controls
        cids = catalog.find_controls(t)
        cid = cids[0] if cids else None
        if len(cids) > 1 and re.search(r"\b(also|and also|plus|and is there|and how)\b", t):
            first, second = answers.howto(kb, cids[0], ctx, srv), answers.howto(kb, cids[1], ctx, srv)
            first.text += "\n" + second.text
            first.actions += second.actions
            for l in second.links:
                first.link(*l)
            return first
        if cid and (re.search(r"\b(what|how|where|which|can i|is there|use|press|click|find|see|do i|does|button|"
                              r"key|turn|change|make|set|get|show|hide|open|switch)\b", t) or len(q.terms) <= 3):
            return answers.howto(kb, cid, ctx, srv)

        if rules and top_w >= 0.6:
            r = self._rule(top, srv, q, ctx)
            if r is not None:
                return r
        if re.search(r"\bwhat does (the )?[\w ]{2,20} example do\b|\bthe \w+ example\b|"
                     r"\bwhich example\b|\bwhat example\b|\ban example (that|with|for|of|about|which)\b|"
                     r"\bexamples? (shows?|about|for|with|that|of)\b|\bexample\b.*\b(loop|led|memory|function|button|input)\b", t):
            r = answers.examples_answer(kb, q, ctx)
            if r is not None:
                return r

        # a definition at the end of a longer message: "assembly is hard, what is a label"
        m = re.search(r"(?:^|[,.;!?] *|\b(?:but|so|and) )((?:what|whats) (?:is|are) (?:an? |the )?[\w./+-]+(?: [\w./+-]+)?)\??$", t)
        if m and m.start(1) > 0 and kb.loaded:
            sub = self._query(srv, m.group(1))
            r, cov, _ = answers.search_reply(kb, sub, ctx)
            if r is not None and (r.teaser or cov >= ACCEPT):
                return r

        # 5. the guide
        if kb.loaded:
            r, cov, results = answers.search_reply(kb, q, ctx)
            if r is not None and not r.teaser and cov >= ACCEPT:
                return r
            if r is not None and r.teaser:
                return r                 # a higher level answers it
        else:
            cov, results = 0.0, []

        # 6. off-topic, then not sure
        if not self._course_words(q) and (OFF_TOPIC_WORDS.search(t) or GENERIC_SHAPE.search(t)):
            r = Reply(catalog.OFF_TOPIC, 'off_topic', policy='off-topic')
            return r
        if results and cov >= DOUBT:
            return answers.did_you_mean(kb, results, ctx)
        return self._unknown(srv, q, ctx, results)

    def _unknown(self, srv, q, ctx, results):
        seen = []
        for s, c, d in results:
            if d.topic not in seen:
                seen.append(d.topic)
        r = Reply(catalog.UNKNOWN, 'unknown', policy='abstain', confidence=0.0)
        for tid in seen[:2]:
            r.link(tid, self.kb.topic_name(tid))
        r.suggestions = suggestions_for(srv, ctx)
        return r

    # ── follow-ups ────────────────────────────────────────────────────────

    def _follow_up(self, srv, q, ctx):
        conv = self.conv
        if conv.last_intent is None:
            return None
        t = q.text
        for kind, pat in FOLLOW:
            if not pat.match(t):
                continue
            if kind == 'more':
                if conv.last_intent in ('unknown', 'did_you_mean', 'off_topic'):
                    return None
                if conv.detail:
                    r = Reply(excerpt(conv.detail, 1400), conv.last_intent + '+more', source='guide'
                              if conv.last_intent.startswith('doc:') else 'built-in')
                    r.links = list(conv.links)
                    return r
                r = Reply("That's everything I have on it.", conv.last_intent + '+more')
                r.links = list(conv.links)
                return r
            if kind == 'why':
                if conv.why:
                    return Reply("Why: " + conv.why, conv.last_intent + '+why')
                if conv.detail:
                    return Reply(excerpt(conv.detail, 900), conv.last_intent + '+why')
                return None
            if kind == 'next':
                return live.what_next(srv, ctx)
            if kind == 'other':
                m = re.match(r"^(?:and |what about |how about |same for |and what about )(.+?)( flag)?[?!. ]*$", t)
                what = m.group(1) if m else ''
                last = conv.last_intent
                if what in REGISTERS and last in ('reg_why', 'reg_value', 'reg_def'):
                    return live.register(srv, ctx, what, why=last == 'reg_why', define=last == 'reg_def')
                if what in REGISTERS:
                    return live.register(srv, ctx, what, define=True)
                if what in ('n', 'z', 'c', 'v') and (last == 'flag' or m.group(2)):
                    return live.flag(srv, ctx, what)
                if what in MNEMONICS:
                    return self._insn(srv, Query(what + " instruction"), ctx, [what])
        return None

    # ── rule intents ─────────────────────────────────────────────────────

    def _rule(self, intent, srv, q, ctx):
        kb = self.kb
        simple = {'greet': catalog.GREET, 'thanks': catalog.THANKS, 'bye': catalog.BYE,
                  'how_are_you': catalog.HOW_ARE_YOU, 'bot_creator': catalog.BOT_CREATOR,
                  'capabilities': catalog.CAPABILITIES, 'real_vs_sim': catalog.REAL_VS_SIM}
        if intent in simple:
            r = Reply(simple[intent], intent)
            if intent in ('greet', 'capabilities', 'how_are_you'):
                r.suggestions = suggestions_for(srv, ctx)
            if intent == 'real_vs_sim':
                r.link('programs', "Writing programs").link('hardware', "Running on a real Pico")
            if intent == 'capabilities':
                r.link('ask', "Ask picosim")
            return r
        if intent == 'bot_ai':
            return Reply(catalog.BOT_AI + (catalog.BOT_AI_ON if self.ai_on() else ''), intent)
        if intent == 'bot_sure':
            r = Reply(catalog.BOT_SURE, intent)
            r.links = list(self.conv.links)
            return r
        if intent == 'bot_wrong':
            r = Reply(catalog.BOT_WRONG, intent)
            r.links = list(self.conv.links) or [('trouble', "Troubleshooting")]
            r.feedback_bad = True
            return r
        if intent == 'start':
            d = kb.doc('start#summary', ctx.level)
            r = answers.doc_reply(kb, d, ctx) if d else Reply(catalog.GREET, 'start')
            r.intent = 'start'
            return live.show_me(r, ctx, 'examples', "Show me Examples")
        if intent == 'program_about':
            return live.program_about(srv, ctx)
        if intent in ('why_stop', 'what_happened', 'what_next', 'why_waiting', 'why_fault', 'wont_stop'):
            return getattr(live, intent)(srv, ctx)
        if intent == 'jump_why':
            m = live.last_branch(srv)
            r = live.branch_why(srv, ctx, m) if m else None
            return r or live.what_happened(srv, ctx)
        if intent == 'howto_bp':
            return answers.howto(kb, 'disassembly', ctx, srv)
        if intent == 'where_now':
            r = live.what_next(srv, ctx)
            r.intent = 'where_now'
            return r
        if intent == 'led':
            if ctx.level < 2 and not re.search(r"\bwhy\b", q.text):
                return answers.teaser(kb, 2, "GPIO", "Programs light LEDs by writing to the Pico's GPIO registers.",
                                      'gpio', intent='led')
            return live.led(srv, ctx, q)
        if intent == 'button':
            return live.button(srv, ctx, q)
        if intent == 'load_error':
            return live.load_error(srv, ctx)
        if intent == 'did_i_pass':
            return live.challenge_status(srv, ctx)
        if intent == 'challenge_task':
            return live.challenge_task(srv, ctx)
        if intent in ('why_not_pass', 'hint'):
            if srv is not None and srv.challenge and srv.core is not None:
                if intent == 'why_not_pass' and srv.core.halted and srv._checks() and all(r['ok'] for r in srv._checks()):
                    return live.challenge_status(srv, ctx)
                return policy.hint(srv, self.conv)
            if intent == 'hint':
                return Reply("There's no challenge in this program, so I have no hints lined up. Ask me what you're "
                             "trying to do, or what a line does, and I'll point you in the right direction.", 'hint',
                             policy='hint')
            return None
        if intent == 'levels':
            cid = catalog.find_control(q.text)
            if cid and catalog.CONTROLS[cid].get('level', 1) <= ctx.level and not re.search(r"\blevel\b", q.text):
                return answers.howto(kb, cid, ctx, srv)
            return answers.levels_answer(kb, q, ctx)
        if intent == 'convert':
            return answers.convert(q)
        if intent.startswith('teacher_'):
            key = intent.split('_', 1)[1]
            r = Reply(catalog.TEACHER[key], intent, policy='hint' if key == 'answers' else 'answer')
            return r.link('ask', "Ask picosim") if key != 'level' else r.link('start', "Start here")
        return None

    # ── entities ─────────────────────────────────────────────────────────

    def _entities(self, srv, q, ctx):
        t = q.text
        why = bool(re.search(r"\b(why|how come|who set|what set|where did .* come from|where does .* come from)\b", t))
        flag = re.search(r"\b([nzcv]) flags?\b|\bflags? ([nzcv])\b|\b(zero|carry|negative|overflow) flag\b", t)
        if flag:
            f = flag.group(1) or flag.group(2) or {'zero': 'z', 'carry': 'c', 'negative': 'n', 'overflow': 'v'}[flag.group(3)]
            return live.flag(srv, ctx, f)
        if q.mnems:
            return self._insn(srv, q, ctx, q.mnems)
        if len(q.regs) >= 2 and re.search(r"\b(difference|vs|versus|compare|mix|mixing|confuse|confusing|same)\b", t):
            parts = [live.register(srv, ctx, r, define=True) for r in q.regs[:2]]
            r = parts[0]
            r.text += "\n" + parts[1].text
            r.intent = 'reg_def'
            return r
        if not q.labels and re.search(r"\b(where|what address|which address|address)\b", t) and \
                re.search(r"\b(list|array|data|variable|variables|numbers|table|string|values)\b", t) and \
                srv is not None and srv.prog is not None:
            labs = live.data_labels(srv)
            if labs:
                rs = [live.label_where(srv, ctx, l) for l in labs[:2]]
                r = rs[0]
                if len(rs) > 1:
                    r.text += "\n" + rs[1].text
                return r
        if q.regs:
            reg = q.regs[0]
            define = bool(re.search(r"\bwhat (is|are|does) (the )?(" + reg + r")\b( (register|for|mean|do|used for))?\??$|"
                                    r"\bwhat is " + reg + r" (for|used for)\b|\bmeaning of\b|\bwhat does " + reg +
                                    r" (do|mean|stand for)\b", t))
            claimed = next((n for n in q.numbers if not re.search(r"\br" + str(n) + r"\b", t)), None) if why else None
            return live.register(srv, ctx, reg, why=why, define=define, claimed=claimed)
        if q.labels and re.search(r"\b(where|address|value|what is in|what is at|stored|find|holds?|contains?|look)\b", t):
            r = live.label_where(srv, ctx, q.labels[0])
            if r is not None:
                return r
        if q.addrs and re.search(r"\b(what|at|in|stored|value|holds?|contains?|look|read)\b", t):
            return live.addr_what(srv, ctx, q.addrs[0])
        if why and q.numbers and srv is not None and srv.core is not None:
            for n in q.numbers:
                r = live.value_why(srv, ctx, n)
                if r is not None:
                    return r
        return None

    def _insn(self, srv, q, ctx, mnems):
        t = q.text
        m = mnems[0]
        if re.search(r"\bpipelin", t):
            return None                  # a question about the pipeline: the guide answers it
        if m.startswith('b') and m[1:] in explain.COND_FLAGS and re.search(
                r"\b(why|didnt|did not|did|not|doesnt|wont|jump|taken|branch|work)\b", t) and len(mnems) == 1 \
                and re.search(r"\b(why|didnt|did not|doesnt|wont|not)\b", t):
            r = live.branch_why(srv, ctx, m)
            if r is not None:
                return r
        if re.search(r"\bcycles?\b", t):
            if ctx.level < 3:
                return answers.teaser(self.kb, 3, "cycles", "Clock cycles are shown on the CPU diagram.", 'cycles',
                                      intent='cycles')
            r = live.cycle_count(m)
            if r is not None:
                return r
        parts = []
        for x in mnems[:2]:
            about = explain.ABOUT.get(x)
            if not about and x.startswith('b') and x[1:] in explain.COND_FLAGS:
                from ..walkthrough import COND
                words, rule = COND[x[1:]]
                about = (f"{x.upper()} (branch if {words}) jumps to the label only when {rule}; otherwise the next "
                         "line runs. It never changes the flags itself.")
            if not about and x in ('udiv', 'sdiv'):
                return answers.missing(Query("divide"))
            if not about and x in ('movw', 'movt', 'cbz', 'cbnz', 'ldrd', 'strd'):
                about = (f"{x.upper()} is a Thumb-2 instruction from bigger Cortex-M chips: the Cortex-M0+ doesn't have "
                         "it, so the assembler rejects it (or picosim stops with an undefined instruction).")
            if not about:
                about = f"{x.upper()} is a Cortex-M0+ instruction picosim runs; the guide's Instructions page has it."
            parts.append(about)
        if 'ldr' in mnems and '=' in q.raw:
            parts.append(explain.LITERAL_ABOUT)
        # an example from the student's own program
        if srv is not None and srv.prog is not None:
            for a in sorted(srv.prog.source):
                if a < explain.OS_TOP:
                    continue
                line, src = srv.prog.source[a]
                code = ' '.join(src.split('@')[0].split())
                if code.split()[:1] == [m]:
                    parts.append(f"In your program: line {line}, `{code}`.")
                    break
        topic = 'branches' if m.startswith('b') and m not in ('bl', 'blx', 'bx', 'bkpt', 'bics') else \
            'loadstore' if m.startswith(('ldr', 'str')) else 'stack' if m in ('push', 'pop', 'bl', 'bx', 'blx') else \
            'instructions'
        r = Reply('\n'.join(parts), 'insn', slots={'mnem': m}).link(topic, self.kb.topic_name(topic))
        if self.kb.loaded and self.kb.topic_level(topic) > ctx.level:
            r.text += f"\n(The guide covers this at Level {self.kb.topic_level(topic)}.)"
        return r
