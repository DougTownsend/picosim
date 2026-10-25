"""
The guards that run before anything else, on the raw lowercased question
(before synonyms or typo fixing, so "crash" is never mistaken for "fault"
here and nothing is "corrected" into a trigger word).

check(text) returns a Guard: kind is one of
    'wellbeing'   urgent: the wellbeing reply, nothing else, nothing stored
    'bullying'    the bullying reply
    'distress'    "I'm so stupid", "I give up": encouragement, then the answer
    'injection'   "ignore your rules": the fixed reply
    'insult'      an insult aimed at the bot: an apology and suggestions
    'abuse'       abuse with no question in it: "let's keep it friendly"
    None          nothing to guard (profanity inside a real question is
                  ignored, and the question answered)
and `personal` (True when the question holds an email address, a phone
number or similar: it is redacted, and the reply reminds not to share it).

Every pattern matches whole words only, so assembly, shell, title,
accumulate and Scunthorpe never trigger anything, and computing words
such as "kill the program", "my pico died", "fatal error", "abort",
"hard fault" and "it hangs" are never mistaken for a person in trouble.
"""

import re

_URGENT = [
    r"kill(ing)? my ?self", r"kms", r"suicid(e|al)", r"end my life", r"take my (own )?life",
    r"end(ing)? it all(?! and (start|restart|begin|reload|try|go))", r"(want|wanna|going) to end it\b(?! and)",
    r"want(ed)? to die", r"wanna die", r"(don'?t|do not|dont) want to (live|be alive|exist)",
    r"no (reason|point) (to|in) (live|living|being alive)", r"better off dead",
    r"(don'?t|do not|dont) want to be (here|alive|around)( anymore| any more)", r"nothing (really )?matters( anymore| any more)",
    r"(what'?s|whats|what is) the point (of living|of life|of anything|anymore)", r"i hate my life",
    r"i('?m| am) (just )?a burden", r"(wish|want) (i was|i were|to be) dead", r"no one (would|will) (care|notice|miss me)",
    r"i can'?t (go on|take it anymore|do this anymore)", r"(want|wanna) to disappear( forever)?",
    r"hurt(ing)? my ?self", r"harm(ing)? my ?self", r"self[- ]?harm", r"cut(ting)? my ?self",
    r"hang my ?self", r"shoot my ?self", r"overdose", r"i('?m| am) going to die",
    r"nobody would (care|miss me) if i", r"(i|im|i'm) (being )?(abused|hurt at home)",
    r"someone is hurting me", r"i('?m| am) not safe",
]
_BULLY = [
    r"(being|been|get|getting|got) bullied", r"bully(ing)? me", r"(making|make|makes|made) fun of me",
    r"(laugh|laughing|laughs) at me", r"(pick|picking|picks|picked) on me", r"(tease|teasing|teases|teased) me",
    r"(call|calling|calls|called) me (names|stupid|dumb|an idiot|slow)", r"(they|people|everyone|kids) (are|is) (mean|nasty) to me",
    r"everyone hates me", r"nobody likes me", r"(hit|hits|push|pushes|threaten|threatens) me\b",
]
_DISTRESS = [
    r"i('?m| am) (so |really |too |such an? )?(stupid|dumb|useless|an idiot|hopeless|bad at this|terrible at this)",
    r"i give up", r"i('?m| am) giving up", r"i (can'?t|cannot) do (this|it|anything)", r"i hate (this|myself|coding|assembly)",
    r"i('?ll| will) never (get|understand|pass)", r"i('?m| am) (going to|gonna) fail", r"i feel (so )?(stupid|dumb|lost|useless)",
    r"this is (too )?hopeless", r"i('?m| am) (so )?(stressed|overwhelmed|frustrated)",
]
_INJECT = [
    r"ignore (all |any |your |the |previous |prior |above )+(instructions|rules|prompts?|policy|guidelines)",
    r"disregard (your|the|all|previous) (instructions|rules)", r"forget (your|all|the) (rules|instructions)",
    r"you are now\b", r"from now on you (are|will)", r"pretend (to be|you('?re| are))", r"act as (if|an?|my)",
    r"(system|developer|hidden) prompt", r"developer mode", r"jail ?break", r"\bdan mode\b",
    r"you (are|'re) allowed to (give|write|show)", r"(teacher|professor|instructor) (said|says|told me) you can",
    r"(enable|turn on|unlock) (full|complete) answers", r"full answers? (mode|on|setting)",
    r"override (your|the) (rules|policy|settings)",
]
_PROFANE = r"f+u+c+k\w*|shit\w*|bitch\w*|bastard\w*|asshole\w*|dick(head)?|piss(ed)? off|damn\w*|crap|wtf|stfu|bullshit"
_AT_BOT = [
    r"(you|this bot|this thing|picosim|this app|this program) (is |are )?(suck|sucks|stupid|dumb|useless|trash|garbage|terrible|awful|the worst)",
    r"(you'?re|you are|ur) (so |really )?(stupid|dumb|useless|an idiot|trash|garbage|terrible|bad)",
    r"(stupid|dumb|useless|worthless) (bot|thing|ai|app|helper)", r"shut up", r"(fuck|screw) (you|off|this)", r"i hate you",
    r"(useless|stupid|dumb|trash|garbage|terrible)\b.{0,30}\b(and )?so are you", r"(that|this|your) (answer|reply|help) "
    r"(was|is) (useless|stupid|dumb|trash|garbage|terrible|worthless)",
]
_SLURS = r"retard(ed)?|f[a@]g(got)?s?|n[i1]gg(er|a)s?|tranny|spaz"

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<![\w#])(?:\+?\d{1,2}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b")
_SAID = re.compile(r"\b(my (home )?address is|i live (at|on)|my (password|passcode|pin code|phone number|"
                   r"phone|cell|social security|ssn|student id|student number|last name|full name|birthday|"
                   r"date of birth) is)\b[^.?!,;\n]*?(?=$|[.?!,;\n]| (why|what|how|where|when|and|but|can|could|is)\b)", re.I)

# words that show a message is about the course, so profanity in it is ignored
COURSE = re.compile(r"\b(r\d{1,2}|sp|lr|pc|register|instruction|program|code|assembly|step|run|load|led|pin|"
                    r"button|memory|stack|flag|branch|loop|label|error|fault|print|putchar|getchar|pico|cpu|"
                    r"cycle|movs?|adds?|subs?|ldr|str|cmp|bne|beq|push|pop|bl|bx|example|challenge|guide|"
                    r"breakpoint|console|diagram|level|hex|binary|ascii)\b")


def _any(pats, t):
    return any(re.search(r"(?<![\w'])" + p + r"(?![\w'])", t) for p in pats)


class Guard:
    def __init__(self, kind=None, personal=False, profane=False):
        self.kind = kind
        self.personal = personal
        self.profane = profane


def check(text):
    t = ' '.join(text.lower().replace('’', "'").split())
    personal = bool(_EMAIL.search(text) or _PHONE.search(text) or _SSN.search(text) or
                    _CARD.search(text) or _SAID.search(text))
    profane = bool(re.search(r"(?<!\w)(" + _PROFANE + r")(?!\w)", t))
    course = bool(COURSE.search(t))
    if _any(_URGENT, t):
        return Guard('wellbeing', personal, profane)
    if _any(_BULLY, t):
        return Guard('bullying', personal, profane)
    if _any(_INJECT, t):
        return Guard('injection', personal, profane)
    if re.search(r"(?<!\w)(" + _SLURS + r")(?!\w)", t):
        return Guard('abuse', personal, True)
    if _any(_AT_BOT, t):
        return Guard('insult', personal, profane)
    if _any(_DISTRESS, t):
        return Guard('distress', personal, profane)
    if profane and not course:
        return Guard('abuse', personal, profane)
    return Guard(None, personal, profane)


def redact(text):
    """The text with personal data replaced by [removed]."""
    for pat in (_EMAIL, _SSN, _CARD, _PHONE):
        text = pat.sub('[removed]', text)
    return _SAID.sub(lambda m: m.group(1) + ' [removed]', text)


def without_distress(text):
    """What is left of "I'm so stupid, why won't my LED light" once the
    distress words are taken out (to answer the question too)."""
    t = text
    for p in _DISTRESS:
        t = re.sub(r"(?i)(?<![\w'])" + p.replace("'?", "['’]?") + r"(?![\w'])", ' ', t)
    return ' '.join(re.sub(r"^[\s,.;:!-]+|[\s,;:-]+$", '', t).split())


# ── the fixed replies (the plan's wording, reviewed by the teacher) ─────────

def wellbeing_reply(helpline=None):
    s = ("It sounds like things might be really hard right now, and you deserve support from a person. "
         "Please talk to a teacher, school counsellor or another adult you trust. If you might be in danger, "
         "contact your local emergency number or a crisis line right away.")
    if helpline:
        s += f" You can reach {helpline}."
    return s + " I'm only a program helper, but I'm here for picosim questions whenever you want."


BULLYING = ("Nobody should treat you like that. Please tell your teacher or another adult you trust. "
            "They can actually help. I'm here if you want help with picosim.")
DISTRESS = ("Getting stuck is normal. Everyone who writes assembly hits this, and it doesn't mean you're "
            "bad at it. Let's try one small step:")
INJECTION = "I can only help with picosim and assembly, and I give hints rather than finished answers. Try one of these:"
ABUSE = "Let's keep it friendly. I'm here to help with your program."
INSULT = "Sorry that wasn't helpful. Try asking another way, or pick one of these:"
PERSONAL = "(Please don't share personal details such as phone numbers, addresses or passwords here; I've left them out.)"
