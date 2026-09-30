extends Node
## User guide window: a list of topics on the left, the selected topic on
## the right.  Opened from the toolbar's "Guide" button or with F1.
##
## Self-contained: main.gd only creates it if this file exists, so deleting
## guide.gd removes the Guide button and nothing else.  The "CPU diagram
## components" topic is built from component_info.gd when that file exists.

const INFO_PATH := "res://scripts/component_info.gd"
const ACCENT := "#61afef"
const DIM := "#7f8794"
const KEY := "#e5c07b"

var win: Window
var topics: ItemList
var body: RichTextLabel
var _ids: Array = []
var _text := {}


func _ready() -> void:
	win = Window.new()
	win.title = "picosim — User Guide"
	win.visible = false
	win.transient = true
	win.min_size = Vector2i(560, 360)
	win.close_requested.connect(win.hide)
	win.window_input.connect(_on_window_input)
	add_child(win)

	var bg := PanelContainer.new()
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color("1b1e24")
	sb.content_margin_left = 10
	sb.content_margin_right = 10
	sb.content_margin_top = 10
	sb.content_margin_bottom = 10
	bg.add_theme_stylebox_override("panel", sb)
	win.add_child(bg)

	var split := HSplitContainer.new()
	bg.add_child(split)
	topics = ItemList.new()
	topics.custom_minimum_size.x = 210
	topics.add_theme_font_size_override("font_size", 14)
	topics.item_selected.connect(func(i): _show(_ids[i]))
	split.add_child(topics)

	body = RichTextLabel.new()
	body.bbcode_enabled = true
	body.selection_enabled = true
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_theme_font_override("mono_font", Backend.mono_font())
	body.add_theme_font_size_override("normal_font_size", 15)
	body.add_theme_font_size_override("bold_font_size", 15)
	body.add_theme_font_size_override("mono_font_size", 14)
	body.meta_clicked.connect(func(m): open(str(m)))
	var bsb := StyleBoxFlat.new()
	bsb.bg_color = Color("22262e")
	bsb.set_corner_radius_all(6)
	bsb.content_margin_left = 16
	bsb.content_margin_right = 16
	bsb.content_margin_top = 12
	bsb.content_margin_bottom = 12
	body.add_theme_stylebox_override("normal", bsb)
	split.add_child(body)

	_add_topics()
	topics.select(0)
	_show(_ids[0])
	var args := OS.get_cmdline_user_args()
	var i := args.find("--guide")
	if i >= 0:
		open.call_deferred(args[i + 1] if i + 1 < args.size() else "")


## Show the guide, optionally at a topic id.
func open(topic := "") -> void:
	if topic != "" and _text.has(topic):
		topics.select(_ids.find(topic))
		_show(topic)
	var area := get_viewport().get_visible_rect().size
	win.popup_centered(Vector2i(mini(int(area.x * 0.8), 1100), int(area.y * 0.85)))


func _unhandled_key_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k and k.pressed and not k.echo and k.keycode == KEY_F1:
		open()
		get_viewport().set_input_as_handled()


func _on_window_input(event: InputEvent) -> void:
	# Esc (or F1 again) closes the guide while it has focus.
	var k := event as InputEventKey
	if k and k.pressed and not k.echo and k.keycode in [KEY_ESCAPE, KEY_F1]:
		win.hide()
		win.set_input_as_handled()


func _show(id: String) -> void:
	body.text = _text[id]
	body.scroll_to_line(0)


func _topic(id: String, name: String, bb: String) -> void:
	_ids.append(id)
	_text[id] = bb
	topics.add_item(name)


static func _h(s: String) -> String:
	return "[font_size=22][b]%s[/b][/font_size]\n\n" % s


static func _sub(s: String) -> String:
	return "\n[color=%s][b]%s[/b][/color]\n" % [ACCENT, s]


static func _k(s: String) -> String:
	return "[color=%s][code]%s[/code][/color]" % [KEY, s]


static func _link(id: String, s: String) -> String:
	return "[url=%s][color=%s][u]%s[/u][/color][/url]" % [id, ACCENT, s]


# ── content ─────────────────────────────────────────────────────────────────

func _add_topics() -> void:
	_topic("start", "Getting started", _h("Getting started") +
		"picosim runs ARM Cortex-M0+ (Thumb) assembly the way the course's multi-cycle datapath would: every instruction is broken into clock cycles you can step through and watch.\n" +
		_sub("1. Load a program") +
		"Click " + _k("Open…") + " (" + _k("Ctrl/Cmd+O") + ") and pick a [code].s[/code] file, or start picosim with [code]picosim --gui file.s[/code]. The file is assembled and loaded; if the assembler reports an error it is shown in a dialog.\n" +
		_sub("2. Run it") +
		"• " + _k("Step Cycle") + " (" + _k("F11") + ") — advance one clock cycle.\n" +
		"• " + _k("Step Insn") + " (" + _k("F10") + ") — finish the current instruction (or run one whole instruction).\n" +
		"• " + _k("Run") + " / " + _k("Pause") + " (" + _k("F5") + ") — run continuously at the speed set by the slider.\n" +
		"• " + _k("Reload") + " (" + _k("Ctrl/Cmd+R") + ") — re-assemble the file from disk and reset the CPU. Use it after editing your code, or to start over after the program halts.\n" +
		_sub("3. Watch it") +
		"Anything that changed in the last step turns [color=#f2cc60]yellow[/color] — registers, flags, memory bytes and pins. Click " + _k("Show CPU Diagram") + " to see the datapath and the " + _link("cycles", "cycle-by-cycle") + " inspector.\n" +
		_sub("The screen") +
		"• Top left — " + _link("registers", "Registers") + "\n" +
		"• Middle left — " + _link("memory", "Disassembly and Memory") + "\n" +
		"• Bottom left — the " + _link("pico", "Raspberry Pi Pico board") + "\n" +
		"• Right — the " + _link("diagram", "CPU diagram") + " (when shown), the " + _link("cycles", "cycle inspector") + " and the " + _link("serial", "USB serial console") + "\n\n" +
		"Drag the bars between panels to resize them. See " + _link("layout", "Layout and zoom") + ".")

	_topic("programs", "Writing programs", _h("Writing programs") +
		"A program is a GNU-assembler Thumb file. A minimal one:\n\n" +
		"[code].syntax unified\n.cpu cortex-m0plus\n.thumb\n\nmain:\n    push  {r7, lr}\n    movs  r0, #'H'\n    bl    putchar      @ print 'H'\n    movs  r0, #0\n    pop   {r7, pc}     @ return: the program halts[/code]\n" +
		_sub("Entry point and ending") +
		"Execution starts at [code]main:[/code] (the label [code]asm_main:[/code] also works, and is what the Pico build uses). A small built-in OS calls it; when it returns, the OS runs [code]BKPT[/code] and the simulator halts. Press " + _k("Reload") + " to run it again.\n" +
		_sub("Input and output") +
		"• [code]bl putchar[/code] — prints the character in [code]r0[/code] to the " + _link("serial", "serial console") + ".\n" +
		"• [code]bl getchar[/code] — waits for one key typed in the console and returns it in [code]r0[/code].\n" +
		"They behave the same on a real Pico, so the same file runs on both.\n" +
		_sub("Memory map") +
		"• [code]0x0000–0x2FFF[/code] — the built-in OS\n" +
		"• [code]0x3000–0xFFFF[/code] — your code and data; the stack starts at the top of RAM ([code]SP = 0x00010000[/code]) and grows down\n" +
		"• [code]0x10000[/code] and up — memory-mapped I/O. The GPIO registers of the RP2040 are here: SIO at [code]0xD0000000[/code], IO_BANK0 at [code]0x40014000[/code], PADS_BANK0 at [code]0x4001C000[/code]. Writing them changes the " + _link("pico", "Pico board") + "'s pins.\n")

	_topic("registers", "Registers", _h("Registers panel") +
		"Shows R0–R12, SP (R13), LR (R14) and PC (R15) in hex. Values changed by the last step are [color=#f2cc60]yellow[/color]. Hover a register to see it as signed and unsigned decimal.\n" +
		_sub("Editing") +
		"Click a register, type a new value — hex like [code]0x1F[/code] or decimal like [code]31[/code] — and press " + _k("Enter") + ". Click a flag (N, Z, C, V) to toggle it.\n" +
		_sub("Datapath registers") +
		"The line below the flags shows the datapath's internal registers: MAR (memory address), MDR (memory data), IR and IR2 (the instruction being executed). They are explained in " + _link("components", "CPU diagram components") + ".\n" +
		_sub("Status line") +
		"[b]Next state[/b] is the FSM state the next clock cycle will run, and whether the CPU is between instructions or partway through one. Below it are the total instructions and clock cycles executed.\n" +
		_sub("Bigger text") +
		"Drag the bars below and beside the panel to make it larger — the text and fields scale up to fill the space.")

	_topic("memory", "Disassembly & memory", _h("Disassembly and Memory") +
		_sub("Disassembly tab") +
		"Your program as the CPU sees it: address, raw instruction bits, label and instruction. The row with ▶ is the instruction being executed. With " + _k("Follow PC") + " ticked the list scrolls to it automatically.\n\n" +
		"[b]Breakpoints:[/b] click the narrow first column of a row to put a red ● there; click again to remove it. " + _k("Run") + " stops when it reaches a breakpoint (before executing that instruction).\n" +
		_sub("Memory tab") +
		"• A hex dump of 256 bytes with an ASCII column. Changed bytes are [color=#f2cc60]yellow[/color], the byte at SP is highlighted blue and the current instruction brown.\n" +
		"• [b]Go to[/b] — type an address ([code]0x3000[/code]) or a label and press " + _k("Enter") + ", or jump with " + _k("PC") + ", " + _k("SP") + " or " + _k("main") + ".\n" +
		"• [b]Write bytes at[/b] — enter an address and hex bytes ([code]de ad be ef[/code]) and press " + _k("Write") + " to change memory.\n" +
		"• [b]Stack (from SP)[/b] — the words on the stack, with [code]SP→[/code] marking the top.")

	_topic("pico", "Raspberry Pi Pico board", _h("Raspberry Pi Pico board") +
		"A to-scale drawing of the Pico with its 40-pin header, showing what your program does to the RP2040's GPIO pins.\n" +
		_sub("Reading the pins") +
		"• [color=#39d353]green 1[/color] — output driven high\n" +
		"• dim [b]0[/b] — output (or pulled input) low\n" +
		"• [color=#61afef]blue 1[/color] — input reading high\n" +
		"• blank — floating (nothing drives it)\n" +
		"• [color=#f2cc60]yellow ring[/color] — changed by the last step\n" +
		"The on-board LED lights when GP25 is high. Under the board, the last I/O register write is decoded (for example [code]IO_BANK0 GPIO20_CTRL ← 0x331F[/code]). Hover any pin for its name, direction and level.\n" +
		_sub("Driving inputs") +
		"Click an [i]input[/i] pin to drive it like a switch: floating (Z) → 1 → 0 → floating. Your program sees it when it reads [code]GPIO_IN[/code]. Output pins cannot be clicked.\n" +
		_sub("Full window") +
		"Click " + _k("⛶ Enlarge") + " in the board's corner to fill the window with it; " + _k("✕ Close") + " or " + _k("Esc") + " returns.")

	_topic("diagram", "CPU diagram", _h("CPU diagram") +
		"Click " + _k("Show CPU Diagram") + " in the toolbar. The diagram is the multi-cycle datapath from the course text: one shared 32-bit bus, MAR/MDR, IR/IR2, a separate address adder, the ALU, the register file and the FSM that controls them.\n" +
		_sub("What the colors mean") +
		"• [color=#61afef]Bright blue wires and boxes[/color] carry data in the cycle being shown.\n" +
		"• [color=#c678dd]Purple dashed wires[/color] are control signals (BranchTaken).\n" +
		"• [color=#f2cc60]Yellow[/color] boxes changed at this cycle's clock edge; the caption shows old → new.\n" +
		"• Triangles on the bus are tri-state gates: the lit one is the block driving the bus. The bus value is printed above the bus.\n" +
		"When you step, a glowing pulse travels along the active wires in the order the data flows. Its speed follows the speed slider; at high speeds animation is skipped.\n" +
		_sub("Learning the components") +
		"Hover a block for a one-line summary. [b]Click a block[/b] (or the bus) to open a card explaining what it does, how it works, what it connects to and when it is used; the block and its wires are outlined in [color=#ffb86c]orange[/color]. Click it again, click empty space, press ✕ or " + _k("Esc") + " to close. The same descriptions are in " + _link("components", "CPU diagram components") + ".")

	_topic("cycles", "Cycle by cycle", _h("Cycle-by-cycle inspector") +
		"Shown below the CPU diagram. It follows the instruction being executed one clock cycle at a time.\n" +
		_sub("Left side") +
		"• The instruction's address and text, and how far through it the CPU is.\n" +
		"• The phase chips — " + _k("F") + " Fetch, " + _k("D") + " Decode, " + _k("EA") + " Evaluate address, " + _k("FO") + " Fetch operands, " + _k("EX") + " Execute, " + _k("SR") + " Store result — light up for the selected cycle. Hover one for its name.\n" +
		"• The list of this instruction's cycles: number, FSM state and phase. Click a row to look at an earlier cycle again; the diagram shows that cycle.\n" +
		"• " + _k("◀ Prev") + " and " + _k("Next ▶") + " move through the cycles. At the latest cycle, Next clocks the CPU (the same as Step Cycle).\n" +
		_sub("Right side") +
		"• The state name and a plain-language description of what the cycle does, including the value on the bus.\n" +
		"• [b]Control signals asserted[/b] — what the FSM turns on. [code]GateX[/code] puts X on the bus, [code]LD.X[/code] makes X load at the clock edge, [code]XMUX=…[/code] picks a multiplexer input, [code]ALUK=…[/code] picks the ALU operation.\n" +
		"• [b]What changed at the clock edge[/b] — every register, flag, memory byte or I/O register the cycle wrote, old → new.\n" +
		_sub("How many cycles?") +
		"Every instruction starts with the same 3 fetch cycles (FETCH_ADDR → FETCH_MEMORY → FETCH_IR). Typical totals: B = 5, ADDS/MOVS/CMP = 6, LDR/STR = 7, BL = 9, PUSH/POP of 2 registers = 10. The full table is in [code]docs/cycles.md[/code].")

	_topic("serial", "Serial console", _h("USB Serial console") +
		"Bottom right. Shows what your program prints with [code]putchar[/code].\n" +
		_sub("Typing input") +
		"• Click in the output area and type: each key is sent immediately (what [code]getchar[/code] expects).\n" +
		"• Or type a whole line in the box below and press " + _k("Enter") + " — it is sent followed by a carriage return.\n" +
		"When the program is waiting in [code]getchar[/code], the console header says so. " + _k("Clear") + " empties the output.")

	_topic("hardware", "Running on a real Pico", _h("Running on a real Pico") +
		"The same [code].s[/code] file can run on a Raspberry Pi Pico connected by USB.\n" +
		_sub("Steps") +
		"1. Choose [b]Pico (USB serial)[/b] in the toolbar's mode menu.\n" +
		"2. Click " + _k("Flash") + ". picosim builds a [code].uf2[/code] with the Pico SDK (downloaded on first use, so the first build is slow) and uploads it. It uses [code]picotool[/code] if installed, otherwise copies to a mounted [code]RPI-RP2[/code] drive — hold BOOTSEL while plugging the Pico in to get that drive.\n" +
		"3. After flashing, pick the Pico's port (marked [i](Pico)[/i]; " + _k("⟳") + " refreshes the list) and click " + _k("Connect") + ". The console now talks to the board.\n\n" +
		"Stepping, the diagram and the registers only work in [b]Simulator[/b] mode — switch back to it to debug.")

	_topic("layout", "Layout and zoom", _h("Layout and zoom") +
		"• [b]Resize panels[/b] by dragging the bars between them. The registers panel's text grows with it.\n" +
		"• [b]Zoom[/b] the whole window with " + _k("−") + " / " + _k("+") + " in the toolbar, " + _k("Ctrl/Cmd +") + " / " + _k("Ctrl/Cmd −") + ", or " + _k("Ctrl/Cmd") + " + mouse wheel. Click the percentage (or " + _k("Ctrl/Cmd 0") + ") to reset. The zoom is remembered.\n" +
		"• " + _k("Show / Hide CPU Diagram") + " toggles the datapath and cycle inspector.\n" +
		"• The Pico board can fill the window — see " + _link("pico", "Raspberry Pi Pico board") + ".")

	_topic("keys", "Keyboard shortcuts", _h("Keyboard shortcuts") +
		"[table=2]" +
		_row("F1", "open this guide") + _row("F11", "step one clock cycle (hold to repeat)") +
		_row("F10", "step one instruction (hold to repeat)") + _row("F5", "run / pause") +
		_row("Ctrl/Cmd+O", "open a .s file") + _row("Ctrl/Cmd+R", "reload (re-assemble and reset)") +
		_row("Ctrl/Cmd + / −", "zoom in / out") + _row("Ctrl/Cmd 0", "reset zoom") +
		_row("Esc", "close the enlarged Pico board, a component card, or this guide") +
		"[/table]\n\nF5, F10 and F11 only work in Simulator mode. While you are typing in the serial console, other keys go to your program.")

	_add_components()

	_topic("trouble", "Troubleshooting", _h("Troubleshooting") +
		_sub("Nothing happens when I press Step or Run") +
		"The program has halted (the status bar says [i]halted[/i]) — press " + _k("Reload") + ". Or you are in Pico mode — switch to Simulator. Or no program is loaded.\n" +
		_sub("The program stops and waits") +
		"It is in [code]getchar[/code]. Click the serial console and type a key.\n" +
		_sub("\"Could not load program\"") +
		"The assembler found an error. The dialog shows the assembler's message (it names the line); fix the file and press " + _k("Reload") + ".\n" +
		_sub("\"Simulator backend disconnected\"") +
		"The Python backend stopped. Close picosim and start it again with [code]picosim --gui[/code].\n" +
		_sub("A pin will not respond to clicks") +
		"Only input pins can be driven. If your program configured the pin as an output (via [code]GPIO_OE[/code]), it controls the pin.")


static func _row(k: String, what: String) -> String:
	return "[cell][color=%s][code]%s[/code][/color]    [/cell][cell]%s[/cell]" % [KEY, k, what]


func _add_components() -> void:
	if not ResourceLoader.exists(INFO_PATH):
		return
	var info = load(INFO_PATH)
	var names := {}
	for id in info.INFO:
		names[id] = str(info.INFO[id]["title"]).get_slice(" — ", 0)
	var t := _h("CPU diagram components") + "Every block on the " + _link("diagram", "CPU diagram") + ", in the order data usually flows. Click a block on the diagram to see the same text next to it.\n"
	for id in info.ORDER:
		t += "\n[color=%s]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/color]\n\n" % DIM
		t += info.bbcode(id, names) + "\n"
	_topic("components", "CPU diagram components", t)
