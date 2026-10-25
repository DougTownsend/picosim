"""
What the bot remembers between questions, so follow-ups work:

    "why is r0 72?"  →  "what about r1?"     (same question, another register)
    "what does ldr do?"  →  "tell me more"   (the longer version)
    "what happens next?"  →  "why?"          (the reason it gave)

Loading a program marks the memory stale rather than clearing it: the hint
count for each challenge survives a reload (so "another hint" moves on),
but a follow-up about a register no longer means the old program's.
"""


class Conversation:
    def __init__(self):
        self.last_intent = None
        self.slots = {}
        self.detail = ''           # what "more detail" shows
        self.why = ''              # what "why?" shows
        self.links = []
        self.hint_count = {}       # (program path, challenge task) → hints given
        self.stale = False
        self.turns = []            # [(question, answer text)] for Claude mode, the last few
        self.unanswered = 0

    def remember(self, question, reply):
        if reply.policy == 'safety':
            return                 # never kept
        self.last_intent = reply.intent
        self.slots = dict(reply.slots)
        self.detail = reply.detail
        self.why = reply.why
        self.links = list(reply.links)
        self.stale = False
        self.turns.append((question, reply.text))
        del self.turns[:-6]

    def program_loaded(self):
        self.stale = True
        self.slots = {}

    def reset(self):
        self.__init__()
