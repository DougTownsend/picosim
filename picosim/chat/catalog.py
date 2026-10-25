"""
What Ask picosim knows how to answer without searching: the controls it
can explain and point at, the fixed replies, features picosim does not
have, and the rules that recognise each kind of question.

The controls' ids are the ones main.gd registers in ask_controls(), and a
test checks that every id and every guide topic named here exists.  Their
how-to text starts from the control's own tooltip (sent in the knowledge
pack), so the bot and the button never disagree.
"""

import re

# ── controls ─────────────────────────────────────────────────────────────────
# id: name shown, key, level it appears at, aliases (longest match wins),
# guide topic, extra sentence, why it may be missing although the level is reached

CONTROLS = {
    'step_insn': dict(name="Step instruction", key="S or F10", guide='running',
                      aliases=["step instruction", "step button", "step key", "s key", "next line", "one line at a time",
                               "one instruction at a time", "run one instruction", "run one line", "f10", "step"]),
    'step_back': dict(name="◀ Step back", key="B", guide='running',
                      aliases=["step back", "back button", "previous step", "b key", "previous line", "back a step"]),
    'step_over': dict(name="Step over", key="O", level=2, guide='running',
                      aliases=["step over", "skip over", "skip a call", "skip the call", "skip putchar", "skip a function",
                               "over a function", "over a call", "o key"]),
    'step_cycle': dict(name="Step cycle", key="C or F11", level=3, guide='cycles',
                       aliases=["step cycle", "one cycle at a time", "one clock cycle", "cycle by cycle", "c key", "f11"]),
    'run': dict(name="Run / Pause", key="Space or F5", guide='running',
                aliases=["run button", "pause button", "pause", "run my program", "run the program", "run it", "play button",
                         "space bar", "spacebar", "start running", "f5", "run", "go by itself", "by itself", "on its own",
                         "automatically", "without clicking step", "without pressing step", "all the way through",
                         "kill the program", "stop the program", "stop running", "kill it", "make it stop"]),
    'reload': dict(name="Reload", key="Ctrl/Cmd+R", guide='running',
                   aliases=["reload", "restart", "start over", "reset", "start again", "from the beginning", "begin again",
                            "run it again", "run again"]),
    'open': dict(name="Open…", key="Ctrl/Cmd+O", guide='programs',
                 aliases=["open a file", "open my file", "load a file", "load my file", "load my program", "my own program",
                          "my own file", "load a program", "open a program", "open my program", "open my own", "open"]),
    'examples': dict(name="Examples", guide='examples',
                     aliases=["examples menu", "example programs", "sample programs", "examples", "the example list"]),
    'speed': dict(name="Speed slider", guide='running',
                  extra="**Max** finishes almost at once; the starting speed, 8 cyc/s, runs about one instruction a "
                        "second so you can watch.",
                  aliases=["speed slider", "speed", "slider", "too slow", "go faster", "slow down", "go slower",
                           "speed up", "run faster", "run slower", "make it faster", "make it slower"]),
    'settings': dict(name="⚙ Settings", guide='programs',
                     hidden="It only appears when the loaded program has settings (lines such as "
                            "`.equ LED_PIN, 15 @ <- …`), like **Blink an LED**.",
                     aliases=["settings button", "program settings", "settings", "gear", "change the pin", "led pin setting"]),
    'diagram': dict(name="Show CPU Diagram", level=3, guide='diagram',
                    aliases=["show cpu diagram", "hide cpu diagram", "cpu diagram", "datapath diagram", "the diagram",
                             "see the cpu", "inside the cpu", "hide the diagram"]),
    'machine': dict(name="machine menu (Multi-cycle / 2-stage pipeline)", level=4, guide='pipeline',
                    aliases=["machine menu", "multi-cycle menu", "switch to the pipeline", "switch machine",
                             "pipeline view", "2-stage pipeline menu"]),
    'view': dict(name="View menu", guide='layout',
                 extra="It also has **Show the tour again** and the **Heat overlay on the CPU diagram** switch (on by "
                       "itself from Level 3).",
                 aliases=["view menu", "color scheme", "dark mode", "light mode", "colors", "color", "theme",
                          "heat overlay", "heat tint", "show the tour", "tour again", "replay the tour"]),
    'perf': dict(name="Performance", key="F2", level=4, guide='performance',
                 aliases=["performance window", "performance button", "f2"]),
    'level': dict(name="Level menu", guide='start',
                  aliases=["level menu", "change level", "change the level", "switch level", "level up", "next level",
                           "higher level", "lower level"]),
    'guide': dict(name="Guide", key="F1", guide='start',
                  aliases=["user guide", "guide", "manual", "documentation", "help page", "f1"]),
    'mode': dict(name="Simulator / Pico (USB serial) menu", level=2, guide='hardware',
                 aliases=["simulator menu", "mode menu", "pico mode", "usb serial mode", "real pico mode",
                          "simulator mode", "switch to simulator"]),
    'flash': dict(name="Flash", level=2, guide='hardware',
                  hidden="It appears in **Pico (USB serial)** mode: pick that in the mode menu (it says Simulator).",
                  aliases=["flash button", "flash", "upload", "put it on the pico", "copy it to the pico",
                           "run it on the pico", "run on a real pico", "load it onto the pico", "put my program on",
                           "run it on a real pico", "run it on my pico", "on a real pico", "onto my pico", "onto the pico",
                           "on my real pico", "on real hardware"]),
    'connect': dict(name="Connect", level=2, guide='hardware', hidden="It appears in **Pico (USB serial)** mode.",
                    aliases=["connect button", "serial port", "disconnect"]),
    'zoom': dict(name="Zoom (− / % / +)", key="Ctrl/Cmd + and −", guide='layout',
                 text="The − and + buttons in the toolbar zoom the whole window, as do Ctrl/Cmd + mouse wheel; "
                      "click the percentage to fit the window again.",
                 extra="Dragging the bars between the panels also makes a panel's text bigger.",
                 aliases=["zoom in", "zoom out", "zoom", "text size", "font size", "make text bigger", "make it bigger",
                          "make the text bigger", "too small", "tiny text", "cant read", "everything bigger", "so tiny",
                          "is tiny", "too tiny", "bigger text", "smaller text", "hard to read"]),
    'numfmt': dict(name="Hex / Decimal / Signed menu", guide='registers',
                   aliases=["hex menu", "show decimal", "see decimal", "in decimal instead", "number format",
                            "hex or decimal", "show hex", "see hex", "decimal view", "signed view", "numbers in decimal",
                            "values in decimal", "registers in decimal", "decimal instead", "as decimal", "in decimal"]),
    'disassembly': dict(name="Disassembly (breakpoints)", guide='memory',
                        extra="Click the first column of a row to put a red ● there: **Run** stops just before that "
                              "instruction. Click it again to remove it.",
                        aliases=["set a breakpoint", "remove a breakpoint", "breakpoint", "red dot", "stop at a line",
                                 "stop at line", "disassembly", "stop right at", "make it stop at", "pause at a line"]),
    'skip_main': dict(name="Skip to main ▸", level=2, guide='running',
                      hidden="It is shown only while the built-in OS's start-up is running (before main). At Level 1 "
                             "the OS is hidden anyway: programs start at main.",
                      aliases=["skip to main", "run to main", "skip the os", "skip os", "skip the start", "skip boot",
                               "skip startup"]),
    'memory_tab': dict(name="Memory tab", level=2, guide='memory',
                       aliases=["memory tab", "see memory", "view memory", "bytes in memory", "hex dump", "look at memory",
                                "see the stack", "view the stack"]),
    'explain': dict(name="Explain this step", guide='explain',
                    aliases=["explain this step", "explain panel", "explanation panel"]),
    'more_detail': dict(name="More detail", guide='explain',
                        aliases=["more detail box", "more detail checkbox", "tick more detail"]),
    'console': dict(name="Serial console", guide='serial',
                    extra="Click in it and type: each key goes to your program's getchar. **Clear** empties it.",
                    aliases=["serial console", "console", "type input", "type a key", "input box", "clear the console",
                             "clear button", "where does it print", "output window", "where does the output",
                             "where is the output", "see the output", "output show up", "where is the printed"]),
    'pico': dict(name="Raspberry Pi Pico board", level=2, guide='pico',
                 aliases=["pico board", "board drawing", "pin drawing", "the board", "the pins"]),
    'registers': dict(name="Registers panel", guide='registers', aliases=["registers panel", "register panel"]),
    'ask': dict(name="Ask", key="?", guide='ask', aliases=["ask button", "this chat", "ask picosim", "this helper", "chatbot"]),
}
_ALIASES = sorted(((a, cid) for cid, c in CONTROLS.items() for a in c['aliases']), key=lambda x: -len(x[0]))


def find_controls(text):
    """The controls a question names, longest alias first, each once."""
    out = []
    taken = []
    for a, cid in _ALIASES:
        for m in re.finditer(r'(?<![\w-])' + re.escape(a) + r'(?![\w-])', text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue                 # inside a longer alias already found
            taken.append((m.start(), m.end()))
            if cid not in out:
                out.append(cid)
    return out


def find_control(text):
    """The control a question names (the longest alias that appears), or None."""
    found = find_controls(text)
    return found[0] if found else None


# ── fixed replies ────────────────────────────────────────────────────────────

CAPABILITIES = (
    "I'm picosim's helper. Ask me in plain words about:\n"
    "• your program right now: *Why did it stop?* · *What just happened?* · *Why is R0 72?* · *Why isn't my LED on?*\n"
    "• picosim itself: *How do I set a breakpoint?* · *What does Step over do?*\n"
    "• assembly and the Pico: *What does LDR do?* · *What is the Z flag?* · *What is little-endian?*\n"
    "• errors and challenges: *What does this error mean?* · *Did I pass?* · *Give me a hint*\n"
    "I give hints, not finished answers, and I only know about picosim, ARM assembly and the Pico.")
GREET = "Hi! I'm picosim's helper. Ask me anything about your program, the buttons, or ARM assembly."
THANKS = "You're welcome! Ask again any time."
BYE = "Bye! Good luck with your program."
HOW_ARE_YOU = "I'm a program, so I'm always ready. How's your program going? Ask me if anything is confusing."
BOT_AI = ("I'm picosim's built-in helper: a program, not a person. My answers come from picosim's guide and from your "
          "program as it runs.")
BOT_AI_ON = (" Your teacher has also turned on AI help, so answers marked **AI** come from Claude, an AI made by "
             "Anthropic; check those against the guide if unsure.")
BOT_CREATOR = ("I'm part of picosim, the ARM assembly simulator your class uses. My answers are built from picosim's "
               "own user guide and from your program as it runs.")
BOT_SURE = ("My built-in answers come straight from picosim's guide and from your program as it runs, so they should "
            "match what you see on screen. If something looks wrong, check the guide page, or ask your teacher.")
BOT_WRONG = ("Sorry about that. Please check the guide page below, and tell your teacher: they can help, and they can "
             "pass it on so the answer gets fixed.")
OFF_TOPIC = ("I'm picosim's helper, so I can only answer questions about picosim, ARM assembly and how the Pico's "
             "processor works. Try asking: *Why did my program stop?* · *What does LDR do?* · *How do I set a "
             "breakpoint?* For anything else, your teacher is the best person to ask.")
UNKNOWN = ("I don't know that one. Try asking it another way: name the button, instruction or register you mean, or "
           "say what you see on the screen. Your teacher can help too.")
NOT_ENGLISH = ("I can only answer in English right now. Please ask in English, in simple words, for example: "
               "*What does Step do?*")
TOO_LONG = ("That's a lot of text for me. Ask one question at a time (up to {n} characters). If it's an error message, "
            "paste just the line that says Error.")
NO_HINTS_LEFT = ("That's all my hints. Try it, run it to the end, and ask me \"did I pass?\" If you're still stuck, ask "
                 "your teacher, or look up {concept} online.")

REAL_VS_SIM = (
    "Mostly, yes. **Like the real Pico:** the same Cortex-M0+ (Thumb) instructions, registers and flags, the same faults "
    "for bad addresses and unaligned loads, and the same GPIO registers (SIO, IO_BANK0, PADS_BANK0) your program writes. "
    "The same `.s` file runs on a real board: from Level 2, pick **Pico (USB serial)** and press **Flash**.\n"
    "**Simplified:** picosim has 64 KB of RAM from address 0 with a tiny built-in OS below `0x3000` (the real RP2040 "
    "keeps its RAM at `0x20000000` and runs programs from flash); there are no interrupts, timers or UART; and the "
    "multi-cycle CPU diagram is a teaching model. The 2-stage pipeline at Level 4 is the real chip's timing.")

TEACHER = {
    'ai': ("AI help is off unless your teacher sets it up. A teacher runs `picosim --chat-setup`, which says where the "
           "config file is and creates it, sets `\"ai\": \"on\"` there, and gives picosim an Anthropic API key in the "
           "`ANTHROPIC_API_KEY` environment variable. Questions the built-in answers can't handle then go to Claude."),
    'answers': ("No: I only ever give hints, never finished solutions, and there is no setting that changes that. If "
                "you need more help, ask your teacher, or look the topic up online."),
    'level': ("picosim has no level lock: the Level menu always belongs to the student. A teacher can start picosim at a "
              "level for one run by giving Godot `--level=N` after its `--` (it is not saved), and tell the class which "
              "level to use."),
    'log': ("Teachers can set `\"log_unanswered\": true` in the chat config (`picosim --chat-setup` shows where it is) to "
            "keep a local, redacted list of questions I couldn't answer, and **Report** on any answer saves it for them."),
}

# features picosim does not simulate: (pattern, name, what to do instead)
NOT_BUILT = [
    (r"\binterrupts?\b|\bnvic\b|\birqs?\b|\bisrs?\b|\bexceptions?\b|\bhandlers?\b|\bsystick\b",
     "interrupts and exceptions (NVIC, SysTick)",
     "picosim runs one program from `main` with no interrupts, so programs **poll**: they read an input over and over "
     "in a loop, as **Buttons and LEDs** does."),
    (r"\btimers?\b|\bsleep_ms\b|\bmilliseconds?\b|\bdelay ?ms\b|\breal time\b|\bclock time\b",
     "the RP2040's timer",
     "Use a delay loop that counts a register down to 0, as **Blink an LED** does."),
    (r"\buart\b|\btx pin\b|\brx pin\b|\bbaud\b",
     "the UART peripheral",
     "The serial console uses `putchar` and `getchar`, which call picosim's built-in OS instead."),
    (r"\bpio\b|\bstate machines? (in|on) the pico\b", "PIO (programmable I/O)", "Drive pins from your program with the SIO registers."),
    (r"\badc\b|\banalog\w*\b|\bpotentiometer\b|\btemperature sensor\b", "the ADC (analog inputs)",
     "Pins in picosim are digital: 0 or 1."),
    (r"\bpwm\b|\bservo\b|\bdim(ming)? (the|an?) led\b|\bbrightness\b", "PWM",
     "You can still blink a pin quickly from a loop to dim an LED on a real board."),
    (r"\bi2c\b|\bspi\b|\bdma\b|\bwatchdog\b|\brtc\b|\busb device\b", "that peripheral",
     "picosim simulates the CPU, memory and the GPIO pins (SIO, IO_BANK0, PADS_BANK0)."),
    (r"\bsecond core\b|\bcore ?1\b|\bmulti ?core\b|\bdual ?core\b|\bboth cores\b|\bmultithread\w*\b", "the second core",
     "picosim runs one core, core 0."),
    (r"\b0x20000000\b|\b0x10000000\b|\bxip\b|\breal memory map\b|\brp2040 memory map\b|\bsram at\b",
     "the RP2040's real memory map",
     "picosim uses a simplified map: RAM at `0x0000`–`0xFFFF` with your program from `0x3000`. The real RP2040 has "
     "its SRAM at `0x20000000` and runs from flash at `0x10000000`; the GPIO registers are at their real addresses."),
]
C_LANG = (r"\bin c\b|\bc code\b|\bc program\b|\bc language\b|\bprintf\b|\bint main\b|\bwrite (it )?in c\b|\bc\+\+\b|"
          r"\bpython\b|\bmicropython\b|\barduino\b|\bjava\b")
C_REPLY = ("picosim runs **assembly only**: you write the CPU's own instructions, one per line. C (or Python) on a Pico "
           "is compiled or interpreted down to these same Thumb instructions, so what you learn here is what C turns "
           "into.")
CHIP_LACKS = [
    (r"\bdivid\w*\b|\bdivision\b|\budiv\b|\bsdiv\b|\bmodulo\b|\bremainder\b|\bmod\b",
     "The Cortex-M0+ has **no divide instruction** (UDIV and SDIV are on bigger Cortex-M chips), so the assembler "
     "rejects them. Divide by a power of 2 with a shift (`lsrs r0, r0, #1` halves an unsigned number), or subtract in "
     "a loop and count how many times it fits. (The RP2040 adds a hardware divider in its SIO block; picosim doesn't "
     "simulate it.)"),
    (r"\bfloat\w*\b|\bfpu\b|\bfractions?\b|\bdecimal point\b|\b\d+\.\d+\b|\breal numbers?\b",
     "The Cortex-M0+ has **no floating-point unit**: registers hold whole numbers only. Programs use integers, or "
     "\"fixed point\": store tenths, so 2.5 is kept as 25."),
]

# which level shows what
FEATURE_LEVEL = [
    (r"\bpipeline\b|\bperformance\b|\bcpi\b|\bheat ?map\b|\bthroughput\b|\b2-stage\b", 4),
    (r"\bcpu diagram\b|\bdiagram\b|\bdatapath\b|\bclock cycles?\b|\bstep cycle\b|\bfsm\b|\bcontrol signals?\b|"
     r"\binside the cpu\b|\bmar\b|\bmdr\b", 3),
    (r"\bmemory tab\b|\bstack\b|\bpins?\b|\bboard\b|\bgpio\b|\bstep over\b|\bpico mode\b|\bflash\b|\bthe os\b", 2),
]

OFF_TOPIC_WORDS = re.compile(
    r"\b(weather|football|soccer|basketball|baseball|hockey|nba|nfl|movies?|films?|netflix|tv shows?|songs?|music|"
    r"singers?|rappers?|fortnite|minecraft|roblox|video games?|tiktok|instagram|snapchat|youtube|girlfriend|boyfriend|"
    r"crush|dating|date me|marry me|love life|recipes?|cook\w*|pizza|food|lunch|dinner|breakfast|president|election|"
    r"politics|war|religion|horoscope|zodiac|stocks?|bitcoin|crypto|cars?|pets?|dogs?|cats?|celebrit\w+|"
    r"taylor swift|capital of|translate|essay|poem|homework for|history|chemistry|biology|geography|calculus|algebra|"
    r"shakespeare|novel|vacation|holiday|birthday|horse|anime|fashion|makeup)\b")
GENERIC_SHAPE = re.compile(
    r"^(who (is|was|won|invented)|what is the capital|when (did|was|is)|how old (is|are)|tell me a (joke|story|riddle)|"
    r"write (me )?(an? )?(essay|poem|story|song|letter)|what time is it|what is the date|do you like|"
    r"what is your favou?rite|can you (sing|dance|draw)|how tall|how far|what should i eat|whats the score)")

# words that make a question picosim's business (with registers, mnemonics,
# numbers, labels and control names, which the router adds)
COURSE_WORDS = set("""picosim program programs code assembly assembler register registers instruction instructions
cpu processor pico rp2040 cortex m0 memory stack flag flags branch branches loop loops label labels error errors fault
faults print prints printing putchar getchar char character ascii hex hexadecimal binary decimal bit bits byte bytes word
words halfword address addresses pointer led leds pin pins gpio button buttons breakpoint breakpoints step steps run
runs reload load loads store stores challenge challenges example examples guide level levels console serial
diagram datapath cycle cycles pipeline alu bus mux fsm state states signal signals compile assemble thumb arm
function functions call calls return returns push pop lr sp pc stack overflow carry zero negative
signed unsigned immediate operand operands mnemonic directive syntax endian little big flash bootsel uf2 usb
subroutine variable variables constant constants number numbers count counter compare comparison jump jumps
os boot main halt halted waiting input output prints mask shift shifts logic computer computers""".split())


# ── intent rules ─────────────────────────────────────────────────────────────
# (intent, weight, pattern) on the cleaned question text (normalize.clean).

RULES = [
    ('greet', 1.0, r"^(hi|hello|hey|hiya|yo|howdy|sup|good (morning|afternoon|evening))( there)?( picosim| bot| helper)?[!. ]*$"),
    ('thanks', 1.0, r"^(thanks|thank you|thx|ty|cheers|thank you so much|thanks a lot|great thanks|ok thanks|perfect)[!. ]*$"),
    ('bye', 1.0, r"^(bye|goodbye|see you|see ya|cya|good night|gotta go)[!. ]*$"),
    ('how_are_you', 1.0, r"^(how are you|how is it going|how are you doing)( today)?\??$"),
    ('bot_ai', 0.95, r"\b(are you|is this) (an? )?(ai|robot|bot|chatgpt|gpt|claude|human|real|person|real person|computer)\b"),
    ('bot_creator', 0.95, r"\bwho (made|created|built|wrote|programmed|designed) you\b|^who are you\??$|^what are you\??$"),
    ('bot_sure', 0.9, r"^(are you sure|is that (right|correct|true)|really|you sure|for real)\??!?$"),
    ('bot_wrong', 0.95, r"\b(that is|thats|you are|youre|this is) (wrong|incorrect|not right|not true|a wrong answer)\b|"
                        r"\bwrong answer\b|\bthat (did not|didnt) help\b|\bnot helpful\b|\bthat is not what i asked\b"),
    ('capabilities', 1.0, r"^(help|help me|\?+|what can you do|what can i ask( you)?|what do you do|how do you work|"
                          r"how can you help( me)?|what do you know|menu|options)[?!. ]*$"),
    ('start', 0.9, r"\bhow (do|should|can) i (start|begin|get started|use (this|picosim|the app|this app|it))\b|"
                   r"^what (do|should) i do( first| now)?\??$|\bwhere (do|should) i (start|begin)\b|"
                   r"\bwhat is (this app|picosim|this thing|this software)\b|\bhow does (picosim|this app) work\b|"
                   r"\bget started\b|\bim new\b|\bfirst time\b|\bmissed the first class\b|\bwhat do i (even )?do (first|now)\b|"
                   r"\bjust opened (this|it|picosim)\b|\bwhere do i even (start|begin)\b"),
    ('program_about', 0.85, r"\bwhat (does|is) (this|my|the) (program|example|file) (do|doing|for|about)\b|"
                            r"\bwhat is this (program|example)\b"),
    ('why_stop', 0.95, r"\bwhy (did|has|does|is) (it|my program|the program|this|my code)? ?(stop|stopped|halt|halted|end|"
                       r"ended|finish|finished|pause|paused|quit)\b|\bwhy (it )?(stopped|halted)\b|\bwhat stopped\b|"
                       r"\bwhy (wont|cant|can i not) i (step|press step|run)\b|\b(step|run)( button)? (does nothing|doesnt "
                       r"work|is grey|is greyed|is disabled|wont work|isnt working|not working|greyed out)\b|"
                       r"\bnothing happens when i (press|click)\b|\bwhy is (step|run) (grey|greyed|disabled)\b|"
                       r"\bwhy (is it|its) halted\b|\bwhat does halted mean\b|\bhalted\b|"
                       r"\b(wont|doesnt|will not|does not|cant) (run|step|start|go)\b"),
    ('jump_why', 0.93, r"\bwhy (did|does|is) (it|the program|my program) (jump|jumping|go|going|branch|loop|skip|skipping)"
                       r"( back| up| over| ahead)?\b|\binstead of going (down|on) to the next line\b|"
                       r"\bwhy (did|does) it (jump|branch|skip)\b"),
    ('howto_bp', 0.9, r"\b(stop|pause|halt) (right )?(at|on|before) (the |a |that |this |my )?(\w+ )?(line|instruction)\b|"
                      r"\bmake it stop (right )?at\b"),
    ('what_happened', 0.92, r"\bwhat (just )?happened\b|\bwhat did (that|this|it) (just )?do\b|"
                            r"\bwhat (did|does) (that|this|the last) (line|instruction|step) (just )?do\b|"
                            r"\bexplain (this|that|the last|the) step\b|\bwhat (just )?changed\b|\bwhat did the step do\b"),
    ('what_next', 0.92, r"\bwhat (happens|will happen|comes|is coming|runs) next\b|\bwhat will (this|it|the next line|the "
                        r"next instruction) do\b|\bwhat (does|will) the next (line|instruction|step) do\b|"
                        r"\bwhere (does|will) it go next\b|\bwhat is next\b|\bnext (line|instruction|step)\b"),
    ('why_waiting', 0.93, r"\bwhy (is it|its|is my program|is the program) (waiting|stuck|paused)\b|\bwaiting for (input|a key)\b|"
                          r"\bwhat is it waiting for\b|\b(it|its|it is|my program|my program is|the program is|the program|"
                          r"everything is|everything) stuck\b|\bwhy (does|did) it wait\b"),
    ('why_fault', 0.95, r"\bwhy did (it|my program|the program) (fault)\b|\bcpu fault\b|\bbus fault\b|\bhard ?fault\b|"
                        r"\bundefined instruction\b|\bunaligned\b|\bwhat (does|is) (the|this|a) fault\b|\bfault mean\b|"
                        r"\bbit 0 clear\b|\b(it|my program) (just )?fault(ed)?\b|\bwhy (the|a) fault\b|\bwhat is a fault\b|"
                        r"\bfault\b"),
    ('wont_stop', 0.93, r"\b(wont|doesnt|will not|does not|cant|never) (stop|end|finish|halt)\b|\bnever (stops|ends|finishes|"
                        r"halts)\b|\bkeeps (running|going|looping)\b|\binfinite loop\b|\bruns forever\b|"
                        r"\bstuck (in|on) (a|the) (loop|bx lr)\b|\b(loops|looping|runs|running|goes|going|keeps going) forever\b|"
                        r"\bkeeps looping\b|\bnever ends\b"),
    ('where_now', 0.85, r"\bwhere (am i|is it|is the program|is my program)( now)?\b|\bwhich line (am i on|is it on|runs next|is next)\b|"
                        r"\bwhat line (is it on|am i on)\b"),
    ('led', 0.95, r"\bled\b.*\b(not|wont|doesnt|isnt|off|never|dark|no|cant|why|dim)\b|\b(why|how come)\b.*\bled\b|"
                  r"\b(gp|gpio ?|pin ?)\d{1,2}\b.*\b(not|wont|doesnt|isnt|off|never|why|stays|low|high)\b|"
                  r"\bwhy (is|does|wont|doesnt|isnt) (gp|gpio ?|pin ?)\d{1,2}\b|"
                  r"\bled (is )?not (on|working|blinking)\b|\bwont blink\b|\bnot blinking\b|\bturn on (the|my|an) led\b|"
                  r"^(my |the )?led\??$|\b(which|what) pin\b.*\bled\b|\bled pin\b|\bled\b.*\b(which|what) pin\b|"
                  r"\blight up\b"),
    ('button', 0.9, r"\bbuttons? (does|do|is|are)?(nt| not)? ?(work|working|respond|do anything|nothing)\b|"
                    r"\bpress (the|a) button\b|\bclick (a|the) pin\b|\bpin (will not|wont|doesnt) respond\b|"
                    r"\bhow do i (press|use) (a|the) button\b|\bread (a|the) button\b"),
    ('load_error', 0.95, r"\bload error\b|\b(wont|doesnt|didnt|cant|will not|does not|did not) (it |my program |the program |"
                         r"this |my file |my code )?(load|assemble|compile|open|build)\b|\berror (message|says|said)\b|\bwhat does (this|the|my) error\b|\bwidth suffix\b|"
                         r"\bout of range\b|\bundefined reference\b|\bbad instruction\b|\bsyntax error\b|\bassembler error\b|"
                         r"\bbad arguments\b|\bgarbage following\b|\blo register required\b|\bmy error\b|"
                         r"\b\.s:\d+:|\berror:"),
    ('did_i_pass', 0.95, r"\bdid i (pass|win|do it|get it|finish it|complete it|beat it)\b|\b(am i|is it) (done|right|correct)\b|"
                         r"\bcheck my (answer|work)\b|\bdid (it|my program) (pass|work)\b|\bhave i passed\b"),
    ('challenge_task', 0.92, r"\bwhat is the challenge\b|\bwhat (do|am) i (supposed|meant) to do\b|\bwhat is my task\b|"
                             r"\bwhat does the challenge (want|say|mean)\b|\bexplain the challenge\b"),
    ('why_not_pass', 0.95, r"\bwhy (didnt|did not|dont|do not|havent|cant|isnt|wont) (i|it|my program|my answer)? ?(pass|work|"
                           r"turn green|count)\b|\bwhy (is it|its|is the strip) (still )?(red|not green|failing)\b|"
                           r"\bwhat is wrong with my (answer|program|code)\b|\bwhy (did|does) (it|the check) fail\b"),
    ('hint', 0.95, r"\b(give me |can i (have|get) |i need |i want )?(a |another |one more |more |next )?hints?\b|"
                   r"\bhelp (me )?with the challenge\b|\bstuck on the challenge\b|\bnudge\b"),
    ('levels', 0.9, r"\blevel [1-4]\b|\bwhat (is|are) (the )?levels?\b|\bwhich level\b|\bhow do i (see|get|unlock|show|turn on) "
                    r"(the )?(cpu|diagram|datapath|memory tab|pipeline|pins|board|performance|step over|heat map)\b|"
                    r"\bunlock\b|\bwhat level\b|\bwhat does each level\b"),
    ('real_vs_sim', 0.92, r"\b(same as|like|different from|differ from) (what )?(a |the |my )?real (pico|chip|board|hardware|rp2040)\b|"
                          r"\breal (pico|chip) would\b|"
                          r"\b(work|run) on (a |the )?(real )?(pico|hardware|board)\b|\bhow (accurate|realistic|real)\b|"
                          r"\bis (this|picosim) (accurate|realistic|the real thing|a real)\b|\breal (pico|hardware) vs\b"),
    ('teacher_ai', 0.92, r"\b(turn|switch) on (the )?ai\b|\benable (the )?ai\b|\bai (mode|help|helper)\b|\bapi key\b|\bclaude\b.*\b(set ?up|on)\b|"
                         r"\bchat ?setup\b|\bchat\.json\b"),
    ('teacher_answers', 0.95, r"\ballow (full )?answers\b|\b(give|show) (full|complete|whole) (answers|solutions)\b|"
                              r"\bfull answers?\b|\bsolution mode\b|\bteacher setting\b"),
    ('teacher_level', 0.92, r"\block (the )?level\b|\bforce (the )?level\b|\bstop students (changing|from changing)\b"),
    ('teacher_log', 0.9, r"\bunanswered questions\b|\blog (of )?questions\b|\breport (this|an) answer\b"),
    ('convert', 0.95, r"(0x[0-9a-f]+|0b[01]+|\b\d+\b|'.') (in|to|into|as) (decimal|dec|hex|hexadecimal|binary|bin|ascii|a "
                      r"character|a char|char)\b|\b(binary|hex|hexadecimal|decimal|ascii)( code| value)? (of|for) "
                      r"(the (number|letter|character) )?('.'|0x[0-9a-f]+|0b[01]+|\d+|[a-z])\b|^what is (0x[0-9a-f]+|0b[01]+)\??$|"
                      r"\bconvert (0x[0-9a-f]+|0b[01]+|\d+)\b"),
]
RULES = [(i, w, re.compile(p)) for i, w, p in RULES]

# phrasings that one guide section answers exactly: (pattern, document id);
# a test checks that every id is in the knowledge pack
DOC_RULES = [
    (r"\b(nothing|no output|doesnt|wont|isnt|not|dont) (print|prints|appear|appears|show|shows|showing|printing)( up)?\b.*"
     r"\b(pico|board)\b|\b(pico|board)\b.*\bnothing (prints|appears|shows)\b|\bno output\b.*\b(pico|board)\b",
     'trouble#nothing-appears-in-the-console-after-fla'),
    (r"\bports?\b.*\b(not found|missing|cant find|not there|doesnt show|isnt there|not showing)\b|\bno (serial |usb )?ports?\b|"
     r"\bcant find (the |a |my )?(serial |usb )?port\b", 'trouble#nothing-appears-in-the-console-after-fla'),
    (r"\b(strange|weird|odd|wrong|funny|random|garbage|gibberish|funky) (characters?|chars?|symbols?|letters?|output)\b|"
     r"\bprints? (a |an )?(symbol|box|garbage|nonsense|weird|wrong|strange)\b|\bprinted (a |an )?(symbol|box|garbage)\b",
     'trouble#it-prints-a-strange-character-instead-of'),
    (r"\b(write|make|add|create|set ?up) (a |my own |your own |own |new |an )?challenges?\b",
     'challenges#writing-your-own-for-teachers'),
    (r"\bflash (failed|fails|didnt work|doesnt work|error|not working)\b|\bflashing (failed|fails|doesnt work)\b",
     'trouble#flash-failed'),
    (r"\b(cant|cannot|can not) find (a |the )?(button|panel)\b", 'trouble#i-can-t-find-a-button-or-panel-from-the-'),
    (r"\bwhy (is|does|are) (the |a )?(2-stage |two stage |2 stage )?pipelin\w* (faster|quicker|better|so fast|take fewer)\b|"
     r"\bpipelin\w*\b.*\b(fewer|less) cycles\b|\bhow (is|does) (the |a )?pipelin\w* (faster|speed)", 'pipeintro#summary'),
    (r"\bcode i (didnt|did not) write\b|\bended up in (the )?os\b", 'trouble#i-pressed-step-and-ended-up-in-code-i-di'),
    (r"\b(r[0-3]|value|register) (changed|got overwritten|changes|was overwritten)\b.*\b(putchar|bl|call|function)\b",
     'trouble#a-value-i-saved-in-r0-r3-changed-after-b'),
]
DOC_RULES = [(re.compile(p), d) for p, d in DOC_RULES]

# follow-ups that only make sense after another answer
FOLLOW = [
    ('more', re.compile(r"^(more( detail)?|tell me more|explain more|go on|more please|details?|elaborate|explain( that)?|"
                        r"and\??|explain further|in more detail|full details)[?!. ]*$")),
    ('why', re.compile(r"^(why|but why|how come|why is that|why does it|why that)[?!. ]*$")),
    ('next', re.compile(r"^(and )?(the |what about the )?next (line|one|instruction|step)[?!. ]*$|^(and )?then( what)?\??$|"
                        r"^what next\??$")),
    ('other', re.compile(r"^(and |what about |how about |same for |and what about )(r1[0-5]|r[0-9]|sp|lr|pc|n|z|c|v|"
                         r"line \d+|[a-z]{1,6})( flag)?[?!. ]*$")),
]
