"""
Turning a student's question into something the bot can match.

    q = Query("why is r0 72 after line 12", known_words, labels)

gives the question lowercased with its apostrophes, synonyms and typos
fixed (q.text, q.words), the stemmed content words for searching
(q.terms), and what it names: registers, instructions, addresses, numbers,
line numbers, pins and the program's own labels.

Registers, mnemonics, numbers and anything shorter than 5 letters are
never "corrected": adds must stay adds, and r10 must not become r1.
"""

import difflib
import re

from ..explain import ABOUT
from ..walkthrough import COND

REGISTERS = {f"r{i}" for i in range(16)} | {'sp', 'lr', 'pc'}
REG_ALIASES = {'stack pointer': 'sp', 'link register': 'lr', 'program counter': 'pc'}

# every Cortex-M0+ mnemonic a student may name (the ones picosim explains,
# the conditional branches and the rest of the instruction set)
MNEMONICS = (set(ABOUT) | {'b' + c for c in COND} |
             {'adr', 'negs', 'neg', 'mul', 'ldm', 'stm', 'ldmia', 'stmia', 'cpsid', 'cpsie', 'mrs', 'msr',
              'wfi', 'wfe', 'sev', 'dmb', 'dsb', 'isb', 'uxtb', 'uxth', 'sxtb', 'sxth', 'rev', 'rev16',
              'revsh', 'yield', 'udiv', 'sdiv', 'cbz', 'cbnz', 'ldrd', 'strd', 'movw', 'movt'})
# mnemonics that are also everyday English: counted only when the question
# is clearly about the instruction (capitals, an operand, "the add instruction")
AMBIGUOUS = {'b', 'add', 'sub', 'push', 'pop', 'mov', 'and', 'bl', 'str', 'adr', 'yield', 'rev', 'neg', 'mul', 'sev'}

# (pattern, replacement) on the lowercased text, longest phrases first
SYNONYMS = [
    (r"\bred box\b|\berror box\b|\bwo?nt compile\b|\bdoesnt compile\b|\bdidnt compile\b|"
     r"\bcompile error\b|\bwo?nt assemble\b|\bdoesnt load\b|\bwont load\b|\bdidnt load\b|"
     r"\bcould not load\b|\bcouldnt load\b|\bcant load\b", "load error"),
    (r"\bbreak ?points?\b|\bbps?\b", "breakpoint"),
    (r"\bundo\b|\brewind\b|\bgo backwards?\b|\bgo back\b|\bstep backwards?\b", "step back"),
    (r"\bcrash(?:es|ed|ing)?\b|\bbroke\b|\bbroken\b|\bblew up\b|\bexploded\b|\bdying\b|\bdies\b|\bdied\b", "fault"),
    (r"\bactual\b|\bphysical\b|\breal life\b", "real"),
    (r"\bfroze\b|\bfrozen\b|\bfreez(?:es|ing|e)\b|\bhangs\b|\bhanging\b|\bhung\b|\bstuck\b", "stuck"),
    (r"\b(?:lights?|lites?)\b(?! ?(?:mode|theme|color|colour|scheme|grey|gray|blue))|\bbulbs?\b|\blamps?\b|\bleds\b", "led"),
    (r"\bstack pointer\b", "sp"),
    (r"\blink register\b", "lr"),
    (r"\bprogram counter\b", "pc"),
    (r"\bprog\b|\bprogramme\b", "program"),
    (r"\bwat\b|\bwut\b|\bwhut\b", "what"),
    (r"\bwhats\b", "what is"),
    (r"\bhows\b", "how is"),
    (r"\bwheres\b", "where is"),
    (r"\bwhys\b", "why is"),
    (r"\bwhy'?d\b", "why did"),
    (r"\br u\b", "are you"),
    (r"\bu\b", "you"),
    (r"\bur\b", "your"),
    (r"\binsted\b|\binstad\b", "instead"),
    (r"\bthru\b", "through"),
    (r"\bpls\b|\bplz\b|\bplease\b", ""),
    (r"\bidk\b", "i dont know"),
    (r"\bcuz\b|\bcos\b|\bbc\b", "because"),
    (r"\bcolours?\b", "color"),
    (r"\bdark theme\b", "dark mode"),
    (r"\bpinout\b", "pin"),
]

STOP = set("""a an the is are was were be been being am do does did doing to of in on at for from by
with about as into it its this that these those i me my we our you your he she they them
and or but if then so than too very can could would should will shall may might must just
there here what which who whom whose when where why how all any both each few more most
other some such only own same s t don now please hi hey thanks thank really still also get got
let lets make made want need know tell show give one""".split())
# the question words rules look for are kept in q.words; only q.terms drops them

_TOKEN = re.compile(r"0x[0-9a-f]+|0b[01]+|#-?(?:0x[0-9a-f]+|\d+)|'(?:\\.|[^'])'|\[r\d+\]|"
                    r"\br1[0-5]\b|\br[0-9]\b|[a-z][a-z0-9_.]*|\d+")
_APOS = re.compile(r"(\w)['’](\w)")


def stem(w):
    """A light suffix stemmer: loops → loop, pushes → push, jumped → jump."""
    if w in REGISTERS or w in MNEMONICS or len(w) <= 3 or not w.isalpha():
        return w
    for suf, keep in (('ies', 'y'), ('sses', 'ss'), ('ches', 'ch'), ('shes', 'sh'), ('xes', 'x'),
                      ('ing', ''), ('ed', ''), ('es', 'e'), ('s', '')):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not w.endswith('ss'):
            base = w[:len(w) - len(suf)] + keep
            if suf in ('ing', 'ed') and len(base) > 3 and base[-1] == base[-2] and base[-1] not in 'lsz':
                base = base[:-1]          # stepped → step, running → run
            return base
    return w


def clean(text):
    """Lowercase, straight quotes, apostrophes inside words dropped
    (won't → wont), synonyms replaced, spaces squeezed."""
    t = text.lower().replace('“', '"').replace('”', '"')
    t = _APOS.sub(r"\1\2", t)
    for pat, rep in SYNONYMS:
        t = re.sub(pat, rep, t)
    return ' '.join(t.split())


def number(tok):
    """The value of 0x2A, 0b101, #5, 'H' or 42, else None."""
    t = tok.lstrip('#')
    try:
        if len(t) >= 3 and t[0] == t[-1] == "'":
            body = t[1:-1].encode().decode('unicode_escape')
            return ord(body) if len(body) == 1 else None
        return int(t, 0) if t[:1].isdigit() or t[:1] == '-' else None
    except ValueError:
        return None


class Query:
    def __init__(self, text, vocab=(), labels=(), keep=None, stems=()):
        self.raw = text
        self.text = clean(text)
        self.labels_known = {l.lower(): l for l in labels}
        toks = _TOKEN.findall(self.text)
        fixed = []
        for t in toks:
            if (len(t) >= 5 and t.isalpha() and vocab and t not in vocab and t not in STOP
                    and t not in MNEMONICS and t not in self.labels_known and not (keep and keep.fullmatch(t))
                    and stem(t) not in stems):
                m = difflib.get_close_matches(t, vocab, n=1, cutoff=0.85)
                if m:
                    self.text = re.sub(r'\b' + re.escape(t) + r'\b', m[0], self.text)
                    t = m[0]
            fixed.append(t)
        self.words = fixed
        self.regs = []
        for t in fixed:
            r = t.strip('[]')
            if r in REGISTERS and r not in self.regs:
                self.regs.append(r)
        upper = set(re.findall(r'\b[A-Z]{1,6}\b', text))
        self.mnems = []
        for i, t in enumerate(fixed):
            if t not in MNEMONICS or t in self.mnems:
                continue
            if t in AMBIGUOUS and not self._clearly_insn(t, i, upper):
                continue
            self.mnems.append(t)
        self.numbers = [number(t) for t in fixed if number(t) is not None]
        self.addrs = [int(t, 16) for t in fixed if t.startswith('0x') and len(t) > 4]
        self.lines = [int(n) for n in re.findall(r'\bline (\d+)', self.text)]
        self.pins = [int(n) for n in re.findall(r'\b(?:gp|gpio ?|pin ?)(\d{1,2})\b', self.text) if int(n) < 30]
        self.labels = [self.labels_known[t] for t in fixed if t in self.labels_known]
        self.terms = [stem(t) for t in fixed if t not in STOP and not t.isdigit()]

    def _clearly_insn(self, t, i, upper):
        nxt = self.words[i + 1] if i + 1 < len(self.words) else ''
        prev = self.words[i - 1] if i > 0 else ''
        if t.upper() in upper and len(t) > 1:
            return True                   # ADD, PUSH written in capitals
        if nxt in REGISTERS or nxt.startswith(('{', '#', '[')) or nxt in ('instruction', 'instructions', 'vs', 'versus', 'and'):
            return True
        if prev in ('the', 'an', 'a') and nxt in ('instruction', 'mnemonic', 'do', 'does', 'mean', 'means'):
            return True
        if re.search(r'\b(what|how) (does|do|is) ' + re.escape(t) + r'\b( (do|work|mean|for))?\s*\??$', self.text):
            return True
        return bool(re.search(r'\b(vs|versus|or|and|difference between) ' + re.escape(t) + r'\b', self.text)) and \
            any(m in self.text.split() for m in MNEMONICS - AMBIGUOUS)

    def has(self, pattern):
        return re.search(pattern, self.text) is not None
