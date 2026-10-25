"""
One answer from Ask picosim, as the server sends it to the GUI.

text uses a small markup that chat_panel.gd turns into coloured BBCode:
**bold**, `code`, lines starting with "• ", and [guide:id|label] (or
[guide:id]) for a link that opens a guide page.
"""


class Reply:
    def __init__(self, text, intent, policy='answer', source='built-in', links=None, actions=None,
                 suggestions=None, detail='', why='', confidence=1.0, slots=None, ai_offer=False):
        self.text = text
        self.intent = intent            # what the bot took the question to be (tests, eval)
        self.policy = policy            # answer, hint, off-topic, safety or abstain
        self.source = source            # built-in, guide or ai
        self.links = links or []        # [(guide topic id, label)]
        self.actions = actions or []    # [(kind, arg, label)]: spotlight, open_guide, memory_tab, set_level, open_examples
        self.suggestions = suggestions or []   # chips: a question string, or {'label', 'ask'} / {'label', 'doc'}
        self.detail = detail            # what "more detail" / "tell me more" shows next
        self.why = why                  # what a bare "why?" shows next
        self.confidence = confidence
        self.slots = slots or {}        # entities the answer was about (for follow-ups)
        self.ai_offer = ai_offer        # offer "Ask the AI helper" (when AI is on)
        self.escalate = False           # the built-in answers gave up: Claude may answer (AI on)
        self.teaser = False             # "that's part of Level N"
        self.feedback_bad = False       # "that's wrong": counted as a 👎

    def link(self, tid, label):
        if tid and all(t != tid for t, _ in self.links):
            self.links.append((tid, label))
        return self

    def act(self, kind, arg, label):
        self.actions.append((kind, arg, label))
        return self

    def to_msg(self, qid):
        chips = [s if isinstance(s, dict) else {'label': s, 'ask': s} for s in self.suggestions[:4]]
        return {'type': 'chat_reply', 'id': qid, 'text': self.text,
                'links': [{'id': t, 'label': l} for t, l in self.links[:4]],
                'actions': [{'kind': k, 'arg': a, 'label': l} for k, a, l in self.actions[:3]],
                'suggestions': chips, 'source': self.source, 'intent': self.intent, 'policy': self.policy,
                'more': bool(self.detail), 'ai_offer': self.ai_offer}
