extends Node
## User guide window: a list of topics on the left, the selected topic on
## the right.  Opened from the toolbar's "Guide" button or with F1.
##
## Self-contained: main.gd only creates it if this file exists, so deleting
## guide.gd removes the Guide button and nothing else.  The "CPU diagram
## components" topic is built from component_info.gd when that file exists.
##
## Topics are grouped under section headings in the list (see _section).
## The "Pipeline components" topic is built the same way from PIPE_INFO, and
## the "Pipeline walkthrough" works like the program walkthrough below, with
## pictures of the pipeline drawn by pipeline_view.gd.
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
const PIPELINE_PATH := "res://scripts/pipeline_view.gd"
const TEXT_SCALE_MAX := 3.0
## Size the walkthrough's diagram pictures are drawn at (the diagram's 1000 × 640 shape).
const SHOT_SIZE := Vector2i(1250, 800)
const SHOT_CACHE := 3   # steps whose pictures are kept
const KEYS_HINT := "[color=%s]Keys: ← → cycle by cycle · Shift + ← → instruction by instruction · Backspace for the list[/color]\n"
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
var _wt_cycle := 0                # cycle of that step on display
var _wt_list_scroll := 0.0        # where the list was scrolled to, for ☰ All instructions
var _wt_gen := 0                  # bumps whenever a different page is requested
var _wt_shots := {}               # step → [Texture2D per cycle]
var _wt_order: Array = []         # cached steps, oldest first
var _wt_demo := -1                # --guide-step N (screenshots)
var _shot_vp: SubViewport
var _shot_view: Control

var _pw: Dictionary = {}          # the pipeline walkthrough message
var _pw_waiting := false
var _pw_step := -1
var _pw_cycle := 0
var _pw_list_scroll := 0.0
var _pw_shots := {}               # step → [Texture2D per cycle]
var _pw_order: Array = []
var _pshot_vp: SubViewport
var _pshot_view: Control


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
	topics.item_selected.connect(func(i): if _ids[i] != "": _show(_ids[i]))
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
	body.meta_underlined = false   # _link underlines itself; walkthrough rows and buttons do not
	split.add_child(body)

	Backend.walkthrough_received.connect(_on_walkthrough)
	Backend.pipe_walkthrough_received.connect(_on_pipewalk)
	Backend.program_loaded.connect(func(_p): _wt_reset(); _pw_reset())
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
	elif k and k.pressed and k.keycode in [KEY_LEFT, KEY_RIGHT, KEY_BACKSPACE]:
		if _shown == "walkthrough" and _wt_step >= 0 and _wt_shots.has(_wt_step):
			_walk_key(k, false)
			win.set_input_as_handled()
		elif _shown == "pipewalk" and _pw_step >= 0 and _pw_shots.has(_pw_step):
			_walk_key(k, true)
			win.set_input_as_handled()


## ← → cycle by cycle (running on into the next instruction), Shift + ← →
## instruction by instruction, Backspace back to the list.
func _walk_key(k: InputEventKey, pipe: bool) -> void:
	var step := _pw_step if pipe else _wt_step
	var cyc := _pw_cycle if pipe else _wt_cycle
	var n: int = (_pw if pipe else _wt)["steps"][step]["cycles"].size()
	var goto := _pw_goto if pipe else _wt_goto
	if k.keycode == KEY_BACKSPACE:
		goto.call(-1)
		return
	var d := 1 if k.keycode == KEY_RIGHT else -1
	if k.shift_pressed:
		goto.call(maxi(0, step + d))
	elif cyc + d >= 0 and cyc + d < n:
		if pipe:
			_pw_cycle += d
			_show_pipewalk()
		else:
			_wt_cycle += d
			_show_walkthrough()
	elif step + d >= 0:
		goto.call(step + d, d < 0)


## Restyle and rebuild the topics: their text has the scheme's colours baked in.
func _apply_palette() -> void:
	win.theme = Palette.ui_theme
	_wt_shots.clear()   # the pictures have the old colours baked in
	_wt_order.clear()
	_pw_shots.clear()
	_pw_order.clear()
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
		shown = _ids.filter(func(x): return x != "")[0]
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
	if id == "pipewalk":
		_show_pipewalk()
		return
	_wt_gen += 1
	body.text = _text[id]
	body.scroll_to_line(0)


func _on_meta(m: Variant) -> void:
	var s := str(m)
	if s == "wt:all":
		_wt_goto(-1)
	elif s.begins_with("wt:"):
		_wt_goto(int(s.substr(3)))
	elif s.begins_with("wc:"):
		_wt_cycle = int(s.substr(3))
		_show_walkthrough()
	elif s == "pw:all":
		_pw_goto(-1)
	elif s.begins_with("pw:"):
		_pw_goto(int(s.substr(3)))
	elif s.begins_with("pc:"):
		_pw_cycle = int(s.substr(3))
		_show_pipewalk()
	else:
		open(s)


func _topic(id: String, name: String, bb: String) -> void:
	_ids.append(id)
	_text[id] = bb
	topics.add_item("    " + name)


## A heading in the topic list: groups the topics under it.
func _section(name: String) -> void:
	_ids.append("")
	var i := topics.add_item(name.to_upper())
	topics.set_item_selectable(i, false)
	topics.set_item_custom_fg_color(i, Palette.c("accent"))
	topics.set_item_tooltip_enabled(i, false)


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
	_section("Getting started")

	_topic("start", "Getting started", _h("Getting started") +
		"picosim runs ARM Cortex-M0+ (Thumb) assembly and shows you how a processor carries it out, clock cycle by clock cycle. It can run your program on two machines (pick one in the toolbar's machine menu):\n" +
		"• " + _k("Multi-cycle") + " — the course's multi-cycle datapath: one instruction at a time, each broken into the FSM's states. See " + _link("diagram", "Datapath diagram") + ".\n" +
		"• " + _k("2-stage pipeline") + " — the Raspberry Pi Pico's real Cortex-M0+: two instructions in flight at once. See " + _link("pipeline", "Pipeline diagram") + ".\n" +
		"Both run the same instructions with the same results; only the timing differs.\n" +
		_sub("1. Load a program") +
		"Click " + _k("Open…") + " (" + _k("Ctrl/Cmd+O") + ") and pick a [code].s[/code] file, or start picosim with [code]picosim --gui file.s[/code]. The file is assembled and loaded; if the assembler reports an error it is shown in a dialog.\n" +
		_sub("2. Run it") +
		"• " + _k("Step Cycle") + " (" + _k("F11") + ") — advance one clock cycle.\n" +
		"• " + _k("Step Insn") + " (" + _k("F10") + ") — finish the current instruction (or run one whole instruction).\n" +
		"• " + _k("Run") + " / " + _k("Pause") + " (" + _k("F5") + ") — run continuously at the speed set by the slider.\n" +
		"• " + _k("Reload") + " (" + _k("Ctrl/Cmd+R") + ") — re-assemble the file from disk and reset the CPU. Use it after editing your code, or to start over after the program halts.\n" +
		_sub("3. Watch it") +
		"Anything that changed in the last step turns [color=" + _c("changed") + "]yellow[/color] — registers, flags, memory bytes and pins. Click " + _k("Show CPU Diagram") + " to see the " + _link("diagram", "datapath") + " and the " + _link("cycles", "cycle-by-cycle") + " inspector, or the " + _link("pipeline", "pipeline") + " when the machine menu is on " + _k("2-stage pipeline") + ".\n" +
		_sub("The screen") +
		"• Top left — " + _link("registers", "Registers") + "\n" +
		"• Middle left — " + _link("memory", "Disassembly and Memory") + "\n" +
		"• Bottom left — the " + _link("pico", "Raspberry Pi Pico board") + "\n" +
		"• Right — the " + _link("diagram", "CPU diagram") + " or " + _link("pipeline", "pipeline") + " (when shown), the " + _link("cycles", "cycle inspector") + " and the " + _link("serial", "USB serial console") + "\n\n" +
		"To see what your program does in words, open the " + _link("walkthrough", "Program walkthrough") + " or the " + _link("pipewalk", "Pipeline walkthrough") + ".\n\n" +
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

	_section("The main window")

	_topic("registers", "Registers", _h("Registers panel") +
		"Shows R0–R12, SP (R13), LR (R14) and PC (R15) in hex. Values changed by the last step are [color=" + _c("changed") + "]yellow[/color]. Hover a register to see it as signed and unsigned decimal.\n" +
		_sub("Editing") +
		"Click a register, type a new value — hex like [code]0x1F[/code] or decimal like [code]31[/code] — and press " + _k("Enter") + ". Click a flag (N, Z, C, V) to toggle it.\n" +
		_sub("Datapath registers") +
		"The line below the flags shows the datapath's internal registers: MAR (memory address), MDR (memory data), IR and IR2 (the instruction being executed). They are explained in " + _link("components", "Datapath components") + ".\n" +
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

	_topic("serial", "Serial console", _h("USB Serial console") +
		"Bottom right. Shows what your program prints with [code]putchar[/code].\n" +
		_sub("Typing input") +
		"• Click in the output area and type: each key is sent immediately (what [code]getchar[/code] expects).\n" +
		"• Or type a whole line in the box below and press " + _k("Enter") + " — it is sent followed by a carriage return.\n" +
		"When the program is waiting in [code]getchar[/code], the console header says so. " + _k("Clear") + " empties the output.")

	_topic("layout", "Layout and zoom", _h("Layout and zoom") +
		"• [b]Resize panels[/b] by dragging the bars between them. The registers panel's text grows with it.\n" +
		"• [b]Zoom[/b] the whole window with " + _k("−") + " / " + _k("+") + " in the toolbar, " + _k("Ctrl/Cmd +") + " / " + _k("Ctrl/Cmd −") + ", or " + _k("Ctrl/Cmd") + " + mouse wheel. Click the percentage (or " + _k("Ctrl/Cmd 0") + ") to reset. The zoom is remembered.\n" +
		"• " + _k("Show / Hide CPU Diagram") + " toggles the diagram and the panel under it. The machine menu next to it switches the diagram between the " + _link("diagram", "multi-cycle datapath") + " and the " + _link("pipeline", "2-stage pipeline") + ".\n" +
		"• The Pico board can fill the window — see " + _link("pico", "Raspberry Pi Pico board") + ".")

	_section("Multi-cycle datapath")

	_topic("diagram", "Datapath diagram", _h("Datapath diagram") +
		"Click " + _k("Show CPU Diagram") + " in the toolbar, with the machine menu next to it on " + _k("Multi-cycle") + ". The diagram is the multi-cycle datapath from the course text: one shared 32-bit bus, MAR/MDR, IR/IR2, a separate address adder, the ALU, the register file and the FSM that controls them.\n" +
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
		"Hover a block for a one-line summary. [b]Click a block[/b], a tri-state buffer (▲ / ▼) or the bus to open a card explaining what it does, how it works, what it connects to and when it is used; the block and its wires are outlined in [color=" + _c("sel") + "]orange[/color]. Click it again, click empty space, press ✕ or " + _k("Esc") + " to close. The same descriptions are in " + _link("components", "Datapath components") + ".\n" +
		_sub("Python models") +
		"A block's card ends with [b]How it works in Python[/b], the block's model from [code]picosim/components.py[/code]. A test drives these models with only the FSM's control signals and checks, on every cycle of every test program, that they produce exactly what the simulator does.")

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

	_add_components()

	_topic("walkthrough", "Program walkthrough", "")   # built in _show_walkthrough

	_section("2-stage pipeline (RP2040)")

	_topic("pipeline", "Pipeline diagram", _h("Pipeline diagram") +
		"Pick " + _k("2-stage pipeline") + " in the toolbar's machine menu, next to " + _k("Show CPU Diagram") + ". The diagram turns into the Raspberry Pi Pico's own processor, the Cortex-M0+, which overlaps instructions in a [b]2-stage pipeline[/b]. Pick " + _k("Multi-cycle") + " to go back to the " + _link("diagram", "datapath diagram") + ".\n" +
		_sub("Two instructions at once") +
		"On the multi-cycle datapath one instruction uses the whole CPU until it is finished. The M0+ splits the work into two stages that run at the same time:\n" +
		"• [color=" + _c("stage_f") + "][b]FETCH[/b][/color] (left) — reads the next instruction from memory at the address in the PC.\n" +
		"• [color=" + _c("stage_e") + "][b]DECODE + EXECUTE[/b][/color] (right) — decodes the instruction fetched in the previous cycle, reads its registers, uses the ALU or memory, and writes the result back.\n" +
		"• [b]IF/EX[/b] — the pipeline register between them. It holds the fetched instruction until Execute is free.\n" +
		"So while one instruction executes, the one after it is already being fetched. When nothing gets in the way, an instruction finishes every cycle.\n" +
		_sub("Instruction tokens") +
		"Every instruction in flight is a coloured token labelled with its address and assembly, for example [code]3022  ldr r2, [lb]pc, #0x20][/code]. The token keeps its colour as it moves, so you can follow one instruction. The line under the label says what it is doing:\n" +
		"• [b]fetching[/b] — it is being read from memory this cycle.\n" +
		"• [b]waiting in IF/EX[/b] — it has been fetched, but the instruction ahead of it still needs Execute.\n" +
		"• [b]cycle k of n[/b] — it is executing; n is its cycle count from RP2040 datasheet Table 81.\n" +
		"• [b]refilling[/b] — a taken branch's last cycle, while its target is fetched.\n" +
		"• [color=" + _c("red") + "][b]✕ flushed[/b][/color] — fetched from the wrong path behind a taken branch, so it is discarded and never executes.\n" +
		"When you step, each token slides to its next position: from Fetch across IF/EX into Execute, and out to the right when it finishes. A new instruction drops into Fetch. The slide follows the speed slider.\n" +
		_sub("What the colours mean") +
		"The diagram follows the same rules as the datapath diagram:\n" +
		"• [color=" + _c("accent") + "]Bright blue wires and boxes[/color] carry data in the cycle shown, and a glowing pulse runs along them when you step.\n" +
		"• [color=" + _c("purple") + "]Purple dashed stubs[/color] are control inputs. Each enters a mux or the ALU on a [i]slanted[/i] side and is labelled with what it selects this cycle: the PC MUX's select ([code]PC+2[/code] or [code]target[/code]) and the ALU's operation ([code]ALUK[/code]).\n" +
		"• Every mux and the ALU take their inputs on the long side and send their result out of the short side. The +2 adder is drawn sideways: the PC enters its long left side and PC + 2 leaves its short right side.\n" +
		"• [color=" + _c("red") + "]FLUSH[/color] is a control line from BRANCH RESOLVE to IF/EX. It is dashed, and turns red when a taken branch discards the instruction in Fetch. IF/EX then shows ✕.\n" +
		"• [color=" + _c("changed") + "]Yellow[/color] registers and flags changed in the last step.\n" +
		_sub("Registers") +
		"The simulator executes each instruction as a whole, so the register file, the flags and the Registers panel show the state [i]after[/i] the instruction in Execute has finished. The datapath diagram is different: it shows values during the cycle.\n" +
		_sub("Learning the components") +
		"Hover a block for a one-line summary, or click it for a card explaining what it does, how it works and what it connects to. The same descriptions are in " + _link("pipecomponents", "Pipeline components") + ". " + _link("pipecycles", "Pipeline cycle by cycle") + " explains stepping, the instruction strip and where the pipeline loses cycles.")

	_topic("pipecycles", "Pipeline cycle by cycle", _h("Pipeline cycle by cycle") +
		_sub("Stepping") +
		"• " + _k("Step Cycle") + " (" + _k("F11") + ") — one pipeline clock cycle: every token moves on one stage.\n" +
		"• " + _k("Step Insn") + " (" + _k("F10") + ") — jump to the next instruction's first Execute cycle.\n" +
		"• " + _k("Run") + " (" + _k("F5") + ") — run at the speed slider's rate in pipeline cycles per second. At high speeds the animation is skipped.\n" +
		"• Breakpoints stop the run when the instruction is in Fetch, just before it executes.\n" +
		"The status bar and the diagram's title show the pipeline cycle, the instructions finished so far, and the CPI (cycles per instruction).\n" +
		_sub("The instruction strip") +
		"Under the stages are the last 8 cycles: one row per instruction, one column per cycle, with the current cycle highlighted. Blue cells are [color=" + _c("stage_f") + "]Fetch[/color] and green cells are [color=" + _c("stage_e") + "]Execute[/color]. An outlined cell means [i]waiting[/i] in IF/EX, or a branch [i]refilling[/i]. A ✕ cell is a flushed instruction. When nothing stalls, every row starts one column after the one above it. This is the staircase pattern of a pipeline: each instruction is one step behind the one before.\n" +
		_sub("The explanation panel") +
		"In pipeline mode, the cycle inspector under the diagram is replaced by a panel that explains the cycle shown. It says what each stage holds, its Table 81 row and cycle count, and why it waits, refills or is flushed.\n" +
		_sub("Where the pipeline loses cycles") +
		"A perfect 2-stage pipeline finishes one instruction per cycle (CPI = 1). Two things break the pattern:\n" +
		"• [b]Instructions that need Execute for more than one cycle.[/b] Loads and stores take 2 cycles over the AHB-Lite bus (1 to the SIO), PUSH and POP take 1 + the number of registers, and BL takes 3. The next instruction waits in IF/EX meanwhile.\n" +
		"• [b]Taken branches.[/b] The M0+ has no branch predictor. Fetch always assumes the instruction it just fetched will not branch, and fetches the next address (\"predict not-taken\"). When a branch in Execute turns out to be taken, BRANCH RESOLVE sends the target to the PC MUX and FLUSHes the wrong instruction from IF/EX. The branch then spends one more cycle while the target is fetched. That is why a B<cond> costs 1 cycle when it is not taken and 2 when it is taken, and B and BX always cost 2.\n" +
		"There are no [b]data hazards[/b]: with only one Execute stage, an instruction's result is written back before the next instruction reads its registers. So the M0+ needs no forwarding and no stalls for data.\n" +
		_sub("Example: the bottom of a loop") +
		"[code]cmp  r3, r2      @ 1 cycle\nbls  loop        @ taken: 2 cycles[/code]\n" +
		"• Cycle 1: [code]cmp[/code] executes, [code]bls[/code] is fetched.\n" +
		"• Cycle 2: [code]bls[/code] executes and the condition is true. The instruction after it (fetched by predict-not-taken) is flushed ✕, and the PC MUX selects the target.\n" +
		"• Cycle 3: [code]bls[/code] refills: the first instruction of [code]loop[/code] is fetched.\n" +
		"• Cycle 4: that instruction executes.\n" +
		"Each pass round the loop pays one extra cycle for the taken branch. The " + _link("performance", "Performance window") + " totals these costs and compares them with a 3-stage pipeline, which loses 2 cycles per taken branch.")

	_add_pipe_components()

	_topic("pipewalk", "Pipeline walkthrough", "")   # built in _show_pipewalk

	_section("Measuring performance")

	_topic("performance", "Performance window", _h("Performance window") +
		"Click " + _k("Performance") + " in the toolbar (" + _k("F2") + "). The window counts every instruction your program has completed since it was loaded and updates while it runs. The same run is timed on three machines; they execute the same instructions with the same results, so only the number of clock cycles differs.\n" +
		"• [b]Multi-cycle[/b] — the teaching datapath on the CPU diagram: one instruction at a time, 5–10 cycles each.\n" +
		"• [b]2-stage pipeline (RP2040)[/b] — the Raspberry Pi Pico's Cortex-M0+: the next instruction is fetched while the current one decodes and executes. To watch it run, see " + _link("pipeline", "Pipeline diagram") + ".\n" +
		"• [b]3-stage pipeline[/b] — a teaching design with separate fetch, decode and execute stages.\n" +
		_sub("Metrics") +
		"Cycles, CPI (cycles per instruction) and time at the RP2040's default 125 MHz for each machine; for the multi-cycle machine, the cycles spent in each phase and how often the bus, the ALU and memory are busy; the instruction mix; where data accesses went (RAM, the SIO, APB peripherals); branch statistics; and the instructions that cost the most cycles.\n" +
		_sub("Compare") +
		"Change the design and see the new cycle counts next to the stock ones: the [b]multiplier[/b] (the Cortex-M0+ can be built with a fast 1-cycle or a small 32-cycle multiplier; the RP2040 has the fast one) and [b]memory wait states[/b] for the multi-cycle machine (every memory access takes longer).\n" +
		_sub("Pipeline chart") +
		"The last instructions on the 2-stage (or 3-stage) pipeline, one row per instruction and one column per clock cycle: " + _k("F") + " fetch, " + _k("D") + " decode, " + _k("E") + " execute, " + _k("R") + " a branch's refill cycle, " + _k("✕") + " an instruction fetched from the wrong path and discarded after a taken branch. Click a column (or use " + _k("◀ Cycle") + " / " + _k("Cycle ▶") + ") to see what each stage holds in that cycle and why it takes the cycles it does.\n" +
		_sub("Where the numbers come from") +
		"• RP2040 cycle counts are the [b]RP2040 datasheet, Table 81[/b] (Cortex-M0+ instruction summary), which assumes zero wait states — code and data in SRAM, or the flash cache hitting. For example ADDS = 1, LDR/STR = 2 (or 1 to the SIO's single-cycle I/O port), B<cond> = 1 not taken / 2 taken, B = 2, BL = 3, PUSH = 1 + N, POP {…, PC} = 3 + N.\n" +
		"• The 3-stage design uses the same execute cycles and adds one refill cycle per taken branch, because it has one more stage to refill.\n" +
		"• Not timed, and listed separately instead of guessed: SVC and BKPT (the datasheet gives no fixed count), and the extra bus cycles of APB peripheral accesses (datasheet §2.1.3) — the window tells you how many there were.\n" +
		"• The exact cycle in which the real Cortex-M0+ fetches is not published (it fetches up to 32 bits ahead), so the chart's F marks when an instruction is in this model's fetch stage; the execute-stage counts are the datasheet's.\n")

	_section("Real hardware")

	_topic("hardware", "Running on a real Pico", _h("Running on a real Pico") +
		"The same [code].s[/code] file can run on a Raspberry Pi Pico connected by USB.\n" +
		_sub("Steps") +
		"1. Choose [b]Pico (USB serial)[/b] in the toolbar's mode menu.\n" +
		"2. Click " + _k("Flash") + ". picosim builds a [code].uf2[/code] with the Pico SDK (downloaded on first use, so the first build is slow) and uploads it. It uses [code]picotool[/code] if installed, otherwise copies to a mounted [code]RPI-RP2[/code] drive — hold BOOTSEL while plugging the Pico in to get that drive.\n" +
		"3. After flashing, pick the Pico's port (marked [i](Pico)[/i]; " + _k("⟳") + " refreshes the list) and click " + _k("Connect") + ". The console now talks to the board.\n\n" +
		"Stepping, the diagram and the registers only work in [b]Simulator[/b] mode — switch back to it to debug.")

	_section("Help")

	_topic("keys", "Keyboard shortcuts", _h("Keyboard shortcuts") +
		"[table=2]" +
		_row("F1", "open this guide") + _row("F11", "step one clock cycle (hold to repeat)") +
		_row("F10", "step one instruction (hold to repeat)") + _row("F5", "run / pause") +
		_row("F2", "open the Performance window") +
		_row("Ctrl/Cmd+O", "open a .s file") + _row("Ctrl/Cmd+R", "reload (re-assemble and reset)") +
		_row("Ctrl/Cmd + / −", "zoom in / out") + _row("Ctrl/Cmd 0", "reset zoom") +
		_row("Esc", "close the enlarged Pico board, a component card, or this guide") +
		"[/table]\n\nOn the 2-stage pipeline, F11 steps one pipeline cycle and F10 jumps to the next instruction's first Execute cycle. F5, F10 and F11 only work in Simulator mode. While you are typing in the serial console, other keys go to your program.")

	_topic("trouble", "Troubleshooting", _h("Troubleshooting") +
		_sub("Nothing happens when I press Step or Run") +
		"The program has halted (the status bar says [i]halted[/i]) — press " + _k("Reload") + ". Or you are in Pico mode — switch to Simulator. Or no program is loaded.\n" +
		_sub("The diagram shows the wrong machine") +
		"The machine menu next to " + _k("Show CPU Diagram") + " picks it: " + _k("Multi-cycle") + " for the datapath with the cycle inspector, " + _k("2-stage pipeline") + " for the Pico's pipeline.\n" +
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
	_wt_cycle = 0
	_wt_shots.clear()
	_wt_order.clear()
	if win.visible and _shown == "walkthrough":
		_show_walkthrough()


## The walkthrough page may be open before the program has finished loading.
func _on_state(st: Dictionary) -> void:
	if win.visible and _shown == "walkthrough" and _wt.is_empty() and not _wt_waiting \
			and st.get("loaded", false):
		_show_walkthrough()
	if win.visible and _shown == "pipewalk" and _pw.is_empty() and not _pw_waiting \
			and st.get("loaded", false):
		_show_pipewalk()


func _on_walkthrough(data: Dictionary) -> void:
	_wt_waiting = false
	_wt = data
	_wt_shots.clear()
	_wt_order.clear()
	if _wt_demo >= 0 and _shown == "walkthrough":
		_wt_step = mini(_wt_demo, data["steps"].size() - 1)
		_wt_demo = -1
	if _shown == "walkthrough":
		_show_walkthrough()


## Show step i (-1: the list), at its first cycle, or its last with `at_end`.
func _wt_goto(i: int, at_end := false) -> void:
	if i >= _wt.get("steps", []).size() or (i < 0 and _wt_step < 0):
		return
	if _wt_step < 0:
		var bar := body.get_v_scroll_bar()
		_wt_list_scroll = bar.value / maxf(1.0, bar.max_value)
	_wt_step = maxi(i, -1)
	_wt_cycle = 999 if at_end else 0   # clamped to the last cycle when drawn
	_show_walkthrough()
	if _wt_step < 0:
		_restore_scroll.call_deferred(_wt_list_scroll)


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
		(func(): body.scroll_to_line(0)).call_deferred()   # tables lay out a frame late
	else:
		_wt_detail(_wt_step)


## Heading shown above a step that starts at a label; `n_pass` > 1 says the
## code under it is running again (a loop going round).
func _wt_heading(step: Dictionary, n_pass := 1) -> String:
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
	if n_pass > 1:
		note += " · pass %d" % n_pass
	return "\n[font_size=%d][color=%s][b]%s[/b][/color][/font_size]   [color=%s]%s[/color]\n" % [
		roundi(17 * text_scale), _c("label"), lab, _c("dim"), note]


func _wt_overview() -> String:
	var steps: Array = _wt["steps"]
	var total := 0
	for st in steps:
		total += st["cycles"].size()
	var t := _h("Program walkthrough")
	t += "[color=%s][code]%s[/code][/color]\n\n" % [_c("dim"), _esc(str(_wt["path"]).get_file())]
	t += "Your program one instruction at a time, in the order the CPU runs it, with what each one does to the real values. [b]Click an instruction[/b] to watch its clock cycles on the CPU diagram.\n\n"
	if steps.size() > 0:
		t += "[table=3]" + _stat(str(steps.size()), "instructions") + _stat(str(total), "clock cycles") + \
			_stat("%.1f" % (float(total) / steps.size()), "cycles per instruction") + "[/table]\n\n"
	t += "[color=%s]This runs a separate copy of the program, so the main window is not changed. Values are hexadecimal.[/color]\n" % _c("dim")
	var notes := []
	for s in steps:
		notes.append("%d cycles" % s["cycles"].size())
	t += _walk_table("wt", steps, notes)
	t += "\n" + _sub("Where it stops") + _esc(str(_wt["stopped"])) + "\n"
	if str(_wt["output"]) != "":
		t += "\nPrinted to the serial console so far: [code]%s[/code]\n" % _esc(str(_wt["output"]).c_escape())
	return t


## All the steps as one table, so the columns line up all the way down. A
## label gets a heading row, and one seen before (a loop going round again)
## says which pass this is.
func _walk_table(pre: String, steps: Array, notes: Array) -> String:
	var passes := {}
	var t := "\n[table=4]"
	for i in steps.size():
		var s: Dictionary = steps[i]
		var lab := str(s["label"])
		if lab != "":
			passes[lab] = passes.get(lab, 0) + 1
			var hd := _wt_heading(s, passes[lab]).strip_edges()
			var top := 18 if i > 0 else 4
			t += "[cell][/cell][cell padding=4,%d,8,4]%s[/cell][cell padding=4,%d,8,4]%s[/cell][cell][/cell]" % [
				top, hd.get_slice("   ", 0), top, hd.get_slice("   ", 1)]
		t += _walk_row(pre, i, s, notes[i])
	return t + "[/table]\n"


## A big number with a caption under it, as a table cell.
func _stat(big: String, what: String) -> String:
	return "[cell bg=%s padding=14,8,28,8][font_size=%d][b]%s[/b][/font_size]\n[color=%s]%s[/color][/cell]" % [
		_c("panel_alt"), roundi(22 * text_scale), big, _c("dim"), what]


## One instruction in the overview: number, instruction, what it does, and
## `note` (its cycles). `pre` is the link prefix: "wt" or "pw".
func _walk_row(pre: String, i: int, s: Dictionary, note: String) -> String:
	var bg := "%s,%s" % [_c("panel"), _c("panel_alt")]
	return ("[cell bg=%s padding=8,5,10,5][color=%s]%d[/color][/cell]" % [bg, _c("dim"), i + 1]) + \
		("[cell bg=%s padding=4,5,14,5][url=%s:%d][code][color=%s]%04X[/color]  [color=%s]%s[/color][/code][/url][/cell]" % [
			bg, pre, i, _c("dim"), int(s["addr"]), _c("accent"), _esc(_tidy(str(s["text"])))]) + \
		("[cell bg=%s expand=1 padding=4,5,12,5]%s[/cell]" % [bg, _esc(str(s["summary"]))]) + \
		("[cell bg=%s padding=4,5,10,5][color=%s]%s[/color][/cell]" % [bg, _c("dim"), note])


## "push     {r7, lr}" → "push  {r7, lr}": the disassembler pads mnemonics wide.
static func _tidy(insn: String) -> String:
	var sp := insn.find(" ")
	if sp < 0:
		return insn
	return insn.substr(0, sp).rpad(6) + insn.substr(sp).strip_edges()


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")


## A link drawn as a small button.
func _button(meta: String, label: String, on := false) -> String:
	if on:
		return "[bgcolor=%s][color=%s] %s [/color][/bgcolor]" % [_c("accent"), _c("bg"), label]
	return "[url=%s][bgcolor=%s][color=%s] %s [/color][/bgcolor][/url]" % [meta, _c("sig_bg"), _c("accent"), label]


func _walk_nav(pre: String, i: int, n: int) -> String:
	var t := _button(pre + ":all", "☰ All instructions") + "      "
	t += (_button("%s:%d" % [pre, i - 1], "◀ Previous") if i > 0 else "[color=%s] ◀ Previous [/color]" % _c("dim"))
	t += "  [color=%s]Instruction %d of %d[/color]  " % [_c("dim"), i + 1, n]
	t += (_button("%s:%d" % [pre, i + 1], "Next ▶") if i < n - 1 else "[color=%s] Next ▶ [/color]" % _c("dim"))
	return t + "\n"


## A row of clickable chips, one per cycle; `labels[j]` names cycle j and
## `keys[j]` is its palette colour. Cycle `k` is the one on display.
func _chips(link: String, labels: Array, keys: Array, k: int) -> String:
	var strip := ""
	for j in labels.size():
		var lbl := str(labels[j]).replace(" ", "\u00a0")
		if j == k:
			strip += "[bgcolor=%s][color=%s][b] %s [/b][/color][/bgcolor]  " % [_c(keys[j]), _c("on_stage"), lbl]
		else:
			strip += "[url=%s:%d][bgcolor=%s][color=%s] %s [/color][/bgcolor][/url]  " % [
				link, j, _c("sig_bg"), _c(keys[j]), lbl]
	return strip + "\n"


static func _phase_key(phase: String) -> String:
	if phase == "FETCH":
		return "stage_f"
	if phase == "DECODE":
		return "stage_d"
	return "stage_e"


## One step: what the instruction does, a strip of its cycles, then the
## selected cycle in detail with its diagram picture.
func _wt_detail(i: int) -> void:
	var gen := _wt_gen
	var steps: Array = _wt["steps"]
	var s: Dictionary = steps[i]
	if not _wt_shots.has(i):
		body.text = _walk_nav("wt", i, steps.size()) + "\n" + _h("[code]%s[/code]" % _esc(_tidy(str(s["text"])))) + \
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
	var k := clampi(_wt_cycle, 0, cycles.size() - 1)
	_wt_cycle = k
	var cy: Dictionary = cycles[k]
	var info: Dictionary = cy["info"]
	body.clear()
	body.append_text(_walk_nav("wt", i, steps.size()) + "\n")

	# the instruction
	body.append_text(_h("[code]%s[/code]" % _esc(_tidy(str(s["text"])))).trim_suffix("\n"))
	var where := "[color=%s]address %04X" % [_c("dim"), int(s["addr"])]
	var lab := _walk_label_at(steps, i)
	if lab != "":
		where += " · in [color=%s]%s[/color]" % [_c("label"), lab]
	body.append_text(where + "[/color]\n\n")
	body.append_text("[table=1][cell bg=%s padding=14,10,14,10][b]What it does[/b]\n%s[/cell][/table]\n\n" % [
		_c("panel_alt"), _esc(str(s["summary"]))])

	# the cycle strip
	body.append_text("[b]%d clock cycles[/b]  [color=%s]— click one, or use ← →[/color]\n" % [cycles.size(), _c("dim")])
	var labels := []
	var keys := []
	for cyc in cycles:
		labels.append("%d · %s" % [labels.size() + 1, str(cyc["info"]["state"])])
		keys.append(_phase_key(str(cyc["info"]["phase"])))
	body.append_text(_chips("wc", labels, keys, k))

	# the selected cycle
	var phase := str(info["phase"])
	body.append_text("\n[font_size=%d][b]Cycle %d of %d · %s[/b][/font_size]   [bgcolor=%s][color=%s] %s phase [/color][/bgcolor]\n" % [
		roundi(18 * text_scale), k + 1, cycles.size(), str(info["state"]),
		_c(_phase_key(phase)), _c("on_stage"), phase.capitalize()])
	body.append_text(_esc(str(cy.get("what", cy["text"]))) + "\n\n")
	var w := clampf(body.size.x - 70.0, 360.0, 1400.0)
	if k < shots.size() and shots[k] != null:
		body.add_image(shots[k], int(w), int(w * float(SHOT_SIZE.y) / SHOT_SIZE.x))
		body.append_text("\n[color=%s]The diagram [i]during[/i] this cycle: boxes still hold their old values, bright wires carry data, and yellow [code]old → new[/code] captions show what the clock edge loads.[/color]\n" % _c("dim"))

	var how: Array = cy.get("how", [])
	if not how.is_empty():
		body.append_text(_sub("How the datapath does it"))
		for sentence in how:
			body.append_text("•  %s\n" % _esc(str(sentence)))
	var edge: Array = cy.get("edge", [])
	if not edge.is_empty():
		body.append_text(_sub("At the clock edge"))
		for e in edge:
			body.append_text("•  %s\n" % _edge_bb(str(e)))
	var sigs := ""
	for sg in info["signals"]:
		sigs += "[bgcolor=%s] [code]%s[/code] [/bgcolor]  " % [_c("sig_bg"), _esc(str(sg))]
	body.append_text(_sub("Control signals"))
	body.append_text((sigs if sigs != "" else "[color=%s](none)[/color]" % _c("dim")) + "\n\n")

	# on to the next cycle / instruction
	var nxt := ""
	if k > 0:
		nxt += _button("wc:%d" % (k - 1), "◀ Cycle %d" % k) + "  "
	if k < cycles.size() - 1:
		nxt += _button("wc:%d" % (k + 1), "Cycle %d ▶" % (k + 2))
	elif i < steps.size() - 1:
		nxt += _button("wt:%d" % (i + 1), "Next instruction: %s ▶" % _esc(_tidy(str(steps[i + 1]["text"]))))
	body.append_text(nxt + "\n\n" + KEYS_HINT % _c("dim"))
	body.scroll_to_line(0)


## "PC 0x00003000 → 0x00003002": the new value in the "changed" colour.
func _edge_bb(e: String) -> String:
	var a := e.find(" → ")
	if a < 0:
		return _esc(e)
	return "%s → [color=%s][b]%s[/b][/color]" % [_esc(e.substr(0, a)), _c("changed"), _esc(e.substr(a + 3))]


## The label whose code step i is in (the nearest one at or before it).
static func _walk_label_at(steps: Array, i: int) -> String:
	for j in range(i, -1, -1):
		if str(steps[j]["label"]) != "":
			return str(steps[j]["label"])
	return ""


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
	var t := _h("Datapath components") + "Every block on the multi-cycle " + _link("diagram", "datapath diagram") + ", in the order data usually flows. Click a block on the diagram to see the same text next to it.\n"
	for id in info.ORDER:
		t += "\n[color=%s]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/color]\n\n" % _c("dim")
		t += info.bbcode(id, names, roundi(17 * text_scale)) + "\n"
	_topic("components", "Datapath components", t)


func _add_pipe_components() -> void:
	if not ResourceLoader.exists(INFO_PATH):
		return
	var info = load(INFO_PATH)
	var names := {}
	for id in info.PIPE_INFO:
		names[id] = str(info.PIPE_INFO[id]["title"]).get_slice(" — ", 0)
	var t := _h("Pipeline components") + "Every block on the " + _link("pipeline", "pipeline diagram") + ", Fetch stage first, in the order an instruction meets them. Click a block on the diagram to see the same text next to it.\n"
	for id in info.PIPE_ORDER:
		t += "\n[color=%s]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/color]\n\n" % _c("dim")
		t += info.entry_bbcode(info.PIPE_INFO[id], names, roundi(17 * text_scale)) + "\n"
	_topic("pipecomponents", "Pipeline components", t)


# ── pipeline walkthrough ────────────────────────────────────────────────────
# The same idea as the program walkthrough, on the 2-stage pipeline: one
# step per instruction, with a picture of the pipeline in every cycle the
# instruction spends in Execute, drawn off-screen by pipeline_view.gd.

func _pw_reset() -> void:
	_pw = {}
	_pw_waiting = false
	_pw_step = -1
	_pw_cycle = 0
	_pw_shots.clear()
	_pw_order.clear()
	if win.visible and _shown == "pipewalk":
		_show_pipewalk()


func _on_pipewalk(data: Dictionary) -> void:
	_pw_waiting = false
	_pw = data
	_pw_shots.clear()
	_pw_order.clear()
	if _wt_demo >= 0 and _shown == "pipewalk":
		_pw_step = mini(_wt_demo, data["steps"].size() - 1)
		_wt_demo = -1
	if _shown == "pipewalk":
		_show_pipewalk()


func _show_pipewalk() -> void:
	_wt_gen += 1
	var st := Backend.state
	var h := _h("Pipeline walkthrough")
	if not st.get("loaded", false):
		body.text = h + "Load a program (" + _k("Open…") + ") and this page shows it running on the 2-stage pipeline, step by step."
		return
	if _pw.is_empty() or str(_pw.get("path", "")) != str(st.get("path", "")):
		if not _pw_waiting:
			_pw_waiting = true
			Backend.send("pipe_walkthrough")
		body.text = h + "[color=%s]Working through %s…[/color]" % [_c("dim"), str(st.get("path", "")).get_file()]
		return
	var steps: Array = _pw["steps"]
	if _pw_step < 0 or _pw_step >= steps.size():
		_pw_step = -1
		body.text = _pw_overview()
		body.scroll_to_line(0)
		(func(): body.scroll_to_line(0)).call_deferred()   # tables lay out a frame late
	else:
		_pw_detail(_pw_step)


## Execute cycles of step i (the first step also shows the very first fetch).
func _pw_exec(i: int) -> int:
	return _pw["steps"][i]["cycles"].size() - (1 if i == 0 else 0)


func _pw_overview() -> String:
	var steps: Array = _pw["steps"]
	var total := 0
	var taken := 0
	var notes := []
	for i in steps.size():
		var n := _pw_exec(i)
		total += n
		var note := "%d cycle%s" % [n, "" if n == 1 else "s"]
		if bool(steps[i]["flushes"]):
			taken += 1
			note += " · [color=%s]flush[/color]" % _c("red")
		notes.append(note)
	var t := _h("Pipeline walkthrough")
	t += "[color=%s][code]%s[/code][/color]\n\n" % [_c("dim"), _esc(str(_pw["path"]).get_file())]
	t += "Your program on the Raspberry Pi Pico's 2-stage pipeline (see " + _link("pipeline", "Pipeline diagram") + "): while one instruction executes, the next is already being fetched. [b]Click an instruction[/b] to watch each cycle it spends in Execute.\n\n"
	if steps.size() > 0:
		t += "[table=4]" + _stat(str(steps.size()), "instructions") + _stat(str(total), "pipeline cycles") + \
			_stat("%.2f" % (float(total) / steps.size()), "cycles per instruction") + \
			_stat(str(taken), "pipeline flushes") + "[/table]\n\n"
		t += "A perfect pipeline would take one cycle per instruction (CPI 1); the extra cycles come from instructions that need Execute for longer and from taken branches, which flush the pipeline. The " + _link("walkthrough", "Program walkthrough") + " runs the same instructions on the multi-cycle datapath.\n\n"
	t += "[color=%s]This runs a separate copy of the program, so the main window is not changed. Values are hexadecimal.[/color]\n" % _c("dim")
	t += _walk_table("pw", steps, notes)
	t += "\n" + _sub("Where it stops") + _esc(str(_pw["stopped"])) + "\n"
	return t


## Show step i (-1: the list), at its first cycle, or its last with `at_end`.
func _pw_goto(i: int, at_end := false) -> void:
	if i >= _pw.get("steps", []).size() or (i < 0 and _pw_step < 0):
		return
	if _pw_step < 0:
		var bar := body.get_v_scroll_bar()
		_pw_list_scroll = bar.value / maxf(1.0, bar.max_value)
	_pw_step = maxi(i, -1)
	_pw_cycle = 999 if at_end else 0   # clamped to the last cycle when drawn
	_show_pipewalk()
	if _pw_step < 0:
		_restore_scroll.call_deferred(_pw_list_scroll)


## Chip label and colour for cycle k of step i.
func _pw_chip(i: int, k: int, view: Dictionary) -> Array:
	var e = view.get("E")
	if e == null:
		return ["cycle %d · first fetch" % int(view["cycle"]), "stage_f"]
	var lbl := "cycle %d · Execute %d/%d" % [int(view["cycle"]), int(e["k"]), int(e["cycles"])]
	if bool(e["redirect"]):
		return [lbl + " · taken", "red"]
	if bool(e["refilling"]):
		return [lbl + " · refill", "stage_d"]
	return [lbl, "stage_e"]


## One step: what the instruction does and why it takes the cycles it does,
## a strip of its cycles, then the selected cycle with its pipeline picture.
func _pw_detail(i: int) -> void:
	var gen := _wt_gen
	var steps: Array = _pw["steps"]
	var s: Dictionary = steps[i]
	if not _pw_shots.has(i):
		body.text = _walk_nav("pw", i, steps.size()) + "\n" + _h("[code]%s[/code]" % _esc(_tidy(str(s["text"])))) + \
			"[color=%s]Drawing the pipeline for each cycle…[/color]" % _c("dim")
		var rendered: Array = await _pw_render(s, gen)
		if gen != _wt_gen:
			return
		_pw_shots[i] = rendered
		_pw_order.append(i)
		while _pw_order.size() > SHOT_CACHE:
			_pw_shots.erase(_pw_order.pop_front())
	var shots: Array = _pw_shots[i]
	var cycles: Array = s["cycles"]
	var n := _pw_exec(i)
	var k := clampi(_pw_cycle, 0, cycles.size() - 1)
	_pw_cycle = k
	var view: Dictionary = cycles[k]["view"]
	var PV = load(PIPELINE_PATH)
	body.clear()
	body.append_text(_walk_nav("pw", i, steps.size()) + "\n")

	# the instruction
	body.append_text(_h("[code]%s[/code]" % _esc(_tidy(str(s["text"])))).trim_suffix("\n"))
	var where := "[color=%s]address %04X" % [_c("dim"), int(s["addr"])]
	var lab := _walk_label_at(steps, i)
	if lab != "":
		where += " · in [color=%s]%s[/color]" % [_c("label"), lab]
	body.append_text(where + "[/color]\n\n")
	var how := "[b]%d cycle%s in Execute[/b] (RP2040 datasheet Table 81, row [i]%s[/i])" % [
		n, "" if n == 1 else "s", _esc(str(s["row"]))]
	if bool(s["flushes"]):
		how += ". It changes the PC, so the instruction fetched behind it is [color=%s]flushed[/color], and its last cycle refills the pipeline from the new address." % _c("red")
	elif n > 1:
		how += ", so the instruction behind it waits in IF/EX."
	else:
		how += ": the pipeline keeps finishing one instruction per cycle."
	body.append_text("[table=1][cell bg=%s padding=14,10,14,10][b]What it does[/b]\n%s\n\n[b]On the pipeline[/b]\n%s[/cell][/table]\n\n" % [
		_c("panel_alt"), _esc(str(s["summary"])), how])

	# the cycle strip
	var labels := []
	var keys := []
	for j in cycles.size():
		var ch := _pw_chip(i, j, cycles[j]["view"])
		labels.append(ch[0])
		keys.append(ch[1])
	body.append_text("[b]%d cycle%s[/b]  [color=%s]— click one, or use ← →[/color]\n" % [
		cycles.size(), "" if cycles.size() == 1 else "s", _c("dim")])
	body.append_text(_chips("pc", labels, keys, k))

	# the selected cycle
	var ch := _pw_chip(i, k, view)
	var title := "Pipeline cycle %d" % int(view["cycle"])
	var badge := "before the first instruction executes"
	if view.get("E") != null:
		badge = "Execute %d of %d" % [int(view["E"]["k"]), int(view["E"]["cycles"])]
		if ch[1] == "red":
			badge += " · branch taken"
		elif ch[1] == "stage_d":
			badge += " · refilling"
	body.append_text("\n[font_size=%d][b]%s[/b][/font_size]   [bgcolor=%s][color=%s] %s [/color][/bgcolor]\n\n" % [
		roundi(18 * text_scale), title, _c(ch[1]), _c("on_stage"), badge])
	var w := clampf(body.size.x - 70.0, 360.0, 1400.0)
	if k < shots.size() and shots[k] != null:
		body.add_image(shots[k], int(w), int(w * float(SHOT_SIZE.y) / SHOT_SIZE.x))
		body.append_text("\n")
	var st: Dictionary = PV.stage_text(view)
	body.append_text("[table=2][cell bg=%s padding=12,8,12,10 expand=1][color=%s][b]Fetch[/b][/color]\n%s[/cell][cell bg=%s padding=12,8,12,10 expand=1][color=%s][b]Decode + Execute[/b][/color]\n%s[/cell][/table]\n" % [
		_c("panel_alt"), _c("stage_f"), st["F"], _c("panel_alt"), _c("stage_e"), st["E"]])
	body.append_text("[color=%s]The simulator runs each instruction as a whole, so the registers in the picture show the state after this instruction has finished.[/color]\n\n" % _c("dim"))

	# on to the next cycle / instruction
	var nxt := ""
	if k > 0:
		nxt += _button("pc:%d" % (k - 1), "◀ Previous cycle") + "  "
	if k < cycles.size() - 1:
		nxt += _button("pc:%d" % (k + 1), "Next cycle ▶")
	elif i < steps.size() - 1:
		nxt += _button("pw:%d" % (i + 1), "Next instruction: %s ▶" % _esc(_tidy(str(steps[i + 1]["text"]))))
	body.append_text(nxt + "\n\n" + KEYS_HINT % _c("dim"))
	body.scroll_to_line(0)


## Draw the pipeline once per cycle of `step` and return the pictures.
func _pw_render(step: Dictionary, gen: int) -> Array:
	if _pshot_vp == null:
		_pshot_vp = SubViewport.new()
		_pshot_vp.size = SHOT_SIZE
		_pshot_vp.render_target_update_mode = SubViewport.UPDATE_DISABLED
		add_child(_pshot_vp)
		_pshot_view = load(PIPELINE_PATH).new()
		_pshot_view.still = true
		_pshot_view.size = Vector2(SHOT_SIZE)
		_pshot_vp.add_child(_pshot_view)
		await get_tree().process_frame
	var out := []
	for cy in step["cycles"]:
		if gen != _wt_gen:
			return out
		var post: Dictionary = cy["post"]
		_pshot_view.update_state({"loaded": true, "regs": post["regs"], "flags": post["flags"],
			"pipe": cy["view"], "running": false, "changed": [], "cps": 0})
		_pshot_view.queue_redraw()
		_pshot_vp.render_target_update_mode = SubViewport.UPDATE_ONCE
		await RenderingServer.frame_post_draw
		await RenderingServer.frame_post_draw
		var img := _pshot_vp.get_texture().get_image()
		out.append(ImageTexture.create_from_image(img) if img != null else null)
	return out
