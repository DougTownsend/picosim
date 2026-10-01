extends Node
## User guide window: a list of topics on the left, the selected topic on
## the right.  Opened from the toolbar's "Guide" button or with F1.
##
## Self-contained: main.gd only creates it if this file exists, so deleting
## guide.gd removes the Guide button and nothing else.  The "CPU diagram
## components" topic is built from component_info.gd when that file exists.
##
## The "Program walkthrough" topic explains the loaded program in words, one
## instruction per step (the backend's walkthrough command runs a fresh copy
## of it).  Clicking a step shows each of its clock cycles in detail, with a
## picture of the CPU diagram during that cycle, drawn off-screen by the same
## diagram_view.gd the main window uses.
##
## Making the window bigger than it opened at makes the text bigger with it
## (by the square root of the area, up to 3×); it never drops below normal.

const INFO_PATH := "res://scripts/component_info.gd"
const DIAGRAM_PATH := "res://scripts/diagram_view.gd"
const TEXT_SCALE_MAX := 3.0
## Size the walkthrough's diagram pictures are drawn at (the diagram's 1000 × 640 shape).
const SHOT_SIZE := Vector2i(1250, 800)
const SHOT_CACHE := 3   # steps whose pictures are kept
## Base font sizes: [control, theme item, size at text scale 1].
var _fonts: Array = []

var win: Window
var topics: ItemList
var body: RichTextLabel
var _ids: Array = []
var _text := {}
var bg: PanelContainer
var _shown := ""
var text_scale := 1.0
var _open_size := Vector2i.ZERO   # window size when opened: text scale 1

var _wt: Dictionary = {}          # the walkthrough message for the loaded program
var _wt_waiting := false          # asked the backend, no answer yet
var _wt_step := -1                # step on display (-1 = the list of all steps)
var _wt_gen := 0                  # bumps whenever a different page is requested
var _wt_shots := {}               # step → [Texture2D per cycle]
var _wt_order: Array = []         # cached steps, oldest first
var _wt_demo := -1                # --guide-step N (screenshots)
var _shot_vp: SubViewport
var _shot_view: Control


func _ready() -> void:
	win = Window.new()
	win.title = "picosim — User Guide"
	win.visible = false
	win.transient = true
	win.min_size = Vector2i(560, 360)
	win.close_requested.connect(win.hide)
	win.window_input.connect(_on_window_input)
	win.size_changed.connect(_on_win_resized)
	add_child(win)

	bg = PanelContainer.new()
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	win.add_child(bg)

	var split := HSplitContainer.new()
	bg.add_child(split)
	topics = ItemList.new()
	topics.custom_minimum_size.x = 210
	_fonts.append([topics, "font_size", 14])
	topics.item_selected.connect(func(i): _show(_ids[i]))
	split.add_child(topics)

	body = RichTextLabel.new()
	body.bbcode_enabled = true
	body.selection_enabled = true
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_theme_font_override("mono_font", Backend.mono_font())
	_fonts.append([body, "normal_font_size", 15])
	_fonts.append([body, "bold_font_size", 15])
	_fonts.append([body, "mono_font_size", 14])
	for f in _fonts:
		f[0].add_theme_font_size_override(f[1], f[2])
	body.meta_clicked.connect(_on_meta)
	split.add_child(body)

	Backend.walkthrough_received.connect(_on_walkthrough)
	Backend.program_loaded.connect(func(_p): _wt_reset())
	Backend.state_changed.connect(_on_state)

	_apply_palette()
	Palette.changed.connect(_apply_palette)
	var args := OS.get_cmdline_user_args()
	var j := args.find("--guide-step")
	if j >= 0 and j + 1 < args.size():
		_wt_demo = int(args[j + 1])
	var i := args.find("--guide")
	if i >= 0:
		open.call_deferred(args[i + 1] if i + 1 < args.size() else "")


## Show the guide, optionally at a topic id.
func open(topic := "") -> void:
	if topic != "" and _text.has(topic):
		topics.select(_ids.find(topic))
		_show(topic)
	var area := get_viewport().get_visible_rect().size
	_open_size = Vector2i.ZERO   # ignore the resize popup_centered itself makes
	win.popup_centered(Vector2i(mini(int(area.x * 0.8), 1100), int(area.y * 0.85)))
	_open_size = win.size
	_set_text_scale(1.0)


func _on_win_resized() -> void:
	if _open_size.x <= 0 or _open_size.y <= 0:
		return
	var growth := float(win.size.x * win.size.y) / float(_open_size.x * _open_size.y)
	var s := clampf(sqrt(growth), 1.0, TEXT_SCALE_MAX)
	_set_text_scale(maxf(1.0, floorf(s / 0.05 + 0.001) * 0.05))


func _set_text_scale(s: float) -> void:
	if is_equal_approx(s, text_scale):
		return
	text_scale = s
	for f in _fonts:
		f[0].add_theme_font_size_override(f[1], roundi(f[2] * s))
	topics.custom_minimum_size.x = 210 * s
	_rebuild()   # headings have their size baked into the BBCode


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


## Restyle and rebuild the topics: their text has the scheme's colours baked in.
func _apply_palette() -> void:
	win.theme = Palette.ui_theme
	_wt_shots.clear()   # the pictures have the old colours baked in
	_wt_order.clear()
	bg.add_theme_stylebox_override("panel", Palette.panel_box("bg", 0, 10))
	var bsb := Palette.panel_box("panel", 6, 12)
	bsb.content_margin_left = 16
	bsb.content_margin_right = 16
	body.add_theme_stylebox_override("normal", bsb)
	_rebuild()


## Rebuild every topic's text (colours and heading sizes are baked into it),
## keeping the shown topic and roughly the same scroll position.
func _rebuild() -> void:
	var shown := _shown
	var bar := body.get_v_scroll_bar()
	var pos := bar.value / maxf(1.0, bar.max_value)
	_ids.clear()
	_text.clear()
	topics.clear()
	_add_topics()
	if shown == "":
		shown = _ids[0]
	topics.select(_ids.find(shown))
	_show(shown)
	_restore_scroll.call_deferred(pos)


func _restore_scroll(pos: float) -> void:
	var bar := body.get_v_scroll_bar()
	bar.value = pos * bar.max_value


## BBCode colour for a palette key.
static func _c(key: String) -> String:
	return Palette.hex(key)


func _show(id: String) -> void:
	_shown = id
	if id == "walkthrough":
		_show_walkthrough()
		return
	_wt_gen += 1
	body.text = _text[id]
	body.scroll_to_line(0)


func _on_meta(m: Variant) -> void:
	var s := str(m)
	if s == "wt:all":
		_wt_step = -1
		_show_walkthrough()
	elif s.begins_with("wt:"):
		_wt_step = int(s.substr(3))
		_show_walkthrough()
	else:
		open(s)


func _topic(id: String, name: String, bb: String) -> void:
	_ids.append(id)
	_text[id] = bb
	topics.add_item(name)


func _h(s: String) -> String:
	return "[font_size=%d][b]%s[/b][/font_size]\n\n" % [roundi(22 * text_scale), s]


static func _sub(s: String) -> String:
	return "\n[color=%s][b]%s[/b][/color]\n" % [_c("accent"), s]


static func _k(s: String) -> String:
	return "[color=%s][code]%s[/code][/color]" % [_c("yellow"), s]


static func _link(id: String, s: String) -> String:
	return "[url=%s][color=%s][u]%s[/u][/color][/url]" % [id, _c("accent"), s]


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
		"Anything that changed in the last step turns [color=" + _c("changed") + "]yellow[/color] — registers, flags, memory bytes and pins. Click " + _k("Show CPU Diagram") + " to see the datapath and the " + _link("cycles", "cycle-by-cycle") + " inspector.\n" +
		_sub("The screen") +
		"• Top left — " + _link("registers", "Registers") + "\n" +
		"• Middle left — " + _link("memory", "Disassembly and Memory") + "\n" +
		"• Bottom left — the " + _link("pico", "Raspberry Pi Pico board") + "\n" +
		"• Right — the " + _link("diagram", "CPU diagram") + " (when shown), the " + _link("cycles", "cycle inspector") + " and the " + _link("serial", "USB serial console") + "\n\n" +
		"To see what your program does in words, open " + _link("walkthrough", "Program walkthrough") + ".\n\n" +
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
		"Shows R0–R12, SP (R13), LR (R14) and PC (R15) in hex. Values changed by the last step are [color=" + _c("changed") + "]yellow[/color]. Hover a register to see it as signed and unsigned decimal.\n" +
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
		"• A hex dump of 256 bytes with an ASCII column. Changed bytes are [color=" + _c("changed") + "]yellow[/color], the byte at SP is highlighted blue and the current instruction brown.\n" +
		"• [b]Go to[/b] — type an address ([code]0x3000[/code]) or a label and press " + _k("Enter") + ", or jump with " + _k("PC") + ", " + _k("SP") + " or " + _k("main") + ".\n" +
		"• [b]Write bytes at[/b] — enter an address and hex bytes ([code]de ad be ef[/code]) and press " + _k("Write") + " to change memory.\n" +
		"• [b]Stack (from SP)[/b] — the words on the stack, with [code]SP→[/code] marking the top.")

	_topic("pico", "Raspberry Pi Pico board", _h("Raspberry Pi Pico board") +
		"A to-scale drawing of the Pico with its 40-pin header, showing what your program does to the RP2040's GPIO pins.\n" +
		_sub("Reading the pins") +
		"• [color=" + _c("green") + "]green 1[/color] — output driven high\n" +
		"• dim [b]0[/b] — output (or pulled input) low\n" +
		"• [color=" + _c("accent") + "]blue 1[/color] — input reading high\n" +
		"• blank — floating (nothing drives it)\n" +
		"• [color=" + _c("changed") + "]yellow ring[/color] — changed by the last step\n" +
		"The on-board LED lights when GP25 is high. Under the board, the last I/O register write is decoded (for example [code]IO_BANK0 GPIO20_CTRL ← 0x331F[/code]). Hover any pin for its name, direction and level.\n" +
		_sub("Driving inputs") +
		"Click an [i]input[/i] pin to drive it like a switch: floating (Z) → 1 → 0 → floating. Your program sees it when it reads [code]GPIO_IN[/code]. Output pins cannot be clicked.\n" +
		_sub("Full window") +
		"Click " + _k("⛶ Enlarge") + " in the board's corner to fill the window with it; " + _k("✕ Close") + " or " + _k("Esc") + " returns.")

	_topic("diagram", "CPU diagram", _h("CPU diagram") +
		"Click " + _k("Show CPU Diagram") + " in the toolbar. The diagram is the multi-cycle datapath from the course text: one shared 32-bit bus, MAR/MDR, IR/IR2, a separate address adder, the ALU, the register file and the FSM that controls them.\n" +
		_sub("Values during a cycle, and the clock edge") +
		"The diagram shows the cycle [i]while it happens[/i]. Every register (PC, IR, MAR, MDR, the register file, the flags) keeps showing the value it holds during the cycle. A register that loads a new value at the clock edge which ends the cycle turns [color=" + _c("changed") + "]yellow[/color] and gets a caption [code]old → new[/code] underneath; the box itself only shows the new value once you step to the next cycle. For example in FETCH_ADDR the PC still reads [code]0x00003000[/code] with [code]0x00003000 → 0x00003002[/code] below it, and in FETCH_MEMORY it reads [code]0x00003002[/code]. (The Registers panel on the left always shows the machine after the last clock edge.)\n" +
		_sub("What the colors mean") +
		"• [color=" + _c("accent") + "]Bright blue wires and boxes[/color] carry data in the cycle being shown.\n" +
		"• [color=" + _c("purple") + "]Purple dashed arrows[/color] are control inputs from the FSM: the short stubs into the muxes (select), the ALU (ALUK), the +2 incrementer (PCINC) and each tri-state buffer (GatePC, GateADDR, …), plus BranchTaken. A bright stub is asserted this cycle and is labelled with the value it selects.\n" +
		"• [color=" + _c("changed") + "]Yellow[/color] marks a value loaded at the clock edge that ends this cycle (old → new).\n" +
		"• The triangles on the bus are tri-state buffers, named after the block that feeds them: gatePC, gateAdder, gateMDR, gateALU and gateVector. The lit one is driving the bus; the others are off (high impedance). The bus value is printed above the bus.\n" +
		"• Every mux, the ALU and the adders take their inputs on the long side and send their result out of the short side; control comes in on a slanted side.\n" +
		"When you step, a glowing pulse travels along the active wires in the order the data flows; a register's yellow caption appears when the pulse reaches it. Its speed follows the speed slider; at high speeds animation is skipped.\n" +
		_sub("Learning the components") +
		"Hover a block for a one-line summary. [b]Click a block[/b], a tri-state buffer (▲ / ▼) or the bus to open a card explaining what it does, how it works, what it connects to and when it is used; the block and its wires are outlined in [color=" + _c("sel") + "]orange[/color]. Click it again, click empty space, press ✕ or " + _k("Esc") + " to close. The same descriptions are in " + _link("components", "CPU diagram components") + ".")

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
		"• [b]What changes at the clock edge[/b] — every register, flag, memory byte or I/O register the cycle loads at the edge that ends it, old → new. On the diagram these are the yellow captions.\n" +
		_sub("How many cycles?") +
		"Every instruction starts with the same 3 fetch cycles (FETCH_ADDR → FETCH_MEMORY → FETCH_IR). Typical totals: B = 5, ADDS/MOVS/CMP = 6, LDR/STR = 7, BL = 9, PUSH/POP of 2 registers = 10. The full table is in [code]docs/cycles.md[/code].")

	_topic("walkthrough", "Program walkthrough", "")   # built in _show_walkthrough

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


# ── program walkthrough ─────────────────────────────────────────────────────

func _wt_reset() -> void:
	_wt = {}
	_wt_waiting = false
	_wt_step = -1
	_wt_shots.clear()
	_wt_order.clear()
	if win.visible and _shown == "walkthrough":
		_show_walkthrough()


## The walkthrough page may be open before the program has finished loading.
func _on_state(st: Dictionary) -> void:
	if win.visible and _shown == "walkthrough" and _wt.is_empty() and not _wt_waiting \
			and st.get("loaded", false):
		_show_walkthrough()


func _on_walkthrough(data: Dictionary) -> void:
	_wt_waiting = false
	_wt = data
	_wt_shots.clear()
	_wt_order.clear()
	if _wt_demo >= 0:
		_wt_step = mini(_wt_demo, data["steps"].size() - 1)
		_wt_demo = -1
	if _shown == "walkthrough":
		_show_walkthrough()


func _show_walkthrough() -> void:
	_wt_gen += 1
	var st := Backend.state
	var h := _h("Program walkthrough")
	if not st.get("loaded", false):
		body.text = h + "Load a program (" + _k("Open…") + ") and this page explains it step by step."
		return
	if _wt.is_empty() or str(_wt.get("path", "")) != str(st.get("path", "")):
		if not _wt_waiting:
			_wt_waiting = true
			Backend.send("walkthrough")
		body.text = h + "[color=%s]Working through %s…[/color]" % [_c("dim"), str(st.get("path", "")).get_file()]
		return
	var steps: Array = _wt["steps"]
	if _wt_step < 0 or _wt_step >= steps.size():
		_wt_step = -1
		body.text = _wt_overview()
		body.scroll_to_line(0)
	else:
		_wt_detail(_wt_step)


## Heading shown above a step that starts at a label.
func _wt_heading(step: Dictionary) -> String:
	var lab := str(step["label"])
	if lab == "":
		return ""
	var a := int(step["addr"])
	var note := ""
	if a < 0x3000:
		note = {"_start": "the built-in OS starts here and calls your main",
			"putchar": "built-in OS: print a character", "getchar": "built-in OS: read a key"}.get(lab, "built-in OS")
	elif not lab.begins_with("."):
		note = "a function in your program"
	else:
		note = "a label in your program"
	return "
[color=%s][b]%s[/b][/color]  [color=%s]%s[/color]
" % [_c("accent"), lab, _c("dim"), note]


func _wt_overview() -> String:
	var steps: Array = _wt["steps"]
	var t := _h("Program walkthrough — " + str(_wt["path"]).get_file())
	t += "Your program as the CPU runs it from reset, one instruction per step, in the order they execute. Each line says in plain words what the instruction does with the real values. [b]Click a step[/b] to see its clock cycles one by one, each with a picture of the CPU diagram during that cycle.

"
	t += "[color=%s]This runs a separate copy of the program, so it does not change what the main window shows. Values are hexadecimal.[/color]
" % _c("dim")
	for i in steps.size():
		var s: Dictionary = steps[i]
		t += _wt_heading(s)
		t += "[url=wt:%d][color=%s][b]%d.[/b] [code]%04X  %s[/code][/color][/url]
" % [
			i, _c("text"), i + 1, int(s["addr"]), _esc(str(s["text"]))]
		t += "      %s  [color=%s](%d cycles)[/color]
" % [_esc(str(s["summary"])), _c("dim"), s["cycles"].size()]
	t += "
[color=%s][b]Where it stops[/b][/color]
%s
" % [_c("accent"), _esc(str(_wt["stopped"]))]
	if str(_wt["output"]) != "":
		t += "
Printed to the serial console so far: [code]%s[/code]
" % _esc(str(_wt["output"]).c_escape())
	return t


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")


func _wt_nav(i: int, n: int) -> String:
	var t := "[url=wt:all]◀ All steps[/url]"
	if i > 0:
		t += "      [url=wt:%d]◀ Previous step[/url]" % (i - 1)
	if i < n - 1:
		t += "      [url=wt:%d]Next step ▶[/url]" % (i + 1)
	return "[color=%s]%s[/color]
" % [_c("accent"), t]


## One step: overview, then every cycle in words with its diagram picture.
func _wt_detail(i: int) -> void:
	var gen := _wt_gen
	var steps: Array = _wt["steps"]
	var s: Dictionary = steps[i]
	if not _wt_shots.has(i):
		body.text = _wt_nav(i, steps.size()) + "
" + _h("Step %d · %s" % [i + 1, _esc(str(s["text"]))]) + \
			"[color=%s]Drawing the diagram for each cycle…[/color]" % _c("dim")
		var rendered: Array = await _wt_render(s, gen)
		if gen != _wt_gen:
			return   # the user moved on meanwhile
		_wt_shots[i] = rendered
		_wt_order.append(i)
		while _wt_order.size() > SHOT_CACHE:
			_wt_shots.erase(_wt_order.pop_front())
	var shots: Array = _wt_shots[i]
	var cycles: Array = s["cycles"]
	body.clear()
	body.append_text(_wt_nav(i, steps.size()) + "
")
	body.append_text(_h("Step %d of %d · [code]%04X  %s[/code]" % [i + 1, steps.size(), int(s["addr"]), _esc(str(s["text"]))]))
	if str(s["label"]) != "":
		body.append_text(_wt_heading(s) + "
")
	body.append_text("[b]In short:[/b] %s

" % _esc(str(s["summary"])))
	body.append_text("This instruction takes [b]%d clock cycles[/b]. Each picture shows the CPU diagram [i]during[/i] that cycle: boxes still hold their old values, bright wires carry data, and a yellow [code]old → new[/code] caption marks what is loaded at the clock edge that ends the cycle.
" % cycles.size())
	var w := clampf(body.size.x - 70.0, 360.0, 1400.0)
	for k in cycles.size():
		var cy: Dictionary = cycles[k]
		var info: Dictionary = cy["info"]
		body.append_text(_sub("Cycle %d of %d · %s   [color=%s](%s phase)[/color]" % [k + 1, cycles.size(),
			str(info["state"]), _c("dim"), str(info["phase"]).capitalize()]))
		body.append_text(_esc(str(cy["text"])) + "

")
		if k < shots.size() and shots[k] != null:
			body.add_image(shots[k], int(w), int(w * float(SHOT_SIZE.y) / SHOT_SIZE.x))
			body.append_text("
")
		var sigs := ""
		for sg in info["signals"]:
			sigs += "[bgcolor=%s] [code]%s[/code] [/bgcolor] " % [_c("sig_bg"), _esc(str(sg))]
		body.append_text("[color=%s]Control signals:[/color] %s
" % [_c("dim"), sigs if sigs != "" else "(none)"])
	body.append_text("
" + _wt_nav(i, steps.size()))
	body.scroll_to_line(0)


## Draw the diagram once per cycle of `step` and return the pictures.
func _wt_render(step: Dictionary, gen: int) -> Array:
	if _shot_vp == null:
		_shot_vp = SubViewport.new()
		_shot_vp.size = SHOT_SIZE
		_shot_vp.render_target_update_mode = SubViewport.UPDATE_DISABLED
		add_child(_shot_vp)
		_shot_view = load(DIAGRAM_PATH).new()
		_shot_view.still = true
		_shot_view.size = Vector2(SHOT_SIZE)
		_shot_vp.add_child(_shot_view)
		await get_tree().process_frame
	var out := []
	var hist := []
	for cy in step["cycles"]:
		if gen != _wt_gen:
			return out
		var info: Dictionary = cy["info"]
		hist.append(info)
		var post: Dictionary = cy["post"]
		var stx := {"loaded": true, "regs": post["regs"], "flags": post["flags"],
			"datapath": post["datapath"], "history": hist.duplicate(), "running": false,
			"next_state": "", "cps": 0}
		_shot_view.st = stx
		_shot_view.show_cycle(info)
		_shot_view.queue_redraw()
		_shot_vp.render_target_update_mode = SubViewport.UPDATE_ONCE
		await RenderingServer.frame_post_draw
		await RenderingServer.frame_post_draw
		var img := _shot_vp.get_texture().get_image()
		out.append(ImageTexture.create_from_image(img) if img != null else null)
	return out


static func _row(k: String, what: String) -> String:
	return "[cell][color=%s][code]%s[/code][/color]    [/cell][cell]%s[/cell]" % [_c("yellow"), k, what]


func _add_components() -> void:
	if not ResourceLoader.exists(INFO_PATH):
		return
	var info = load(INFO_PATH)
	var names := {}
	for id in info.INFO:
		names[id] = str(info.INFO[id]["title"]).get_slice(" — ", 0)
	var t := _h("CPU diagram components") + "Every block on the " + _link("diagram", "CPU diagram") + ", in the order data usually flows. Click a block on the diagram to see the same text next to it.\n"
	for id in info.ORDER:
		t += "\n[color=%s]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/color]\n\n" % _c("dim")
		t += info.bbcode(id, names, roundi(17 * text_scale)) + "\n"
	_topic("components", "CPU diagram components", t)
