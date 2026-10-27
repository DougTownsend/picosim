extends Node
## User guide window: a list of topics on the left, the selected topic on
## the right.  Opened from the toolbar's "Guide" button or with F1.
##
## Self-contained: main.gd only creates it if this file exists, so deleting
## guide.gd removes the Guide button and nothing else.  The "CPU diagram
## components" topic is built from component_info.gd when that file exists,
## and "FSM states" from state_info.gd.
##
## Topics are grouped under section headings in the list (see _section).
## The "Pipeline components" topic is built the same way from PIPE_INFO.
##
## The walkthroughs of the loaded program (every step, and where you are)
## are not here: they sit beside the CPU diagram (walk_panel.gd).
##
## Making the window bigger than it opened at makes the text bigger with it
## (by the square root of the area, up to 3×); it never drops below normal.
##
## The topic list follows the Level menu (level.gd): a section or topic above
## the current level is left out of the list, but its page still opens from a
## link, so nothing a lower-level page mentions is ever a dead end.
##
## Every page opens with a short version (two paragraphs at most, see
## _summaries) that covers what a student must know; "Show the full details"
## folds out the rest of the page: step-by-step instructions, every option
## and the exact values.  Which pages are unfolded is kept until picosim closes.

const INFO_PATH := "res://scripts/component_info.gd"
const STATES_PATH := "res://scripts/state_info.gd"
const TEXT_SCALE_MAX := 3.0
const ZWSP := "\u200b"
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
var _sec_level := 1               # level of the section being added (see _section)
var _sec_name := ""               # name of the section being added
var _collapsed := {}              # section name → true: its topics are folded away
var _sums := {}                   # topic id → its short version (see _summaries)
var _unfolded := {}               # topic id → true: its full details are shown
var _unfold_all := false          # --guide-details (screenshots): every page unfolded


func _ready() -> void:
	add_to_group("guide")   # help_button.gd finds the guide here
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
	topics.custom_minimum_size.x = 240
	_fonts.append([topics, "font_size", 16])
	topics.item_selected.connect(_on_topic_clicked)
	var cfg := ConfigFile.new()
	if cfg.load(Level.SETTINGS) == OK:
		for name in cfg.get_value("guide", "collapsed", PackedStringArray()):
			_collapsed[name] = true
	split.add_child(topics)

	body = RichTextLabel.new()
	body.bbcode_enabled = true
	body.selection_enabled = true
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_theme_font_override("mono_font", Backend.mono_font())
	_fonts.append([body, "normal_font_size", 18])
	_fonts.append([body, "bold_font_size", 18])
	_fonts.append([body, "mono_font_size", 17])
	for f in _fonts:
		f[0].add_theme_font_size_override(f[1], f[2])
	body.meta_clicked.connect(_on_meta)
	body.meta_underlined = false   # _link underlines itself; buttons do not
	split.add_child(body)

	Level.changed.connect(_rebuild)
	Backend.facts_received.connect(_rebuild)    # cycle counts quoted in the text

	_apply_palette()
	Palette.changed.connect(_apply_palette)
	var args := OS.get_cmdline_user_args()
	var i := args.find("--guide")
	if i >= 0:
		open.call_deferred(args[i + 1] if i + 1 < args.size() else "")
	var k := args.find("--guide-scroll")    # screenshots: scroll the page to line N
	if k >= 0 and k + 1 < args.size():
		get_tree().create_timer(1.0).timeout.connect(func(): body.scroll_to_line(int(args[k + 1])))
	_unfold_all = args.has("--guide-details")
	var fo := args.find("--guide-fold")     # screenshots: click section heading NAME
	if fo >= 0 and fo + 1 < args.size():
		get_tree().create_timer(0.5).timeout.connect(func():
			topics.item_selected.emit(_ids.find("sec:" + args[fo + 1])))


## Show the guide, optionally at a topic id.
func open(topic := "") -> void:
	if topic != "" and _text.has(topic):
		_select(topic)
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
	topics.custom_minimum_size.x = 240 * s
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
	bg.add_theme_stylebox_override("panel", Palette.panel_box("bg", 0, 10))
	var bsb := Palette.panel_box("panel", 6, 12)
	bsb.content_margin_left = 16
	bsb.content_margin_right = 16
	body.add_theme_stylebox_override("normal", bsb)
	# the topic list: the same rounded panel as the page and the main
	# window's panels, no grid lines, rounded highlights
	topics.add_theme_stylebox_override("panel", Palette.panel_box("panel", 6, 8))
	topics.add_theme_stylebox_override("focus", StyleBoxEmpty.new())
	for s in ["selected", "selected_focus", "hovered_selected", "hovered_selected_focus"]:
		topics.add_theme_stylebox_override(s, Palette.panel_box("box_active", 4, 4))
	topics.add_theme_stylebox_override("hovered", Palette.panel_box("panel_alt", 4, 4))
	for s in ["cursor", "cursor_unfocused"]:
		topics.add_theme_stylebox_override(s, StyleBoxEmpty.new())
	topics.add_theme_color_override("guide_color", Color.TRANSPARENT)
	topics.add_theme_color_override("font_color", Palette.c("text"))
	topics.add_theme_color_override("font_selected_color", Palette.c("text"))
	topics.add_theme_color_override("font_hovered_color", Palette.c("text"))
	topics.add_theme_constant_override("v_separation", 5)
	_rebuild()


## Rebuild every topic's text (colours and heading sizes are baked into it),
## keeping the shown topic and roughly the same scroll position.
## A cycle count from the simulator's own cycle table (picosim/cycles.py),
## by the row's name there; "?" until the server has sent the table.
func _cyc(row: String) -> String:
	var n = Backend.facts.get("cycles", {}).get(row)
	return "?" if n == null else str(int(n))


## "B 5, ADDS, MOVS and CMP 6, …", worked out by the simulator.
func _typical_cycles() -> String:
	return "B %s, ADDS, MOVS and CMP %s, LDR and STR %s, BL %s, and PUSH or POP of 2 registers %s" % [
		_cyc("B label"), _cyc("ADDS R2, R0, R1"), _cyc("LDR R2, [R0, #4]"), _cyc("BL label"), _cyc("PUSH {R4, LR}")]


func _rebuild() -> void:
	var shown := _shown
	var bar := body.get_v_scroll_bar()
	var pos := bar.value / maxf(1.0, bar.max_value)
	var list_pos := topics.get_v_scroll_bar().value
	_ids.clear()
	_text.clear()
	topics.clear()
	_add_topics()
	if shown == "":
		shown = "start"
	_select(shown)
	_show(shown)
	_restore_scroll.call_deferred(pos)
	(func(): topics.get_v_scroll_bar().value = list_pos).call_deferred()


func _restore_scroll(pos: float) -> void:
	var bar := body.get_v_scroll_bar()
	bar.value = pos * bar.max_value


## BBCode colour for a palette key.
static func _c(key: String) -> String:
	return Palette.hex(key)


func _show(id: String) -> void:
	_shown = id
	body.text = _page(id)
	body.scroll_to_line(0)


## A page as shown: its heading, the short version, then the fold-out with
## the full details.  Pages without a short version are shown whole.
func _page(id: String) -> String:
	var full: String = _text[id]
	var sum := str(_sums.get(id, ""))
	if sum == "":
		return full
	const END := "[/font_size]\n\n"        # the end of the heading (_h)
	var cut := full.find(END)
	var head := full.substr(0, cut + END.length()) if cut >= 0 else ""
	var rest := full.substr(cut + END.length()) if cut >= 0 else full
	var t := head + sum.strip_edges() + "\n\n"
	if _unfolded.has(id) or _unfold_all:
		t += _fold(id, true) + "\n\n" + rest.strip_edges() + "\n\n" + _fold(id, true)
	else:
		t += _fold(id, false) + "   [color=%s]step-by-step instructions, every option and the exact values[/color]" % _c("dim")
	return t


func _fold(id: String, open: bool) -> String:
	return _button("fold:" + id, "▾  Hide the details" if open else "▸  Show the full details")


func _on_meta(m: Variant) -> void:
	var s := str(m)
	if s.begins_with("fold:"):
		var id := s.substr(5)
		var opening := not _unfolded.has(id)
		if opening:
			_unfolded[id] = true
		else:
			_unfolded.erase(id)
		var keep := body.get_v_scroll_bar().value
		body.text = _page(id)
		# opening keeps the short version in view; closing goes back to the top
		(func(): body.get_v_scroll_bar().value = keep if opening else 0.0).call_deferred()
	else:
		open(s)


## Highlight topic `id` in the list, or nothing if it is not listed at this level.
func _select(id: String) -> void:
	var i := _ids.find(id)
	if i >= 0:
		topics.select(i)
	else:
		topics.deselect_all()


## Add a topic; it is listed only if its level (or its section's) is reached.
func _topic(id: String, name: String, bb: String, lvl := 0) -> void:
	_text[id] = _space_tables(_lists(bb))
	if _sums.has(id):
		_sums[id] = _space_tables(_lists(str(_sums[id])))
	if maxi(lvl, _sec_level) > Level.current or _collapsed.has(_sec_name):
		return
	_ids.append(id)
	topics.add_item("      " + name)


## A heading in the topic list: groups the topics under it. `lvl` is the
## level its topics appear at.
## Click a heading to fold its topics away (▸) or show them again (▾).
func _section(name: String, lvl := 1) -> void:
	_sec_level = lvl
	_sec_name = name
	if lvl > Level.current:
		return
	if topics.item_count > 0:     # a gap between sections
		_ids.append("")
		var gap := topics.add_item(" ")
		topics.set_item_selectable(gap, false)
		topics.set_item_tooltip_enabled(gap, false)
	_ids.append("sec:" + name)
	var folded := _collapsed.has(name)
	var i := topics.add_item(("▸  " if folded else "▾  ") + name.to_upper())
	topics.set_item_custom_fg_color(i, Palette.c("accent"))
	topics.set_item_tooltip(i, "Click to show these topics again" if folded else "Click to fold these topics away")


func _on_topic_clicked(i: int) -> void:
	var id: String = _ids[i]
	if id.begins_with("sec:"):
		_toggle_section.call_deferred(id.substr(4))   # not while the list is emitting
	elif id != "":
		_show(id)


## Fold a section's topics away or show them again; remembered in settings.cfg.
func _toggle_section(name: String) -> void:
	if _collapsed.has(name):
		_collapsed.erase(name)
	else:
		_collapsed[name] = true
	var cfg := ConfigFile.new()
	cfg.load(Level.SETTINGS)
	cfg.set_value("guide", "collapsed", PackedStringArray(_collapsed.keys()))
	cfg.save(Level.SETTINGS)
	_rebuild()


## A blank line above a table that follows a paragraph, and below every
## table, so boxes and grids do not touch the text around them.
static func _space_tables(bb: String) -> String:
	var above := RegEx.create_from_string("([.:)])\\n\\[table")
	var below := RegEx.create_from_string("\\[/table\\]\\n(?=[^\\n])")
	return below.sub(above.sub(bb, "$1\n\n[table", true), "[/table]\n\n", true)


## Lines starting with "• " or "1. ", "2. " … become [ul] / [ol] lists, so a
## wrapped line lines up under its text instead of under the bullet.
static func _lists(bb: String) -> String:
	var out := PackedStringArray()
	var kind := ""
	var items := PackedStringArray()
	var num := RegEx.create_from_string("^\\d+\\. ")
	for line in (bb + "\n").split("\n"):
		var k := ""
		var body := line
		if line.begins_with("• "):
			k = "ul"
			body = line.substr(2)
		else:
			var m := num.search(line)
			if m:
				k = "ol"
				body = line.substr(m.get_end())
		# an item that starts with markup (a coloured key) would give the
		# bullet that style too: start it with a zero-width space instead
		if k != "" and body.begins_with("["):
			body = ZWSP + body
		if k != kind and not items.is_empty():
			out.append(("[ul]" if kind == "ul" else "[ol type=1]") + "\n".join(items) + ("[/ul]" if kind == "ul" else "[/ol]"))
			items.clear()
		kind = k
		if k == "":
			out.append(line)
		else:
			items.append(body)
	return "\n".join(out).trim_suffix("\n")


func _h(s: String) -> String:
	return "[font_size=%d][b]%s[/b][/font_size]\n\n" % [roundi(26 * text_scale), s]


static func _sub(s: String) -> String:
	return "\n[color=%s][b]%s[/b][/color]\n" % [_c("accent"), s]


static func _k(s: String) -> String:
	return "[color=%s][code]%s[/code][/color]" % [_c("yellow"), s]


static func _link(id: String, s: String) -> String:
	return "[url=%s][color=%s][u]%s[/u][/color][/url]" % [id, _c("accent"), s]


## A program listing on a darker panel, the full width of the page.
static func _codeblock(s: String) -> String:
	return "[table=1][cell bg=%s border=%s padding=14,10,14,10 expand=1][code]%s[/code][/cell][/table]\n" % [
		_c("surface"), _c("edge"), _esc(s)]


## A boxed tip: `lead` in colour `key`, then the text.
static func _note(lead: String, s: String, key := "accent") -> String:
	return "[table=1][cell bg=%s border=%s padding=14,9,14,9 expand=1][color=%s][b]%s[/b][/color]   %s[/cell][/table]\n" % [
		_c("panel_alt"), _c(key), _c(key), lead, s]


## A reference table: a header row, then striped rows. Column `wide`
## takes the spare width. Cells are BBCode with no line breaks.
static func _grid(head: Array, rows: Array, wide := -1) -> String:
	var t := "[table=%d]" % head.size()
	for h in head:
		t += "[cell bg=%s padding=10,6,16,6][b]%s[/b][/cell]" % [_c("box_active"), h]
	for r in rows:
		for i in r.size():
			t += "[cell bg=%s,%s padding=10,5,16,5%s]%s[/cell]" % [
				_c("panel"), _c("panel_alt"), " expand=1" if i == wide else "", r[i]]
	return t + "[/table]\n"


## Code in a table cell or running text, in the instruction colour.
static func _asm(s: String) -> String:
	return "[color=%s][code]%s[/code][/color]" % [_c("cyan"), _esc(s)]


# ── content ─────────────────────────────────────────────────────────────────

func _add_topics() -> void:
	_sums = _summaries()
	_section("Getting started")

	var levels := ""
	for n in range(1, Level.MAX + 1):
		var here := "   [color=%s]← you are here[/color]" % _c("changed") if n == Level.current else ""
		levels += "• [b]%s[/b] — %s%s\n" % [Level.title(n), Level.ABOUT[n - 1], here]
	_topic("start", "Start here", _h("Start here") +
		"picosim runs programs written in [b]assembly language[/b] — the instructions the Raspberry Pi Pico's processor understands — and lets you watch them run one step at a time. Try anything: nothing can break, and " + _k("Reload") + " always starts your program over.\n" +
		_first_steps() +
		_note("Stuck?", "Nothing you do here can break anything, and " + _k("Reload") + " always starts the program over. " + _link("running", "How a program runs") + " explains each of the steps above.") +
		_sub("Words you will meet") +
		"• [b]Register[/b] — a small storage box inside the processor that holds one number. R0–R12 are for your values; SP, LR and PC have special jobs.\n" +
		"• [b]PC[/b] (program counter) — the address of the next instruction. Watch it move as you step.\n" +
		"• [b]Hex[/b] — numbers written in base 16 with a [code]0x[/code] in front: [code]0x2A[/code] = 42. Hover a register to see it in decimal, or switch the Registers panel's " + _k("Hex") + " menu to " + _k("Decimal") + ".\n" +
		"• [b]Flags[/b] N Z C V — four yes/no facts about the last result (negative? zero? …) that branches like [code]BNE[/code] use to decide where to go.\n" +
		"More words are in the " + _link("glossary", "Glossary") + ".\n" +
		_sub("What to read next") +
		"If you missed the first class, read these in order. Each takes a few minutes:\n" +
		"1. " + _link("tour", "The screen at a glance") + " — every button and panel you can see.\n" +
		"2. " + _link("running", "How a program runs") + " — what happens when you press Step and Run.\n" +
		"3. " + _link("numbers", "Hex, binary and ASCII") + " — how to read the numbers on screen.\n" +
		"4. " + _link("instructions", "Instructions") + " and " + _link("branches", "Flags and branches") + " — what each line of a program does.\n" +
		"5. " + _link("examples", "Level 1 examples") + " — step through them with a guide beside you.\n" +
		"6. " + _link("programs", "Writing programs") + " — write, save and run your own.\n" +
		_reading_paths() +
		_sub("Levels: see more when you are ready") +
		"The " + _k("Level") + " menu (top right) decides how much of picosim is shown. Each level adds to the one before:\n" + levels +
		"Your teacher will tell you when to change level. Changing it never changes your program. When you raise it, picosim switches on what is new (the Memory tab, the CPU diagram, the pipeline), says what appeared in a strip under the toolbar, and outlines each new button and panel with a [color=" + _c("changed") + "][b]NEW[/b][/color] tag for a few seconds.\n" +
		_note("Tip", "The list on the left gets longer at each level. Click a section's heading (such as [b]▾ GETTING STARTED[/b]) to fold its topics away, and click it again to bring them back. picosim remembers which sections you folded."))

	_topic("tour", "The screen at a glance", _tour())

	_topic("running", "How a program runs", _running())

	var programs := _h("Writing programs") + \
		"A program is a plain text file whose name ends in [code].s[/code], written in ARM [b]Thumb[/b] assembly language for the GNU assembler. Here is a complete one:\n\n" + \
		_codeblock("        .syntax unified\n        .cpu cortex-m0plus\n        .thumb\n\nmain:\n        push    {r7, lr}        @ save the return address\n        movs    r0, #'H'        @ R0 = the letter H\n        bl      putchar         @ print the letter in R0\n        movs    r0, #0\n        pop     {r7, pc}        @ return: the program halts") + \
		_sub("The parts of a program") + \
		_grid(["Part", "Example", "What it is"], [
			["Directive", _asm(".thumb"), "An order to the assembler, not an instruction for the CPU. Start every program with the same three lines shown above: they choose the Pico's instruction set."],
			["Label", _asm("main:"), "A name for the address of the next instruction, written at the start of a line and ending in a colon. Branches and calls jump to labels."],
			["Instruction", _asm("movs r0, #'H'"), "One step for the CPU: the [b]mnemonic[/b] ([code]movs[/code]) says what to do, the [b]operands[/b] ([code]r0[/code], [code]#'H'[/code]) say what to do it to. One per line."],
			["Immediate", _asm("#72") + "  " + _asm("#0x48") + "  " + _asm("#'H'"), "A constant written into the instruction, after a #. Decimal, hex and a character in quotes all mean the same number here."],
			["Comment", _asm("@ print it"), "Everything after @ is ignored by the assembler. Use comments to explain your code."],
		], 2) + \
		"Indentation and capital letters do not matter ([code]MOVS R0, #5[/code] is fine), but commas between operands do. " + _link("instructions", "Instructions") + " lists what each instruction does.\n" + \
		_sub("Edit and save") + \
		"picosim runs your file but does not edit it. Use any plain-text code editor (VS Code, for example).\n" + \
		"1. Start from an example: open it from the [code]examples[/code] folder in your editor and use [i]Save As[/i] to save a copy in your own folder, with a name ending in [code].s[/code].\n" + \
		"2. In picosim, click " + _k("Open…") + " (" + _k("Ctrl/Cmd+O") + ") and pick your file.\n" + \
		"3. Change the file in your editor and save it. picosim notices and loads it again by itself, from the beginning (the console says [code][lb]… changed on disk: reloading][/code]). " + _k("Reload") + " (" + _k("Ctrl/Cmd+R") + ") does the same by hand.\n" + \
		"If the assembler finds a mistake, a [b]Could not load program[/b] box lists each one: the line number, that line of your file, what is wrong and what usually causes it. Fix the line and save; picosim tries again as soon as you save. " + _link("trouble", "Troubleshooting") + " explains the common messages.\n" + \
		_sub("Entry point and ending") + \
		"Execution starts at [code]main:[/code] (the label [code]asm_main:[/code] also works, and is what the Pico build uses). The built-in OS calls [code]main[/code] like a function, so it should begin with [code]push {…, lr}[/code] and end with [code]pop {…, pc}[/code]. When it returns, the OS runs [code]BKPT[/code] and the simulator halts. Press " + _k("Reload") + " to run it again. See " + _link("running", "How a program runs") + ".\n" + \
		_sub("Input and output") + \
		"• [code]bl putchar[/code] — prints the character in [code]r0[/code] to the " + _link("serial", "serial console") + ".\n" + \
		"• [code]bl getchar[/code] — waits for one key typed in the console and returns it in [code]r0[/code].\n" + \
		"They behave the same on a real Pico, so the same file runs on both. Both may change R0–R3, so keep values you need afterwards in R4–R7 (and add those registers to [code]push[/code] and [code]pop[/code], as the " + _link("examples", "Count with a loop") + " example does).\n"
	if Level.at_least(2):
		programs += _sub("Settings") + \
			"Some programs have settings, such as which pin an LED is on. When a program has them, " + _k("⚙ Settings") + " appears next to " + _k("Reload") + ": pick your values and click " + _k("Apply and reload") + ". picosim remembers them for you and uses them when it runs or flashes the program; the file itself is not changed (a • on the button means you changed something). Not sure what your circuit uses? Open the [b]Buttons and LEDs[/b] example and set its FIND_PINS setting to [i]find my pins[/i].\n" + \
			"To give your own program settings, end an [code].equ[/code] line with a comment that starts with [code]<-[/code], for example [code].equ LED_PIN, 15  @ <- the GPIO number of your LED[/code]. A comment like [code]@ <- 1: wired to GND; 0: wired to 3V3[/code] becomes a menu.\n"
		programs += _sub("Memory map") + \
			"• [code]0x0000–0x2FFF[/code] — the built-in OS\n" + \
			"• [code]0x3000–0xFFFF[/code] — your code and data; the stack starts at the top of RAM ([code]SP = 0x00010000[/code]) and grows down\n" + \
			"• [code]0x10000[/code] and up — memory-mapped I/O. The GPIO registers of the RP2040 are here: SIO at [code]0xD0000000[/code], IO_BANK0 at [code]0x40014000[/code], PADS_BANK0 at [code]0x4001C000[/code] (and RESETS at [code]0x4000C000[/code]). Writing them changes the " + _link("pico", "Pico board") + "'s pins. A pin follows the SIO registers only after its IO_BANK0 CTRL register is set to function 5 (SIO), as on the real chip. Any other address stops the program with a bus fault.\n"
	programs += _sub("Like the real chip")
	if not Level.at_least(2):
		programs += "picosim runs only the instructions the Pico's Cortex-M0+ processor has, and stops with a [b]CPU fault[/b] message in the console wherever a real Pico would crash. See " + _link("trouble", "Troubleshooting") + ".\n"
	else:
		programs += "picosim runs only Cortex-M0+ (ARMv6-M) instructions and stops with an explanation wherever the Pico would crash (a HardFault): a word or halfword access to an address that is not a multiple of its size, a jump through a register to an address with bit 0 clear (mark such labels with [code].thumb_func[/code]), or an instruction the M0+ does not have. See " + _link("trouble", "Troubleshooting") + ".\n"
	_topic("programs", "Writing programs", programs)

	var chal := _h("Challenges") + \
		"Some examples end their opening comment with a [b]challenge[/b]: a small change to make, which picosim checks for you. The task is shown in a strip under the toolbar. When the program finishes, the strip turns [color=" + _c("green") + "]green[/color] if you did it. If not, it lists each check: what was wanted and what your program did instead. Change your file, save it (picosim reloads it) and run it again. A stock example fails its own challenge, because you have not changed it yet.\n" + \
		"Not every example has one: the LED and button programs never finish, so there is nothing to check at the end. " + _k("About challenges") + " opens this page and " + _k("✕") + " hides the strip until you load another program.\n" + \
		_sub("Writing your own (for teachers)") + \
		"Put the lines in the comment at the top of the file, before the first instruction:\n" + \
		"[code]@ Challenge: Change the two numbers so it prints 9, and R3 is still 1.\n@ Expect: output \"9\\n\"\n@ Expect: R3 = 1[/code]\n" + \
		_grid(["Line", "What it checks"], [
			["[code]@ Challenge: …[/code]", "The task the strip shows. Optional; without it, the strip lists the checks."],
			["[code]@ Expect: output \"…\"[/code]", "Everything the program printed, from start to finish, must be exactly this text. Write it in quotes; [code]\\n[/code] is a new line (what [code]movs r0, #10[/code] + [code]bl putchar[/code] prints)."],
			["[code]@ Expect: R2 = 7[/code]", "The register's value when the program finishes. R0–R12, SP and LR; the value may be decimal, hex ([code]0x64[/code]), negative ([code]-1[/code]) or a character ([code]'H'[/code])."],
		], 1) + \
		"Use as many Expect lines as you like; all must pass. Checks run only in the simulator, and not when the program stops with a CPU fault. A line picosim cannot read is reported in the console when the file loads, and the rest of the program still runs.\n"
	_topic("challenges", "Challenges", chal)

	_section("Assembly basics")

	_topic("numbers", "Hex, binary and ASCII", _numbers())

	_topic("instructions", "Instructions", _instructions())

	_topic("branches", "Flags and branches", _branches())

	_topic("examples", "Level 1 examples", _examples())

	_section("Memory and I/O", 2)

	_topic("memmap", "Memory and addresses", _memmap())

	_topic("loadstore", "Loads and stores", _loadstore())

	_topic("stack", "The stack and functions", _stack())

	_topic("gpio", "GPIO: controlling pins", _gpio())

	_topic("examples2", "Level 2 examples", _examples2())

	_section("The main window")

	var ex := _h("Explain this step") + \
		_sub("Where it is") + \
		"At the top of the right-hand column" + (", above the CPU diagram when it is shown" if Level.at_least(3) else ", above the serial console") + ". Click its title ([b]▾ Explain this step[/b]) to fold it away, and again to bring it back. Drag the bar under it to give it more room: its text grows with it.\n" + \
		_sub("What just happened") + \
		"After each " + _k("Step instruction") + (", " + _k("Step over") if Level.at_least(2) else "") + (", " + _k("Step cycle") if Level.at_least(3) else "") + " or " + _k("◀ Step back") + ", this part names the instruction (with its line number in your file) and says in plain words what it did, with the real values: [i]Worked out R2 = R0 + R1 = 3 + 4 = 7[/i], or [i]Checked the condition “not equal” (Z = 0). The flags say Z = 0, so it jumped to loop[/i]. After " + _k("Run") + " stops, it says how many instructions ran and why Run stopped (the end of the program, a breakpoint, a key to type, or Pause).\n" + \
		_sub("What happens next") + \
		"The instruction ▶ points at, and what it [i]will[/i] do with the values the registers hold now. picosim finds this out exactly, by running that instruction on a copy of the machine and throwing the copy away, so your program is not touched. Two lines follow:\n" + \
		"• [b]Then[/b] — where the program goes after it: the next line, back up to the top of a loop, into a function, or back to the code that called the function.\n" + \
		"• [b]Why[/b] — what the instruction is for: saving registers at the start of a function, setting the flags for the branch on the next line, deciding whether to go round a loop again… When the line has a comment in your file, the comment is quoted too, so good comments make better explanations.\n" + \
		_sub("More detail") + \
		"Tick [b]More detail[/b] in the panel's corner for the full picture. It is remembered.\n" + \
		_grid(["Detail", "What it says"], [
			["How this instruction works", "One or two sentences about the instruction itself: what [code]movs[/code] does, what the S means, when a [code]bne[/code] jumps."],
			["What changed", "Every register and flag that changed, old → new, with what each flag means" + (", plus memory bytes and pins" if Level.at_least(2) else "") + ", and anything printed."],
			["PC", "Where the PC moved: on by 2 (or 4) to the next instruction, or a jump."],
		] + ([["In memory", "The instruction's bits, as the Raw column of the Disassembly shows them" + (" (the Memory tab shows the same bytes, coloured as code)" if Level.at_least(3) else "") + "."]] if Level.at_least(2) else []) + \
		([["Clock cycles", "How many cycles it takes on the teaching datapath, and the FSM states in order. With " + _k("Step cycle") + ", each cycle is explained on its own: what the datapath does, which tri-state buffer drives the bus, the control signals and what changes at the clock edge."]] if Level.at_least(3) else []) + \
		([["RP2040 pipeline", "Its cycles on the Pico's own 2-stage pipeline, from datasheet Table 81."]] if Level.at_least(4) else []), 1) + \
		_note("Try this", "Before you press Step, read [b]What happens next[/b] and work out the new values yourself. Then step and check them against [b]What just happened[/b] and the yellow registers.") + \
		_sub("While Run is on") + \
		"Explanations pause: the panel says the program is running and is not updated, so Run is not slowed down. When Run stops, the panel explains where and why, and what comes next.\n"
	_topic("explain", "Explain this step", ex)

	var regs := _h("Registers panel") + \
		"Top left. The processor's 16 registers, each holding one 32-bit number. Values changed by the last step are [color=" + _c("changed") + "]yellow[/color].\n" + \
		_grid(["Register", "Its job"], [
			["[b]R0–R7[/b]", "Your values. Almost every instruction can use these, so use them first. R0 also carries the character for [code]putchar[/code] and [code]getchar[/code]."],
			["[b]R8–R12[/b]", "More storage, but most Cortex-M0+ instructions cannot use them (see " + _link("instructions", "Instructions") + ")."],
			["[b]SP[/b] (R13)", "Stack pointer: where the stack is. [code]push[/code] moves it down and [code]pop[/code] moves it back up."],
			["[b]LR[/b] (R14)", "Link register: where to come back to. [code]bl[/code] puts the return address here before it jumps."],
			["[b]PC[/b] (R15)", "Program counter: the address of the next instruction. It goes up by 2 (or 4) after each instruction, and jumps on a branch."],
		], 1) + \
		("When a program is loaded, the built-in OS has already called your main, so PC is [code]0x00003000[/code] (main), LR is [code]0x00000007[/code] (where main returns to, in the OS), R0 is [code]0x00003001[/code] (left over from the OS) and SP is [code]0x00010000[/code] (the top of RAM). The others start at 0.\n" if not Level.at_least(2) else \
		"When a program is loaded, PC is [code]0x00000000[/code] (the start of the built-in OS), SP is [code]0x00010000[/code] (the top of RAM) and LR is [code]0xFFFFFFFF[/code] (nothing to return to). The others start at 0.\n") + \
		_sub("Hex, decimal or signed") + \
		"The menu at the panel's top right shows every register as " + _k("Hex") + " ([code]0x0000002A[/code]), " + _k("Decimal") + " ([code]42[/code]) or " + _k("Signed") + " (decimal, where values from [code]0x80000000[/code] up are negative, so [code]0xFFFFFFFF[/code] is −1). Hover a register to see all of these at once, plus its 32 bits in binary. See " + _link("numbers", "Hex, binary and ASCII") + ".\n" + \
		_sub("Flags") + \
		"The four check boxes N, Z, C and V are the flags: a tick means 1. They change only after an instruction that sets them, such as [code]cmp[/code], [code]adds[/code] or [code]movs[/code]. Hover one for what it means, or read " + _link("branches", "Flags and branches") + ".\n" + \
		_sub("What changed") + \
		"Under the flags, a yellow line names each register the last step changed, its new value as a number (and as a character when it is one), and the line of your file that changed it, for example [code]R0 = 72 = 0x48 = 'H'   ← movs r0, #'H'   (line 22)[/code]. Hovering a yellow register says the same. The PC is left out, because every step changes it. After " + _k("Run") + ", " + _k("◀ Step back") + " or a value you typed, the line says so instead of naming one instruction.\n" + \
		_sub("Editing") + \
		"Click a register, type a new value — hex like [code]0x1F[/code] or decimal like [code]31[/code] — and press " + _k("Enter") + ". Click a flag to toggle it. This is a quick way to try \"what if R1 were 9?\" without editing the file; " + _k("Reload") + " puts everything back.\n"
	if Level.at_least(3):
		regs += _sub("Datapath registers") + \
			"The line under that shows the datapath's internal registers: MAR (memory address), MDR (memory data), IR and IR2 (the instruction being executed). They are explained in " + _link("components", "Datapath components") + ".\n" + \
			_sub("Status line") + \
			"[b]Next state[/b] is the FSM state the next clock cycle will run (see " + _link("states", "FSM states") + "), and whether the CPU is between instructions or partway through one. Below it are the total instructions and clock cycles executed.\n"
	else:
		regs += _sub("Status line") + \
			"The bottom line counts the instructions run since the program was loaded. " + _k("Reload") + " sets it back to 0.\n"
	regs += _sub("Bigger text") + \
		"Drag the bars below and beside the panel to make it larger — the text and fields scale up to fill the space."
	_topic("registers", "Registers", regs)

	var mem := _h("Disassembly" + (" and Memory" if Level.at_least(2) else "")) + \
		(("[b]At Levels 3 and 4, while the CPU diagram is shown,[/b] this panel shows only the Memory tab. Your program is listed by the " + _link("diagram", "Walkthrough") + " beside the diagram instead, in the order it runs, with ▶ on where you are, the ● breakpoint column and " + _k("Skip to main ▸") + ". " + _k("Hide CPU Diagram") + " brings the Disassembly tab back.\n") if Level.at_least(3) else "") + \
		(_sub("Disassembly tab") if Level.at_least(2) else "") + \
		"Below the Registers. Your program as the CPU sees it, one instruction per row. The row with ▶ is the instruction that runs next. With " + _k("Follow PC") + " ticked the list scrolls to it automatically.\n" + \
		_grid(["Column", "Example", "Meaning"], [
			["●", "", "Breakpoint column: click it to set or clear a breakpoint (below)."],
			["Address", "[code]▶3002[/code]", "Where in memory the instruction is, in hex. Each instruction is 2 bytes, so addresses go up by 2. [code]bl[/code] is 4 bytes."],
			["Raw", "[code]2048[/code]", "The instruction as the CPU stores it: 16 bits in hex ([code]bl[/code] has two groups). The assembler made this from your line."],
			["Label", "[code]main[/code]", "The label at this address, if there is one."],
			["Instruction", _asm("movs r0, #0x48"), "The instruction turned back into assembly from the raw bits, the way the CPU sees it."],
			["Source", "[code]22  movs r0, #'H'  @ R0 = …[/code]", "The line of your file it came from: its line number, your code and your comment. Hover it to see the whole line."],
		], 2) + \
		"Compare the [b]Instruction[/b] and [b]Source[/b] columns to see what the assembler did with your line:\n" + \
		"• Constants are shown in hex: [code]movs r0, #'H'[/code] becomes [code]movs r0, #0x48[/code], and [code]#10[/code] becomes [code]#0xa[/code].\n" + \
		"• Some lines are stored in a shorter form: [code]adds r4, r4, #1[/code] becomes [code]adds r4, #1[/code], which does the same thing. A few instructions have a second name: [code]bcs[/code] shows as [code]bhs[/code].\n" + \
		"• Branches and calls show the label they go to ([code]bl putchar[/code], [code]bne loop[/code]), and the Label column shows where that label is.\n" + \
		"• The first rows, from [code]0000[/code] to [code]0016[/code], are the built-in OS ([code]_start[/code], [code]putchar[/code], [code]getchar[/code]); their Source is the OS's file, [code]os.s[/code]. Your program starts at [code]3000[/code], the [code]main[/code] row." + (" At Level 1 ▶ never stops in them." if not Level.at_least(2) else "") + "\n" + \
		_sub("Breakpoints") + \
		"Click the narrow first column of a row to put a red ● there; click again to remove it. " + _k("Run") + " stops when it reaches a breakpoint, [i]before[/i] running that instruction. Use one to run straight to the part you want to study, then step from there.\n"
	if Level.at_least(2):
		mem += _sub("Memory tab") + \
			"Memory as a place you can look around in. It has five parts, and everything in it updates after each step.\n" + \
			_grid(["Part", "What it shows"], [
				["[b]The map[/b] (left)", "The whole of memory from low addresses (top) to high (bottom): the built-in OS, [b]your code[/b], [b]your data[/b], [b]free RAM[/b] and the [b]stack[/b], which grows down from the top of RAM, then the [b]I/O registers[/b], which are not RAM at all. [b]PC[/b] and [b]SP[/b] tags show where they point, an [b]R[/b] or [b]W[/b] tag marks the part the last step read or wrote, and the part you are looking at has a blue outline. Click a part to go there."],
				["[b]The bytes[/b] (middle)", "256 bytes, 16 to a row; each row starts with the address of its first byte and the columns are + 0 … + F. Every byte is coloured by what it is: an instruction, a literal or string stored with the code, data, or the stack. [color=" + _c("label") + "]Labels[/color] sit above the bytes they name, and a register that holds an address is tagged where it points ([color=" + _c("accent") + "]R1 ↓[/color]): that register is a [b]pointer[/b]. The ▶ instruction is shaded, and the column on the right shows the same bytes as characters."],
				["[b]Last step[/b] (above the bytes)", "What the last instruction did to memory, in words: [i]ldr r0, [lb]r1] read a word at 3024 (numbers): the bytes 0A 00 00 00, which backwards (lowest byte first) make 0x0000000A = 10, now in R0[/i]. The bytes it read are outlined in [color=" + _c("accent") + "]blue[/color], the ones it wrote in [color=" + _c("changed") + "]yellow[/color]. With [b]Follow loads and stores[/b] ticked, the view moves to them by itself."],
				["[b]The stack[/b] (right)", "Each word from SP up to the top of RAM as a box, the newest at the bottom with the [b]◀ SP[/b] tag. Each box says what put it there, such as [i]LR saved by push {r7, lr} (line 20)[/i], or that it looks like a return address, and boxes are grouped by the function that pushed them. A word the last step popped is shown faded just below SP, with the register it went into."],
				["[b]Inspector[/b] (bottom)", "Hover a byte to see everything about it: its address and label, what kind of byte it is, its value in hex, decimal and as a character, the word it belongs to (put together lowest byte first), and which registers point to it."],
			], 1) + \
			"• [b]Show[/b] — " + _k("Bytes") + " (every byte in hex), " + _k("Words") + " (4 bytes put together into one number, so [code]64 00 00 00[/code] shows as [code]00000064[/code]) or " + _k("Characters") + " (each byte as the character it codes, [code]\\0[/code] for the 0 that ends a string).\n" + \
			"• [b]Go to[/b] — type an address ([code]0x3000[/code]) or any label of your program, data labels included ([code]numbers[/code]), and press " + _k("Enter") + ". " + _k("PC") + ", " + _k("SP") + " and " + _k("main") + " jump straight there. The mouse wheel scrolls the bytes.\n" + \
			"• [b]Changing memory[/b] — click a byte to select it (an orange outline), type new bytes in hex on the right ([code]2a 00 00 00[/code]) and press " + _k("Enter") + ": a quick way to try your program on different data without editing the file. " + _k("◀ Step back") + " and " + _k("Reload") + " undo it.\n" + \
			_note("Try this", "Open [b]Add up a list in memory[/b], tick Follow loads and stores and step through the loop. Each [code]ldr r0, [lb]r1][/code] outlines the next number, and the [b]R1 ↓[/b] tag moves 4 bytes along each pass: R1 is a pointer walking through the list.")
	_topic("memory", "Disassembly" + (" & memory" if Level.at_least(2) else ""), mem)

	_topic("pico", "Raspberry Pi Pico board", _h("Raspberry Pi Pico board") +
		"Bottom left, under Disassembly. A to-scale drawing of the Raspberry Pi Pico, lying on its side with the USB connector on the left, showing what your program does to the chip's GPIO pins. " + _link("gpio", "GPIO: controlling pins") + " explains how a program controls them.\n" +
		_sub("Reading the pins") +
		"The 40 gold pads are the header pins, numbered 1–20 along the bottom edge and 21–40 along the top. Each GPIO pad is labelled with its GP number; a small [b]out[/b] or [color=" + _c("accent") + "]in[/color] under the label says which way it is set (a floating input shows neither). Power pins (GND, 3V3, VSYS, VBUS, …) are labelled in grey and red. On the pad itself:\n" +
		_grid(["On the pad", "Means"], [
			["[color=" + _c("green") + "][b]1[/b][/color] on green", "An output your program drives high."],
			["[b]0[/b] on dark grey", "Low: an output driven low, or an input held low (by a click or a pull-down)."],
			["[color=" + _c("accent") + "][b]1[/b][/color] on blue", "An input reading high (clicked high, or held by a pull-up)."],
			["nothing", "Floating: nothing drives the pin and it has no pull resistor. Your program reads it as 0."],
			["[color=" + _c("changed") + "]yellow ring[/color]", "Changed by the last step."],
		], 1) +
		"The same key is drawn above the board. Hover any pin for its header number, GP number, direction, level and pull (for example [i]Pin 20: GP15 — output, high (1)[/i]).\n" +
		_sub("Under the board") +
		"• [b]Last I/O write[/b] — the last I/O register the current instruction wrote, decoded: [code]SIO GPIO_OUT_XOR ← 0x00008000[/code] or [code]IO_BANK0 GPIO15_CTRL ← 0x00000005[/code].\n" +
		"• [b]Changed by the last step[/b] — which pins changed, for example [code]GP15 → 1[/code].\n" +
		_sub("The on-board LED") +
		"The drawing's LED lights when GP25 is high, as on a Raspberry Pi Pico. Real boards differ (on a Pico W that LED is wired to the Wi-Fi chip, not to GP25), so for programs that should work on any board, wire your own LED to a GPIO pin: see the [b]Blink an LED[/b] example.\n" +
		_sub("Driving inputs") +
		"Click an [i]input[/i] pin to drive it like a switch: floating (Z) → 1 → 0 → floating. Your program sees it when it reads [code]GPIO_IN[/code]. A pin with a pull-up already reads 1, so for a button to GND click it [i]twice[/i] (1, then 0) to \"press\" it. Output pins cannot be clicked: your program controls them.\n" +
		_sub("Live from your board") +
		"In " + _k("Pico (USB serial)") + " mode, while a board you flashed from picosim is connected, the drawing shows your [i]real[/i] board's pins instead: the subtitle turns green and says LIVE. The pins update as your program runs (about 50 times a second), and pins that just changed get a yellow ring. You cannot click pins in this mode; use your real buttons. An input with nothing connected reads 0.\n" +
		_sub("Full window") +
		"Click " + _k("⛶ Enlarge") + " in the board's corner to fill the window with it; " + _k("✕ Close") + " or " + _k("Esc") + " returns.", 2)

	_topic("serial", "Serial console", _h("USB Serial console") +
		"Bottom right. Shows what your program prints with [code]putchar[/code].\n" +
		_sub("Typing input") +
		"• Click in the output area and type: each key is sent immediately (what [code]getchar[/code] expects).\n" +
		"• Or type a whole line in the box below and press " + _k("Enter") + " — it is sent followed by a carriage return.\n" +
		"When the program is waiting in [code]getchar[/code], the console header says so. " + _k("Clear") + " empties the output.")

	var layout := _h("Layout and zoom") + \
		"• [b]Resize panels[/b] by dragging the bars between them. The registers panel's text grows with it.\n" + \
		"• [b]Zoom[/b] the whole window with " + _k("−") + " / " + _k("+") + " in the toolbar, " + _k("Ctrl/Cmd +") + " / " + _k("Ctrl/Cmd −") + ", or " + _k("Ctrl/Cmd") + " + mouse wheel. Click the percentage (or " + _k("Ctrl/Cmd 0") + ") to reset. The zoom is remembered.\n" + \
		"• [b]Colours:[/b] " + _k("View") + " in the toolbar picks System, Blue, Dark or Light. It is remembered too.\n" + \
		"• Zoom stops at the largest size where the whole toolbar still fits, so no button is ever cut off.\n"
	if Level.at_least(3):
		layout += "• " + _k("Show / Hide CPU Diagram") + " toggles the diagram and the panel under it." + \
			(" The machine menu next to it switches the diagram between the " + _link("diagram", "multi-cycle datapath") + " and the " + _link("pipeline", "2-stage pipeline") + "." if Level.at_least(4) else "") + "\n"
	if Level.at_least(2):
		layout += "• The Pico board can fill the window — see " + _link("pico", "Raspberry Pi Pico board") + ".\n"
	layout += "\nWhere each panel is, and what every toolbar button does, is in " + _link("tour", "The screen at a glance") + ". Which panels and buttons you see depends on the " + _k("Level") + " menu."
	_topic("layout", "Layout and zoom", layout)

	_section("Hardware", 2)

	_topic("hardware", "Running on a real Pico", _h("Running on a real Pico") +
		"The same [code].s[/code] file that runs in the simulator can run on a real Raspberry Pi Pico (or any RP2040 board) plugged into your computer by USB. picosim turns it into a [code].uf2[/code] file, the format the Pico loads programs from, and copies it to the board. This is called [b]flashing[/b], because the program is stored in the board's flash memory and stays there after you unplug it.\n" +
		_sub("What you need") +
		"• The board and a USB cable that carries data (some cables only charge).\n" +
		"• The tools from [code]installation.md[/code]: the ARM compiler, git and, ideally, [code]picotool[/code]. Your teacher has probably installed them already.\n" +
		_sub("Steps") +
		"1. Load your program in picosim and check it in the Simulator first.\n" +
		"2. Choose [b]Pico (USB serial)[/b] in the toolbar's mode menu (it says [b]Simulator[/b] until you change it). The port menu, " + _k("⟳") + ", " + _k("Connect") + " and " + _k("Flash") + " take the place of the stepping buttons (the board runs the program, so Step and Run are put away until you switch back to Simulator).\n" +
		"3. Click " + _k("Flash") + ". picosim builds the program with the Pico SDK and uploads it; the console shows each step. The very first build downloads the SDK and takes a few minutes; later ones take seconds. It uploads with [code]picotool[/code] if that is installed; otherwise it copies the file to the board's [code]RPI-RP2[/code] drive, which appears when you [b]hold the BOOTSEL button while plugging the board in[/b].\n" +
		"4. When the upload finishes, picosim connects to the board by itself and the console talks to it: what the board prints appears in green, and keys you type go to the board. The board waits up to 5 seconds for this connection before your program starts, so nothing it prints is lost. If it does not connect, pick the port marked [i](Pico)[/i] (" + _k("⟳") + " refreshes the list) and click " + _k("Connect") + ".\n" +
		"5. The " + _link("pico", "Pico board") + " drawing turns LIVE and shows your real board's pins.\n" +
		_sub("What is different on the board") +
		"• The build defines [code]PICOSIM_HARDWARE[/code], so a program can use bigger delays on the board ([code].ifdef PICOSIM_HARDWARE[/code], as the LED examples do): the real chip runs millions of instructions a second.\n" +
		"• Your " + _k("⚙ Settings") + " values (pins and wiring) are built in.\n" +
		"• Your [code]main:[/code] is renamed [code]asm_main[/code] and called by the Pico SDK after it has started USB. When [code]main[/code] returns, the board does not halt: it waits, with USB still running, so its last output stays in the console and the next Flash works.\n" +
		"• Stepping, the registers, the Memory tab and the CPU diagram work only in [b]Simulator[/b] mode. Switch back to it to debug.\n" +
		_sub("If Flash fails") +
		"The status text says [i]Flash failed — see console[/i]; the console shows why:\n" +
		_grid(["The console says", "What to do"], [
			["[code]picotool could not find or access the Pico[/code] or [code]Put the Pico in BOOTSEL mode[/code]", "Unplug the board, hold BOOTSEL, plug it back in, let go, and click Flash again."],
			["[code]'picotool' was not found and no RPI-RP2 volume is mounted[/code]", "No picotool: put the board in BOOTSEL mode as above, so its drive appears."],
			["[code]git is not on PATH[/code], [code]ARM GNU C/C++ compiler was not found[/code] or [code]has no C library (newlib)[/code]", "A tool is missing: see [code]installation.md[/code] or ask your teacher."],
			["an assembler error", "Your program does not assemble: fix it in the Simulator first."],
		], 1), 2)

	_section("Inside the CPU", 3)

	_topic("cpuintro", "The big picture", _cpu_intro())

	_topic("blocks", "Building blocks", _blocks())

	_topic("diagram", "Datapath diagram", _h("Datapath diagram") +
		"New to this? Read " + _link("cpuintro", "The big picture") + " and " + _link("blocks", "Building blocks") + " first.\n\n" +
		"Click " + _k("Show CPU Diagram") + " in the toolbar, with the machine menu next to it on " + _k("Multi-cycle") + ". The diagram is the multi-cycle datapath from the course text: one shared 32-bit bus, MAR/MDR, IR/IR2, a separate address adder, the ALU, the register file and the FSM that controls them.\n" +
		_sub("Values during a cycle, and the clock edge") +
		"The diagram shows the cycle [i]while it happens[/i]. Every register (PC, IR, MAR, MDR, the register file, the flags) keeps showing the value it holds during the cycle. A register that loads a new value at the clock edge which ends the cycle turns [color=" + _c("changed") + "]yellow[/color] and gets a caption [code]old → new[/code] underneath; the box itself only shows the new value once you step to the next cycle. For example in FETCH_ADDR the PC still reads [code]0x00003000[/code] with [code]0x00003000 → 0x00003002[/code] below it, and in FETCH_MEMORY it reads [code]0x00003002[/code]. (The Registers panel on the left always shows the machine after the last clock edge.)\n" +
		_sub("What the colors mean") +
		"• [color=" + _c("accent") + "]Bright blue wires and boxes[/color] carry data in the cycle being shown.\n" +
		"• [color=" + _c("purple") + "]Purple dashed arrows[/color] are control inputs from the FSM: the short stubs into the muxes (select), the ALU (ALUK), the +2 incrementer (PCINC) and each tri-state buffer (GatePC, GateADDR, …), plus BranchTaken. A bright stub is asserted this cycle and is labelled with the value it selects.\n" +
		"• [color=" + _c("changed") + "]Yellow[/color] marks a value loaded at the clock edge that ends this cycle (old → new).\n" +
		"• The triangles on the bus are tri-state buffers, each named after the control signal that turns it on: GatePC, GateADDR, GateMDR, GateALU and GateVEC. The lit one is driving the bus; the others are off (high impedance). The bus value is printed above the bus.\n" +
		"• Every mux, the ALU and the adders take their inputs on the long side and send their result out of the short side; control comes in on a slanted side.\n" +
		"• The [b]heat tint[/b] behind each box, from cool to hot, shows how much of the run so far that part has been used, and its small label gives its share of all clock cycles. It is on from Level 3; " + _k("View") + " ▸ [b]Heat overlay on the CPU diagram[/b] turns it off and on." + (" The " + _link("heat", "Heat map") + " has the same numbers in full." if Level.at_least(4) else "") + "\n" +
		"When you step, a glowing pulse travels along the active wires in the order the data flows; a register's yellow caption appears when the pulse reaches it. Its speed follows the speed slider; at high speeds animation is skipped.\n" +
		_sub("Learning the components") +
		"Hover a block for a one-line summary. [b]Click a block[/b], a tri-state buffer (▲ / ▼) or the bus to open a card explaining what it does, how it works, what it connects to and when it is used; the block and its wires are outlined in [color=" + _c("sel") + "]orange[/color]. Click it again, click empty space, press ✕ or " + _k("Esc") + " to close. The same descriptions are in " + _link("components", "Datapath components") + ".\n" +
		_walk_help(false))

	_topic("cycles", "Cycle by cycle", _h("Cycle-by-cycle inspector") +
		"Shown below the CPU diagram. It follows the instruction being executed one clock cycle at a time. " + _link("tutorial", "Tutorial: one ADDS") + " walks through it with an example.\n" +
		_sub("Left side") +
		"• The instruction's address and text, and how far through it the CPU is.\n" +
		"• The phase chips — " + _k("F") + " Fetch, " + _k("D") + " Decode, " + _k("EA") + " Evaluate address, " + _k("FO") + " Fetch operands, " + _k("EX") + " Execute, " + _k("SR") + " Store result — light up for the selected cycle. Hover one for its name.\n" +
		"• The list of this instruction's cycles: number, FSM state and phase. Click a row to look at an earlier cycle again; the diagram shows that cycle.\n" +
		"• " + _k("◀ Prev") + " and " + _k("Next ▶") + " move through the cycles. At the latest cycle, Next clocks the CPU (the same as Step cycle).\n" +
		_sub("Right side") +
		"• The state name and a plain-language description of what the cycle does, including the value on the bus.\n" +
		"• [b]Control signals asserted[/b] — what the FSM turns on. [code]GateX[/code] puts X on the bus, [code]LD.X[/code] makes X load at the clock edge, [code]XMUX=…[/code] picks a multiplexer input, [code]ALUK=…[/code] picks the ALU operation.\n" +
		"• [b]What changes at the clock edge[/b] — every register, flag, memory byte or I/O register the cycle loads at the edge that ends it, old → new. On the diagram these are the yellow captions.\n" +
		_sub("How many cycles?") +
		"Every instruction starts with the same 3 fetch cycles (FETCH_ADDR → FETCH_MEMORY → FETCH_IR). Typical totals: " + _typical_cycles() + ". " + _link("states", "FSM states") + " explains every state and lists each instruction's sequence; the full table is also in [code]docs/cycles.md[/code].")

	_topic("tutorial", "Tutorial: one ADDS", _tutorial())

	_topic("kinds", "Other instructions", _kinds())

	_topic("signals", "Control signals", _signals())

	_add_states()

	_add_components()

	_section("Pipeline (RP2040)", 4)

	_topic("pipeintro", "Pipelining: the big idea", _pipe_intro())

	_topic("pipeline", "Pipeline diagram", _h("Pipeline diagram") +
		"New to pipelines? Read " + _link("pipeintro", "Pipelining: the big idea") + " first.\n\n" +
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
		"• [b]waiting in IF/EX: Execute is busy[/b] — it has been fetched, but the instruction ahead of it still needs Execute.\n" +
		"• [b]cycle k of n[/b] — it is executing; n is its cycle count from RP2040 datasheet Table 81.\n" +
		"• [b]· refilling from the target[/b] — added to a taken branch's last cycle, while its target is fetched.\n" +
		"• [b]fetched (sequential: predicted not-taken)[/b] — the instruction after a branch, fetched before anyone knows whether the branch is taken.\n" +
		"• [color=" + _c("red") + "][b]✕ wrong path: flushed[/b][/color] — that instruction, once the branch turns out to be taken: it is discarded and never executes.\n" +
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
		"Hover a block for a one-line summary, or click it for a card explaining what it does, how it works and what it connects to. The same descriptions are in " + _link("pipecomponents", "Pipeline components") + ". " + _link("pipecycles", "Pipeline cycle by cycle") + " explains stepping, the instruction strip and where the pipeline loses cycles.\n" +
		_walk_help(true))

	_topic("pipecycles", "Pipeline cycle by cycle", _h("Pipeline cycle by cycle") +
		_sub("Stepping") +
		"• " + _k("Step cycle") + " (" + _k("F11") + ") — one pipeline clock cycle: every token moves on one stage.\n" +
		"• " + _k("Step instruction") + " (" + _k("F10") + ") — jump to the next instruction's first Execute cycle.\n" +
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

	_section("Performance", 4)

	_topic("perfmath", "Performance formulas", _perf_math())

	_topic("performance", "Performance window", _h("Performance window") +
		"Click " + _k("Performance") + " in the toolbar (" + _k("F2") + "). The window counts every instruction your program has completed since it was loaded and updates while it runs. The same run is timed on three machines; they execute the same instructions with the same results, so only the number of clock cycles differs.\n" +
		"• [b]Multi-cycle[/b] — the teaching datapath on the CPU diagram: one instruction at a time, 4 or more cycles each (most take 5–7).\n" +
		"• [b]2-stage pipeline (RP2040)[/b] — the Raspberry Pi Pico's Cortex-M0+: the next instruction is fetched while the current one decodes and executes. To watch it run, see " + _link("pipeline", "Pipeline diagram") + ".\n" +
		"• [b]3-stage pipeline[/b] — a teaching design with separate fetch, decode and execute stages.\n" +
		_sub("How to use it") +
		"1. Load a program and run it (to the end, or as far as you like). The window measures only what has run so far.\n" +
		"2. Press " + _k("F2") + ". Read the [b]Metrics[/b] tab first, then try options on [b]Compare[/b], then look at the [b]Pipeline[/b] tab.\n" +
		"3. Keep it open while you step: it updates as the program runs.\n" +
		"If the formulas are new to you, read " + _link("perfmath", "Performance formulas") + " first.\n" +
		_sub("Metrics tab") +
		"Cycles, CPI (cycles per instruction) and time at the RP2040's default 125 MHz for each machine; for the multi-cycle machine, the cycles spent in each phase and how often the bus, the ALU and memory are busy; the instruction mix; where data accesses went (RAM, the SIO, APB peripherals); branch statistics; and the instructions that cost the most cycles.\n" +
		_sub("Heat map tab") +
		"How much each part of the CPU, each register and each flag was used: totals, a timeline down to single clock cycles, per program line, and before/after comparisons. See " + _link("heat", "Heat map") + ".\n" +
		_sub("Compare tab") +
		"Change the design and see the new cycle counts next to the stock ones: the [b]multiplier[/b] (the Cortex-M0+ can be built with a fast 1-cycle or a small 32-cycle multiplier; the RP2040 has the fast one) and [b]memory wait states[/b] for the multi-cycle machine (every memory access takes longer).\n" +
		_sub("Pipeline tab") +
		"Pick the 2-stage or 3-stage design and how many recent instructions to show. The chart lays out the last instructions on that pipeline, one row per instruction and one column per clock cycle: " + _k("F") + " fetch, " + _k("D") + " decode, " + _k("E") + " execute, " + _k("R") + " a branch's refill cycle, " + _k("✕") + " an instruction fetched from the wrong path and discarded after a taken branch. Click a column (or use " + _k("◀ Cycle") + " / " + _k("Cycle ▶") + ") to see what each stage holds in that cycle and why it takes the cycles it does.\n" +
		_sub("Where the numbers come from") +
		"• RP2040 cycle counts are the [b]RP2040 datasheet, Table 81[/b] (Cortex-M0+ instruction summary), which assumes zero wait states — code and data in SRAM, or the flash cache hitting. For example ADDS = 1, LDR/STR = 2 (or 1 to the SIO's single-cycle I/O port), B<cond> = 1 not taken / 2 taken, B = 2, BL = 3, PUSH = 1 + N, POP {…, PC} = 3 + N.\n" +
		"• The 3-stage design uses the same execute cycles, but a taken branch costs one cycle more than on the 2-stage (2 refill cycles instead of 1), because it has one more stage to refill.\n" +
		"• Not timed, and listed separately instead of guessed: SVC and BKPT (the datasheet gives no fixed count), and the extra bus cycles of APB peripheral accesses (datasheet §2.1.3) — the window tells you how many there were.\n" +
		"• The exact cycle in which the real Cortex-M0+ fetches is not published (it fetches up to 32 bits ahead), so the chart's F marks when an instruction is in this model's fetch stage; the execute-stage counts are the datasheet's.\n")

	var heatp := _h("Heat map") + \
		"A [b]heat map[/b] is a grid of coloured cells where the colour shows how much: here, how much each part of the CPU was used while your program ran. Rows are the parts (the datapath's registers, muxes, adder, ALU, memory and bus; then every register's reads and writes; then the flags). The stronger the colour, the more that part was used. It answers questions a cycle count alone cannot: [i]which part is the bottleneck? which register does my loop lean on? which line of my program keeps the memory busy?[/i]\n" + \
		"Open it with " + _k("Performance") + " (" + _k("F2") + ") ▸ [b]Heat map[/b]. Like the rest of the window it measures what has run so far, and updates while the program runs.\n" + \
		_sub("What \"used\" means") + \
		"On the [b]multi-cycle datapath[/b] a part is used in a clock cycle when the FSM asserts a control signal that makes it work — the same signals the " + _link("diagram", "CPU diagram") + " lights up — so these counts are [b]exact[/b]:\n" + \
		_grid(["Part", "Used in a cycle when"], [
			["MAR, MDR, IR, IR2, PC", "it is loaded ([code]LD.MAR[/code], [code]LD.MDR[/code], [code]LD.IR[/code], [code]LD.PC[/code]…) or drives what it holds (MAR addressing memory, MDR through GateMDR, PC through GatePC)"],
			["Bus and its buffers", "a tri-state buffer drives the bus ([code]GatePC[/code], [code]GateADDR[/code], [code]GateMDR[/code], [code]GateALU[/code], [code]GateVEC[/code]); the Bus row counts cycles with any driver"],
			["ALU, its A / B latches", "[code]ALUK[/code] selects an operation outside DECODE; the latches when [code]LD.ALUA[/code] / [code]LD.ALUB[/code] load them"],
			["Muxes, adder, incrementers", "their select signal is set ([code]ADDR1MUX[/code], [code]ADDR2MUX[/code], [code]SR2MUX[/code], [code]PCMUX[/code], [code]PCINC[/code], [code]MAR+4[/code])"],
			["Memory / I-O", "[code]MEM.EN[/code]. In Totals and Per line it is also split into instruction fetches and data accesses to RAM, the SIO (GPIO) and APB peripherals"],
			["FSM", "every cycle (it is the clock's own row, a reference for 100%)"],
			["Register reads / writes", "one use of a register-file read port ([code]SR1[/code], [code]SR2[/code]) or the write port ([code]DR[/code] with [code]LD.REG[/code], [code]LD.SP[/code], [code]LD.LR[/code]). [code]adds r0, r0, r0[/code] reads R0 twice. The PC is read by every fetch and PC-relative address, and written by every fetch and branch"],
			["Flag reads / writes", "an instruction ending in S (or CMP, CMN, TST) writes the flags it sets; a conditional branch reads the flags its condition tests (BNE reads Z; BGT reads Z, N and V)"],
		], 1) + \
		"The [b]2-stage pipeline[/b] (the RP2040's Cortex-M0+) has no control signals in picosim, so its rows are an [b]estimate[/b] built from the datasheet's cycle counts: the fetch blocks once per halfword fetched (and again after a branch), decode once per instruction, the ALU for ALU instructions and load/store addresses, data memory once per data access, branch resolve for every PC change, and [i]Refill[/i] for the cycle a taken branch wastes. Its registers and flags are counted the same way as on the multi-cycle machine. SVC and BKPT, which the datasheet does not time, count as one cycle.\n" + \
		_sub("Views") + \
		"• [b]Totals[/b] — one row per part: how many cycles it was used and what share of all cycles that is (its [i]utilisation[/i]). Start here.\n" + \
		"• [b]Timeline[/b] — one column per [i]bucket[/i] of clock cycles, from 1 cycle (every cycle of every instruction: you can see the fetch–decode–execute pattern) to thousands. Drag across the grid to zoom into those cycles; " + _k("Zoom out") + ", " + _k("Whole run") + " and " + _k("Fit to width") + " move back out. It includes the built-in OS.\n" + \
		"• [b]Per line[/b] — one column per line of your program (and of the OS), with the uses that line caused over the whole run: a loop body shows up as the hot columns. Hover a column for the line's text and how many times it ran.\n" + \
		"• [b]Compare[/b] — this run against a saved [b]baseline[/b]: blue = used less than before, red = used more, grey = no change. See [i]Before and after[/i] below.\n" + \
		"Beside the grid, [b]Register reads[/b] and [b]Register writes[/b] show R0–R15 as tiles, so you can see at a glance which registers your code relies on. Click a row's name to show that part on the CPU diagram.\n" + \
		_sub("Making small differences visible") + \
		"• [b]Scale[/b]: [i]Linear[/i] colours in proportion to the value; [i]Log[/i] spreads out small values, so a part used a few times still shows next to one used thousands of times; [i]Percentile[/i] colours by rank, so every shade holds the same number of cells and even tiny differences separate.\n" + \
		"• Untick [b]Auto range[/b] and set [b]Min[/b] and [b]Max[/b] to stretch the colours over just the range you care about (values outside are clamped).\n" + \
		"• [b]Threshold[/b] draws cells below that share of the colour range as unused, so only the busy parts stand out.\n" + \
		"• [b]Cell width[/b] and [b]Row height[/b] go down to 2 pixels, to fit thousands of cycles on screen (or " + _k("Ctrl/Cmd") + " + mouse wheel on the grid). [b]Value[/b] switches between counts and % of cycles; [b]Your code only[/b] leaves out the OS (Totals and Per line).\n" + \
		_sub("Before and after: finding what to optimise") + \
		"1. Run your program to the end and open [b]Totals[/b] or [b]Per line[/b]: the strongest cells are the parts and lines doing the most work.\n" + \
		"2. Press " + _k("Save baseline") + ". picosim keeps the counts in a file next to your program ([code]<name>.heat-baseline.json[/code]).\n" + \
		"3. Change the program (keep a register instead of reloading it from memory, unroll a loop, use a cheaper instruction…), save it — picosim reloads it — and run it again.\n" + \
		"4. Choose View ▸ [b]Compare[/b]. Each row shows the baseline, the new count, the change and the change in %. [i]Per 1000 cycles[/i] compares the share of time instead of raw counts, which is fairer when the two runs have different lengths.\n" + \
		_note("Tip", "On the multi-cycle machine the busiest parts are nearly always the bus (about half of all cycles in the examples) and MAR and MDR (about 40%), because every instruction is fetched through them and the bus is shared by everything. Fewer instructions and fewer memory accesses (loads, stores, PUSH and POP) lower them most; compare the Per line view before and after to see which lines changed.") + \
		_sub("Exporting for analysis") + \
		_k("Export CSV…") + " and " + _k("Export JSON…") + " save exactly what the grid shows. A CSV starts with [code]#[/code] lines describing the run (program, model, exact or estimate, cycles, instructions, date), then: Totals — one row per part with its count and % of cycles; Per line — one row per line with a column per part; Timeline — one row per bucket with its first and last cycle; Compare — before, after, change and change %. In Python, [code]pandas.read_csv(path, comment='#')[/code] reads it. " + _k("Save PNG…") + " saves a picture of the grid.\n" + \
		_sub("On the CPU diagram") + \
		"View ▸ [b]Heat overlay on the CPU diagram[/b] tints every box of the " + _link("diagram", "CPU diagram") + " (or the " + _link("pipeline", "pipeline diagram") + ") by how much of the run it was used, with a small label giving its share of all cycles; the register file tints each register by its reads + writes. It updates while the program runs, alongside the usual animation.\n" + \
		_sub("Limits") + \
		"• The timeline keeps every instruction for the first 20 million instructions (about 100 million cycles). After that its columns are samples every few thousand cycles; Totals and Per line stay exact for the whole run.\n" + \
		"• The multi-cycle counts are exact for picosim's teaching datapath, which is not how the real RP2040 is built; the pipeline counts are a model of the real chip, not a measurement of it.\n" + \
		"• Step back rewinds the counts too: they always describe the run up to the instruction shown.\n"
	_topic("heat", "Heat map", heatp)

	_section("Help")

	# only the keys that do something at this level
	var keys := _row("S  or  F10", "Step instruction: run one instruction (hold to repeat)")
	keys += _row("B", "Step back: undo the last step (hold to repeat)")
	if Level.at_least(2):
		keys += _row("O", "Step over: run a whole bl call, stop on the line after it")
	if Level.at_least(3):
		keys += _row("C  or  F11", "Step cycle: one clock cycle (hold to repeat)")
	keys += _row("Space  or  F5", "Run / Pause")
	if Level.at_least(4):
		keys += _row("F2", "open the Performance window")
	keys += _row("F1", "open this guide") + \
		_row("Ctrl/Cmd+O", "open a .s file") + _row("Ctrl/Cmd+R", "reload (start the program over)") + \
		_row("Ctrl/Cmd + / −", "zoom in / out") + _row("Ctrl/Cmd 0", "reset zoom") + \
		_row("Esc", "close this guide" + (", the enlarged Pico board or a component card" if Level.at_least(2) else ""))
	var note := "The letter keys work on every keyboard: on a Mac, F11 shows the desktop and the F keys need Fn, so use S, B, C and Space there. They only work in Simulator mode, and not while you are typing in a box or in the serial console (those keys go to your program)."
	if Level.at_least(4):
		note = "On the 2-stage pipeline, C / F11 steps one pipeline cycle, S / F10 jumps to the next instruction's first Execute cycle and B goes back a whole instruction. " + note
	_topic("keys", "Keyboard shortcuts", _h("Keyboard shortcuts") + "[table=2]" + keys + "[/table]\n\n" + note)

	var tr := _h("Troubleshooting") + \
		"[font_size=%d][b]While it runs[/b][/font_size]\n" % roundi(21 * text_scale) + \
		_sub("Nothing happens when I press Step or Run") + \
		"The program has halted (the status text says [i]halted[/i]) — press " + _k("Reload") + ". Or no program is loaded" + (", or you are in Pico mode — switch to Simulator" if Level.at_least(2) else "") + ".\n" + \
		(_sub("I pressed Step and ended up in code I did not write") + \
		"That is the built-in OS, at addresses below [code]3000[/code]. The first three instructions of every run are the OS starting your [code]main[/code], and each [code]bl putchar[/code] goes into the OS for two instructions ([code]svc #2[/code], [code]bx lr[/code]) before coming back. Keep stepping, or use " + _k("Step over") + " on the [code]bl[/code] line. See " + _link("running", "How a program runs") + ".\n" + \
		_sub("R0 suddenly holds 0x00002FFC or 0x00003001") + \
		"Those are from the OS's first two instructions, before your program starts. Your own code starts at [code]main[/code].\n" if Level.at_least(2) else \
		_sub("R0 already holds 0x00003001 and LR 0x00000007 before I press anything") + \
		"picosim's built-in OS has already run its start-up and called your [code]main[/code]: R0 is left over from that, and LR is where main returns to. Your program does not need them. See " + _link("running", "How a program runs") + ".\n") + \
		_sub("Run is very slow") + \
		"Move the " + _k("Speed") + " slider to the right. At the starting speed (8 cyc/s) Run does about one instruction a second, so you can watch it. [b]Max[/b] finishes almost at once.\n" + \
		_sub("The program stops and waits") + \
		"It is in [code]getchar[/code], waiting for a key (the status text says [i]waiting for input[/i]). Click the serial console and type a key.\n" + \
		_sub("It prints a strange character instead of my number") + \
		"[code]putchar[/code] prints the character whose [i]code[/i] is in R0. Add [code]'0'[/code] to a number from 0 to 9 first; numbers of 10 or more need one digit at a time. See " + _link("numbers", "Hex, binary and ASCII") + ".\n" + \
		_sub("A value I saved in R0–R3 changed after bl putchar") + \
		"Functions are allowed to change R0–R3 (and LR). Keep values you need in R4–R7, and add those registers to [code]main[/code]'s [code]push[/code] and [code]pop[/code].\n" + \
		(_sub("The program never halts, and ▶ is stuck on a bx lr") + \
		"A function called another function (even putchar) without saving LR first, so its [code]bx lr[/code] jumps back to itself forever. Add [code]push {lr}[/code] at its start and end it with [code]pop {pc}[/code]. See " + _link("stack", "The stack and functions") + ".\n" + \
		_sub("Go to in the Memory tab does nothing") + \
		"Type the label exactly as it is in your file (capital letters matter), or an address starting with [code]0x[/code], then press " + _k("Enter") + ". Reload first if you have just added the label.\n" + \
		_sub("My data is not in the Disassembly") + \
		"Data after [code].data[/code] is not listed there: look at it in the Memory tab (Go to its label).\n" if Level.at_least(2) else "") + \
		_sub("I can't find a button or panel from the guide") + \
		"Some appear only at higher levels. Raise the " + _k("Level") + " menu in the toolbar; see " + _link("start", "Start here") + ".\n"
	if Level.at_least(3):
		tr += _sub("The cycle inspector says no cycle has run yet") + \
			"Press " + _k("Step cycle") + " (" + _k("F11") + "). " + _k("Step instruction") + " and " + _k("Run") + " also run cycles, but they finish whole instructions; to see the cycles of one instruction, stop before it (a breakpoint) and use F11.\n" + \
			_sub("A value on the diagram is not the one in the Registers panel") + \
			"The diagram shows values [i]during[/i] the cycle; a register's new value appears as the yellow [code]old → new[/code] caption and moves into the box at the next cycle. The Registers panel shows the machine after the last clock edge. See " + _link("diagram", "Datapath diagram") + ".\n"
	if Level.at_least(4):
		tr += _sub("The diagram shows the wrong machine") + \
			"The machine menu next to " + _k("Show CPU Diagram") + " picks it: " + _k("Multi-cycle") + " for the datapath with the cycle inspector, " + _k("2-stage pipeline") + " for the Pico's pipeline.\n" + \
			_sub("The pipeline totals leave out some instructions") + \
			"SVC (putchar, getchar) and BKPT are not timed on the pipelines: the RP2040 datasheet gives no fixed count for them. The Performance window says how many were left out, and its CPI is over the rest.\n"
	tr += "\n[font_size=%d][b]When it will not load[/b][/font_size]\n" % roundi(21 * text_scale) + \
		"The [b]Could not load program[/b] box shows the assembler's message. The number after the file name is the line, for example [code]add.s:12: Error: …[/code] is line 12. Fix the file, save it and press " + _k("Reload") + ".\n" + \
		_grid(["The message says", "What is usually wrong"], [
			["[code]cannot honor width suffix[/code]", "A missing S ([code]add[/code] instead of [code]adds[/code], [code]mov r0, #1[/code] instead of [code]movs[/code]), or a register from R8 to R12. Use R0–R7. See " + _link("instructions", "Instructions") + "."],
			["[code]invalid immediate: 300 is out of range[/code]", "The constant is too big (or negative). [code]movs[/code], [code]cmp[/code] and two-register [code]adds[/code] / [code]subs[/code] take 0 to 255."],
			["[code]immediate value out of range[/code]", "Three-register [code]adds r0, r1, #9[/code] takes only 0 to 7. Use [code]adds r0, #9[/code] on the same register, or put the constant in a register first."],
			["[code]bad arguments to instruction[/code]", "A missing comma or # (write [code]movs r0, #1[/code]), or a misspelt register."],
			["[code]undefined reference to …[/code]", "A branch or [code]bl[/code] to a label that does not exist. Check the spelling, and that the label line ends in a colon."],
			["[code]The program has no main: label[/code]", "Put [code]main:[/code] on the line before your first instruction."],
		] + ([
			["[code]invalid offset, value too big[/code]", "A load or store offset that is too big, negative or not a multiple of the size; or, on an [code]ldr rX, =…[/code] line, a literal pool too far away (put [code].ltorg[/code] after a [code]b[/code] or [code]pop[/code]). See " + _link("loadstore", "Loads and stores") + "."],
		] if Level.at_least(2) else []), 1) + \
		_sub("\"Simulator backend disconnected\"") + \
		"The Python backend stopped. Close picosim and start it again with [code]picosim --gui[/code].\n"
	if Level.at_least(2):
		tr += "\n[font_size=%d][b]Pins[/b][/font_size]\n" % roundi(21 * text_scale) + \
			_sub("A pin will not respond to clicks") + \
			"Only input pins can be driven. If your program configured the pin as an output (via [code]GPIO_OE[/code]), it controls the pin.\n" + \
			_sub("My program sets GPIO_OE and GPIO_OUT but the pin stays blank") + \
			"The pin is not connected to the SIO yet: write 5 (function SIO) to its IO_BANK0 CTRL register at [code]0x40014004 + 8 × pin[/code] first. The real RP2040 needs this too. Hover the pin for a reminder. See " + _link("gpio", "GPIO: controlling pins") + ".\n" + \
			"\n[font_size=%d][b]On a real Pico[/b][/font_size]\n" % roundi(21 * text_scale) + \
			_sub("There is no Flash button") + \
			"Choose [b]Pico (USB serial)[/b] in the mode menu (it says [b]Simulator[/b]). Flash needs a loaded [code].s[/code] file.\n" + \
			_sub("Flash failed") + \
			"The console shows the reason; the table in " + _link("hardware", "Running on a real Pico") + " says what to do. Most often the board is not in BOOTSEL mode: unplug it, hold BOOTSEL, plug it in, let go, and Flash again.\n" + \
			_sub("Nothing appears in the console after Flash") + \
			"Check that a port marked [i](Pico)[/i] is chosen and that the button says [b]Disconnect[/b] (it is connected); if not, press " + _k("⟳") + " and " + _k("Connect") + ". A board waits only 5 seconds for the console before it starts, so a short program may have finished before you connected: Flash again.\n"
	tr += "\n[font_size=%d][b]\"CPU fault: …\" in the console[/b][/font_size]\n" % roundi(21 * text_scale) + \
		"Your program did something that would crash a real Pico, and the message says what and where. The most common ones:\n" + \
		"• [b]Undefined instruction[/b] — not a Cortex-M0+ instruction (for example code written for a Cortex-M4), or the program ran into data.\n" + \
		"• [b]Bus fault[/b] — the address is not RAM (0x0000–0xFFFF)" + (" or a simulated I/O register" if Level.at_least(2) else "") + ". The two most common causes at first: [code]main[/code] has no [code]pop {…, pc}[/code] at the end, so the CPU runs on through empty memory until it reaches [code]0x00010000[/code]; or [code]main[/code] pops without a matching [code]push {…, lr}[/code] at the start, so the pop reads past the top of RAM. Later on, a pointer that went wrong or a stack that grew too far (too many PUSHes without POPs) do the same.\n"
	if Level.at_least(2):
		tr += "• [b]… with bit 0 clear[/b] — BX, BLX or POP {PC} jumped to an even address. Put [code].thumb_func[/code] on the line before a label whose address you load with [code]ldr r0, =label[/code].\n"
		tr += "• [b]Unaligned access[/b] — LDR/STR need an address that is a multiple of 4, LDRH/STRH a multiple of 2. Use LDRB/STRB for single bytes, and put [code].align 2[/code] before word data.\n"
	tr += "Press " + _k("Reload") + " to start again."
	_topic("trouble", "Troubleshooting", tr)

	_topic("glossary", "Glossary", _glossary())

	if Level.current < Level.MAX:
		_ids.append("")
		var i := topics.add_item("More at higher levels…")
		topics.set_item_selectable(i, false)
		topics.set_item_custom_fg_color(i, Palette.c("dim"))
		topics.set_item_tooltip(i, "Change the Level menu in the toolbar to see more of picosim and more of this guide")


# ── the short versions ──────────────────────────────────────────────────────
# What every page opens with: at most two short paragraphs, in plain words,
# with everything a student must know to use that part of picosim.  The rest
# of the page is behind "Show the full details" (see _page).

func _summaries() -> Dictionary:
	var l2 := Level.at_least(2)
	var l3 := Level.at_least(3)
	var l4 := Level.at_least(4)
	var y := _c("changed")
	var d := {}
	d["start"] = "picosim runs programs written in [b]assembly language[/b], the instructions the Raspberry Pi Pico's processor understands, and lets you watch them one step at a time. To begin, click " + _k("Examples") + " and pick [b]Say hello[/b]. Press " + _k("Step instruction") + " (" + _k("S") + ") to run one instruction: whatever changed turns [color=" + y + "]yellow[/color] in the Registers, the " + _link("explain", "Explain this step") + " panel says what happened and what comes next, and anything printed appears in the serial console. " + _k("Run") + " (" + _k("Space") + ") lets the program go by itself, " + _k("◀ Step back") + " (" + _k("B") + ") undoes a step and " + _k("Reload") + " starts over. Nothing you do can break anything.\n\n" + \
		"The " + _k("Level") + " menu (top right) decides how much of picosim you see: start at Level 1 and move up when your class does. Then read " + _link("tour", "The screen at a glance") + " and " + _link("running", "How a program runs") + "; the " + _link("glossary", "Glossary") + " explains any word you do not know."
	var panels := "the [b]Registers[/b] (top left) show the numbers the CPU is working on, the [b]Disassembly[/b] under them lists your program with ▶ on the next instruction, [b]Explain this step[/b] (top right) describes each step, and the [b]serial console[/b] (bottom right) shows what the program prints"
	if l2:
		panels += "; the [b]Pico board[/b] (bottom left) shows the chip's pins and the [b]Memory[/b] tab shows the bytes in memory"
	if l3:
		panels += "; " + _k("Show CPU Diagram") + " adds a drawing of the processor's insides with the [b]Walkthrough[/b] beside it, which lists your program while the Disassembly makes way for the Memory tab"
	d["tour"] = "Across the top is the toolbar: " + _k("Open…") + " and " + _k("Examples") + " load a program, " + _k("Reload") + " starts it over, " + _k("◀ Step back") + ", " + _k("Step instruction") + " and " + _k("Run") + " move through it, and " + _k("Speed") + " sets how fast Run goes. Below it, " + panels + ".\n\n" + \
		"Hover any button for a reminder of what it does, or click the small " + _k("?") + " beside a panel's title to open its page in this guide. A greyed-out button cannot be used right now: for example Step after the program has finished, until you press " + _k("Reload") + ". " + _k("View") + " ▸ [b]Show the tour again[/b] replays the short first-run tour."
	d["running"] = "Every program runs the same way: picosim's small [b]built-in OS[/b] calls your [code]main[/code], the CPU runs one instruction at a time from top to bottom (unless a branch jumps somewhere else), the program calls functions such as [code]putchar[/code] to print, and [code]main[/code] ends with [code]pop {…, pc}[/code], which returns to the OS. The OS then stops the processor and the status text says [b]halted[/b]." + \
		(" At Level 1 the OS's own instructions are hidden, so ▶ starts on [code]main[/code] and each [code]bl putchar[/code] is one step." if not l2 else " From Level 2 you also step through the OS's instructions: three at the start and two inside each putchar; " + _k("Step over") + " (" + _k("O") + ") runs a whole call in one step.") + "\n\n" + \
		_k("Step instruction") + " runs one instruction. " + _k("Run") + " keeps going until the program ends, reaches a [b]breakpoint[/b] (" + _bp_click() + ") or you press " + _k("Pause") + ". " + _k("◀ Step back") + " undoes a step, and " + _k("Reload") + " starts again. The full details walk through Say hello line by line, with the real values."
	d["programs"] = "A program is a plain text file whose name ends in [code].s[/code]. It starts with three lines ([code].syntax unified[/code], [code].cpu cortex-m0plus[/code], [code].thumb[/code]), then the label [code]main:[/code] and one instruction per line; anything after [code]@[/code] is a comment. [code]main[/code] begins with [code]push {r7, lr}[/code] and ends with [code]pop {r7, pc}[/code], and [code]bl putchar[/code] prints the character in R0.\n\n" + \
		"Write and save the file in any text editor (VS Code, for example); picosim reloads it by itself when you save. " + _k("Open…") + " (" + _k("Ctrl/Cmd+O") + ") loads a file. If the assembler finds a mistake, a box lists each wrong line and what usually fixes it. The easiest start is a copy of an example."
	d["challenges"] = "Some examples set a [b]challenge[/b]: a small change for you to make, shown in a strip under the toolbar. Change the file, save it and run the program to the end: the strip turns [color=" + _c("green") + "]green[/color] if your program did it, or lists what is still wrong. Teachers can add their own with [code]@ Challenge:[/code] and [code]@ Expect:[/code] comment lines (see the details)."
	d["numbers"] = "The CPU stores everything as bits (0s and 1s), and picosim shows them in [b]hexadecimal[/b]: base 16, with the digits 0–9 and A–F and [code]0x[/code] in front. One hex digit is exactly 4 bits, so a 32-bit register is 8 hex digits. For example [code]0x2A[/code] = 2 × 16 + 10 = 42. Hover a register to see its value in decimal and binary too.\n\n" + \
		"Characters are numbers as well ([b]ASCII[/b]): 'H' is 72 ([code]0x48[/code]) and the digits '0' to '9' are 48 to 57. That is why printing the number 7 needs the character '7': add [code]'0'[/code] (48) before calling putchar."
	d["instructions"] = "Each line of a program is one [b]instruction[/b]: its name (the mnemonic, such as [code]movs[/code] or [code]adds[/code]) and what it works on (registers such as [code]r0[/code] and constants such as [code]#5[/code]). The Level 1 examples use [code]movs[/code] (put a value in a register), [code]adds[/code] and [code]subs[/code] (add and subtract), [code]cmp[/code] (compare), [code]b[/code] and the conditional branches (jump), [code]bl[/code] (call a function) and [code]push[/code] / [code]pop[/code] (save and restore registers).\n\n" + \
		"Two rules avoid most mistakes: write [code]movs[/code], [code]adds[/code] and [code]subs[/code] with the [b]S[/b] on the end, and use registers [b]R0 to R7[/b]. A constant in [code]movs[/code], [code]cmp[/code] or two-register [code]adds[/code] / [code]subs[/code] must be 0 to 255; the three-register form ([code]adds r0, r1, #3[/code]) takes only 0 to 7."
	d["branches"] = "A program makes decisions in two steps. First an instruction such as [code]cmp r4, #6[/code] compares two numbers and sets the four [b]flags[/b]: N (negative), Z (zero), C (carry) and V (overflow). Then a conditional branch such as [code]bne loop[/code] reads the flags and either jumps to the label or carries on with the next line.\n\n" + \
		"[code]beq[/code] jumps if the numbers were equal and [code]bne[/code] if they were not; [code]blt[/code], [code]bgt[/code], [code]ble[/code] and [code]bge[/code] compare signed numbers, and [code]blo[/code], [code]bhi[/code], [code]bls[/code] and [code]bhs[/code] unsigned ones. A loop works this way: change a counter, compare it, and branch back while the loop is not finished."
	d["examples"] = "Three examples for Level 1, opened from " + _k("Examples") + ": [b]Say hello[/b] prints [code]Hi![/code] one letter at a time ([code]movs[/code] and [code]bl putchar[/code]), [b]Add two numbers[/b] adds 3 and 4 and prints 7, and [b]Count with a loop[/b] prints [code]12345[/code] with a loop ([code]cmp[/code] and [code]bne[/code]). Each file starts with a comment that says what to watch.\n\n" + \
		"Step through each one with " + _link("explain", "Explain this step") + " open, then try the change suggested in its [b]Try it[/b] line or its challenge."
	d["memmap"] = "Memory is one long row of bytes, and each byte has a number called its [b]address[/b]. A [b]word[/b] is 4 bytes (what one register holds) and a halfword 2. picosim keeps its built-in OS at [code]0x0000[/code]–[code]0x2FFF[/code], your program from [code]0x3000[/code] upward and the stack at the top, just below [code]0x10000[/code]; the Pico's hardware (its pins) is at high addresses such as [code]0xD0000000[/code].\n\n" + \
		"A word is stored lowest byte first ([b]little-endian[/b]), so [code]0x0000300D[/code] appears in memory as [code]0D 30 00 00[/code]. A word must start at an address that is a multiple of 4, and a halfword at a multiple of 2; otherwise the program stops with a fault, as on a real Pico."
	d["loadstore"] = "The ALU works only on registers, so a value in memory must be [b]loaded[/b] into a register ([code]ldr[/code]), worked on, and [b]stored[/b] back ([code]str[/code]). The address goes in square brackets: [code]ldr r0, [lb]r1][/code] means \"R0 = the word at the address in R1\". [code]ldrb[/code] / [code]strb[/code] move one byte and [code]ldrh[/code] / [code]strh[/code] a halfword.\n\n" + \
		"To put an address, or any number bigger than 255, into a register, write [code]ldr r1, =label[/code]: the assembler stores the number just after your code and loads it from there. Data such as [code]numbers: .word 10, 20[/code] goes after a [code].data[/code] line."
	d["stack"] = "A [b]function[/b] is code you call with [code]bl name[/code] and that comes back with [code]bx lr[/code]: [code]bl[/code] puts the return address in LR before it jumps. A function that calls another function must save LR first, because the second [code]bl[/code] overwrites it. It saves it on the stack with [code]push {…, lr}[/code] and returns with [code]pop {…, pc}[/code].\n\n" + \
		"The [b]stack[/b] is memory at the top of RAM. SP points at its top, and it grows down: each push moves SP down 4 bytes per register. The rules: arguments and results go in R0–R3, a function may change R0–R3, and it must put R4–R7 back as it found them."
	d["gpio"] = "The [b]GPIO pins[/b] (GP0–GP29) can be [b]outputs[/b], which your program drives high (1) or low (0) to light an LED, or [b]inputs[/b], which it reads to see a button. A program controls them by storing to special addresses ([b]memory-mapped I/O[/b]): write 5 to the pin's IO_BANK0 CTRL register to connect it to your program, write its bit to [code]GPIO_OE_SET[/code] to make it an output, then write its bit to [code]GPIO_OUT_SET[/code], [code]GPIO_OUT_CLR[/code] or [code]GPIO_OUT_XOR[/code] to make it high, low or flip it.\n\n" + \
		"Each pin is one bit in these registers: GP15 is bit 15, so its [b]mask[/b] is [code]1 << 15[/code] = [code]0x8000[/code]. To read a button, load [code]GPIO_IN[/code] ([code]0xD0000004[/code]) and test the pin's bit with [code]ands[/code]. The [b]Blink an LED[/b] and [b]Buttons and LEDs[/b] examples do both."
	d["examples2"] = "Five examples for Level 2: [b]Add up a list in memory[/b] ([code]ldr[/code] and [code]str[/code]), [b]Blink an LED[/b] (GPIO outputs), [b]Echo what you type[/b] ([code]getchar[/code]), [b]Call a function[/b] ([code]bl[/code], LR and the stack) and [b]Buttons and LEDs[/b] (GPIO inputs). Keep the Memory tab and the Pico board in view while you step: that is where these programs show what they do."
	d["registers"] = "The Registers panel (top left) shows the CPU's 16 registers: R0–R12 hold your values, [b]SP[/b] points at the stack, [b]LR[/b] holds a function's return address and [b]PC[/b] is the address of the next instruction. Whatever the last step changed turns [color=" + y + "]yellow[/color], and the line under the flags says which line of your file changed it.\n\n" + \
		"The four check boxes are the flags N Z C V. The menu at the top right shows the values in " + _k("Hex") + ", " + _k("Decimal") + " or " + _k("Signed") + "; hover a register to see every form at once, or click one, type a new value and press " + _k("Enter") + " (" + _k("Reload") + " puts everything back)."
	d["memory"] = ("From Level 3, while the CPU diagram is shown, this panel shows only the [b]Memory[/b] tab: the [b]Walkthrough[/b] beside the diagram lists your program and holds the breakpoints. " + _k("Hide CPU Diagram") + " brings the Disassembly back.\n\n" if l3 else "") + "The Disassembly lists your program one instruction per row: its address, its bits ([b]Raw[/b]), its label, the instruction as the CPU sees it and the line of your file it came from ([b]Source[/b]). The row with ▶ runs next. Click a row's first column to put a [b]breakpoint[/b] (a red ●) there: " + _k("Run") + " stops just before that instruction." + \
		("\n\nThe [b]Memory[/b] tab shows memory as a place: a map of where everything lives, the bytes coloured by what they are (with labels and pointer tags on them), the stack as boxes that say what put each word there, and, after each step, which bytes it read or wrote. Hover a byte to have it explained; click one to change it." if l2 else "")
	d["pico"] = "The drawing of the Raspberry Pi Pico (bottom left) shows what your program does to the chip's pins. A pin your program drives high shows a green [b]1[/b], one driven low a dark [b]0[/b], and an input reading high a blue [b]1[/b]; a [color=" + y + "]yellow[/color] ring marks a pin the last step changed. Hover a pin for its numbers and state.\n\n" + \
		"Click an [i]input[/i] pin to drive it like a switch: floating → 1 → 0 → floating. In [b]Pico (USB serial)[/b] mode, with a board you flashed connected, the drawing shows your real board's pins instead."
	d["serial"] = "The serial console (bottom right) shows what your program prints with [code]putchar[/code]. When the program calls [code]getchar[/code] it waits for a key: click in the console and type (each key is sent at once), or type a line in the box underneath and press " + _k("Enter") + ". " + _k("Clear") + " empties it."
	d["layout"] = "Drag the bars between the panels to resize them; a panel you make bigger also gets bigger text. Zoom the whole window with " + _k("−") + " / " + _k("+") + " in the toolbar, " + _k("Ctrl/Cmd +") + " / " + _k("Ctrl/Cmd −") + " or " + _k("Ctrl/Cmd") + " + mouse wheel, and click the percentage to fit the window again. " + _k("View") + " picks the colours (System, Blue, Dark or Light). picosim remembers all of these."
	d["explain"] = "[b]Explain this step[/b] (top right) describes every step as you take it. [b]What just happened[/b] says in plain words what the instruction did, with the real values. [b]What happens next[/b] says what the next instruction will do, where the program goes after it ([b]Then[/b]) and what that is for ([b]Why[/b], often using the comment on that line of the program). It is exact: picosim really runs the next instruction on a copy of the machine to find out.\n\n" + \
		"Tick [b]More detail[/b] for exactly what changed (registers, flags" + (", memory and pins" if l2 else "") + "), how the instruction works" + (", how it is stored in memory" if l2 else "") + (" and its clock cycles" if l3 else "") + ". While " + _k("Run") + " is on, the panel waits, then explains where Run stopped. Click its title to fold it away."
	d["hardware"] = "The same [code].s[/code] file can run on a real Raspberry Pi Pico plugged in by USB. Choose [b]Pico (USB serial)[/b] in the mode menu and click " + _k("Flash") + ": picosim builds your program, copies it to the board and connects the console, so you see what the board prints. If picotool is not installed, hold the board's [b]BOOTSEL[/b] button while you plug it in, so it appears as a drive.\n\n" + \
		"The first build takes a few minutes because it downloads the Pico SDK; later ones take seconds. If Flash fails the console says why, and the details list what to do. Stepping, the registers and the CPU diagram work only in Simulator mode."
	d["cpuintro"] = "Inside the processor, every instruction is a short series of [b]clock cycles[/b]. In each cycle the [b]control unit[/b] (a finite-state machine, or FSM) turns on [b]control signals[/b] that tell the [b]datapath[/b] (the registers, a shared bus, the ALU, adders and memory) what to do; at the [b]clock edge[/b] that ends the cycle, the registers take their new values.\n\n" + \
		"This teaching processor has one bus and one memory, so every instruction first spends 3 cycles fetching itself from memory, and most take 5 to 7 cycles in all. " + _k("Show CPU Diagram") + " draws it, and " + _k("Step cycle") + " (" + _k("C") + " or " + _k("F11") + ") runs one cycle at a time."
	d["blocks"] = "The CPU diagram is built from a few kinds of part. [b]Registers[/b] store a value and change only at the clock edge. The [b]bus[/b] carries one value at a time between parts, and [b]tri-state buffers[/b] decide which part drives it. [b]Multiplexers[/b] (muxes) pick one of their inputs, [b]adders[/b] and the [b]ALU[/b] compute, [b]memory[/b] reads or writes at the address in MAR, and the [b]FSM[/b] controls them all.\n\n" + \
		"Muxes, adders and the ALU have no memory: their output follows their inputs straight away. Registers remember, which is why the diagram shows a register's new value as a [color=" + y + "]yellow[/color] [code]old → new[/code] caption until the next cycle starts."
	d["diagram"] = "The CPU diagram shows the multi-cycle datapath during the cycle that just ran. [color=" + _c("accent") + "]Bright blue[/color] wires and boxes carry data in this cycle, [color=" + _c("purple") + "]purple dashed[/color] stubs are control signals from the FSM (bright means on), and a [color=" + y + "]yellow[/color] [code]old → new[/code] caption marks a register that takes a new value at the clock edge. When you step, a glowing pulse follows the data along the wires.\n\n" + \
		"The [b]heat tint[/b] behind each box shows how much of the run that part has been used so far (" + _k("View") + " ▸ [b]Heat overlay[/b] turns it off). Hover a block for a one-line summary, or click it for a card that explains what it does and when it is used. The [b]Walkthrough[/b] beside the diagram lists every step of your program with ▶ on the one you are on: click a step to read what it does, and " + _k("Go here") + " jumps the CPU there."
	d["cycles"] = "The cycle inspector under the diagram follows the current instruction one clock cycle at a time. The left side lists its cycles (click one to look at it again) with the phase chips F, D, EA, FO, EX and SR; the right side says what the cycle does, which control signals it turns on and what changes at the clock edge.\n\n" + \
		"Every instruction starts with the same 3 fetch cycles. Typical totals: " + _typical_cycles() + "."
	d["tutorial"] = "This tutorial follows one instruction, [code]adds r2, r0, r1[/code] from [b]Add two numbers[/b], through its six clock cycles with the real values: three fetch cycles, DECODE, FETCH_OPERANDS (R0 and R1 go into the ALU) and EXECUTE_COMMIT (the sum goes into R2 and the flags). Set a breakpoint on it, press " + _k("Run") + ", then press " + _k("Step cycle") + " once for each row of the table."
	d["kinds"] = "After the 3-cycle fetch, each kind of instruction uses its own states. A load or store works out its address (EVALUATE_ADDRESS) and then reads or writes memory: " + _cyc("LDR R2, [R0, #4]") + " cycles. A branch decides and changes the PC: " + _cyc("B label") + " cycles. BL saves the return address in LR and jumps: " + _cyc("BL label") + " cycles, because it is 32 bits long and is fetched in two halves. PUSH and POP move one register every two cycles: 6 + 2 per register."
	d["signals"] = "Control signals are how the FSM runs the datapath. Most follow a pattern: [code]GateX[/code] puts X on the bus, [code]LD.X[/code] makes X load a new value at the clock edge, [code]XMUX=…[/code] picks a mux input, [code]ALUK=…[/code] picks the ALU's operation, and [code]MEM.EN[/code] with [code]R/W=READ[/code] or [code]WRITE[/code] makes memory do an access. The details list every signal."
	d["states"] = "The FSM is always in exactly one [b]state[/b], and each state is one clock cycle that turns on its own control signals. Every instruction starts with [code]FETCH_ADDR → FETCH_MEMORY → FETCH_IR[/code], then DECODE, then the states its kind needs: for example FETCH_OPERANDS → EXECUTE_COMMIT for an ADDS. The details list every state, what it does and which instructions use it."
	d["components"] = "Every block on the multi-cycle datapath diagram, in the order data usually flows, with what it does, how it works and when it is used. The same card opens when you click a block on the diagram."
	d["pipeintro"] = "The real Pico's processor, the Cortex-M0+, overlaps instructions in a [b]2-stage pipeline[/b]: while one instruction is decoded and executed, the next one is already being fetched. When nothing gets in the way, an instruction finishes every clock cycle (CPI = 1), instead of every 5 to 7 cycles as on the multi-cycle datapath.\n\n" + \
		"Two things cost extra cycles: instructions that need Execute for more than one cycle (loads, stores, PUSH, POP and BL), and taken branches, where the instruction fetched behind the branch is thrown away ([b]flushed[/b]). Pipelining changes only [i]when[/i] things happen, never the results."
	d["pipeline"] = "Pick [b]2-stage pipeline[/b] in the machine menu to see the Pico's own processor. The left stage is [color=" + _c("stage_f") + "]Fetch[/color] and the right one [color=" + _c("stage_e") + "]Decode + Execute[/color], joined by the IF/EX pipeline register. Each instruction in flight is a coloured token that moves across as you step, and FLUSH turns red when a taken branch throws away the wrong instruction. The registers show the state after the instruction in Execute has finished.\n\n" + \
		"The [b]Walkthrough[/b] beside the diagram lists every instruction your program runs, with its pipeline cycles and ▶ on the one in Execute: click one to read why it takes the cycles it does, and " + _k("Go here") + " jumps the pipeline there."
	d["pipecycles"] = _k("Step cycle") + " (" + _k("C") + " or " + _k("F11") + ") moves the pipeline one clock cycle; " + _k("Step instruction") + " (" + _k("S") + " or " + _k("F10") + ") jumps to the next instruction's first Execute cycle. Under the stages, the instruction strip shows the last 8 cycles as a staircase, and the panel under the diagram explains what each stage holds and why it waits, refills or is flushed.\n\n" + \
		"A perfect 2-stage pipeline gets CPI = 1. Instructions that take several Execute cycles, and taken branches (1 extra cycle each on the M0+), are where cycles are lost. With only one Execute stage there are no data hazards."
	d["pipecomponents"] = "Every block on the pipeline diagram, Fetch stage first, with what it does and how it connects. The same card opens when you click a block on the diagram."
	d["perfmath"] = "A program's run time is [b]N × CPI ÷ f[/b]: N is how many instructions it runs, CPI the average number of clock cycles per instruction, and f the clock frequency (the RP2040 runs at 125 MHz, so one cycle lasts 8 ns). For the [b]Add up a list in memory[/b] example the multi-cycle datapath takes 205 cycles (CPI 6.41) and the RP2040's pipeline 50, so the pipeline is 205 ÷ 50 = 4.1 times as fast."
	d["performance"] = "The Performance window (" + _k("F2") + ") times the run so far on three machines that give exactly the same results: the multi-cycle teaching datapath, the RP2040's 2-stage pipeline and a 3-stage teaching pipeline. [b]Metrics[/b] shows cycles, CPI and time; [b]Compare[/b] tries a slower multiplier or slower memory; [b]Pipeline[/b] draws a cycle-by-cycle chart; [b]Heat map[/b] shows which parts were busy. The RP2040's numbers come from its datasheet (Table 81)."
	d["heat"] = "The heat map (Performance ▸ [b]Heat map[/b]) colours each part of the CPU, each register and each flag by how much your program used it: the stronger the colour, the busier the part. [b]Totals[/b] gives each part's share of all cycles, [b]Timeline[/b] shows the use cycle by cycle, and [b]Per line[/b] shows which lines of your program cause it.\n\n" + \
		"To see what a change saves: " + _k("Save baseline") + ", change the program, run it again and choose [b]Compare[/b]. The multi-cycle counts are exact; the pipeline's are an estimate from the datasheet's cycle counts."
	var keys := _k("S") + " steps one instruction, " + _k("B") + " steps back, " + _k("Space") + " runs or pauses"
	if l2:
		keys += ", " + _k("O") + " steps over a call"
	if l3:
		keys += ", " + _k("C") + " steps one clock cycle"
	if l4:
		keys += ", " + _k("F2") + " opens the Performance window"
	d["keys"] = keys + ", " + _k("F1") + " opens this guide, " + _k("Ctrl/Cmd+O") + " opens a file and " + _k("Ctrl/Cmd+R") + " reloads it. Holding a step key repeats it. The keys work only in Simulator mode, and not while you are typing in a box or in the serial console."
	d["trouble"] = "Most problems have a quick fix. If Step does nothing, the program has finished: press " + _k("Reload") + ". If it is waiting, type a key in the serial console. If Run is slow, move the " + _k("Speed") + " slider to the right. If the program will not load, the [b]Could not load program[/b] box gives the line number and what is wrong: fix that line and save.\n\n" + \
		"A [b]CPU fault[/b] message in the console means the program did something that would crash a real Pico. The most common cause is a [code]main[/code] without [code]pop {…, pc}[/code] at its end. The details list every message you may see, with what to do."
	d["glossary"] = "Short meanings of the words used in picosim and in this guide, in alphabetical order. More words appear as you raise the Level."
	return d


# ── getting-started pages ───────────────────────────────────────────────────
# Written for a student who missed the first class: everything a level 1
# student sees is explained here, and the values quoted are the ones the
# examples really produce.

## Start here's "first five minutes".  At level 1 the built-in OS is hidden
## (programs start at main and bl putchar is one step); from level 2 every
## instruction the CPU runs is shown, the OS's too.
func _first_steps() -> String:
	var t := _sub("Your first five minutes")
	t += "1. Click " + _k("Examples") + " in the toolbar and pick [b]Say hello[/b]. Your program appears in the Disassembly list on the left; the row marked ▶ is the instruction that runs next. The [b]Source[/b] column shows the line of your file that each row came from.\n"
	if not Level.at_least(2):
		t += "2. ▶ is on [code]main[/code], the first line of your program. Press " + _k("Step instruction") + " (" + _k("S") + ") once: [code]push {r7, lr}[/code] runs and SP changes.\n"
		t += "3. Step once more and look at the " + _link("registers", "Registers") + ": the one that just changed turns [color=" + _c("changed") + "]yellow[/color]. After [code]movs r0, #'H'[/code], R0 holds [code]0x00000048[/code], the " + _link("numbers", "ASCII code") + " for the letter H.\n"
		t += "4. Step again. [code]bl putchar[/code] calls the function that prints the character in R0, and an [b]H[/b] appears in the " + _link("serial", "serial console") + " on the right.\n"
	else:
		t += "2. Press " + _k("Step instruction") + " (" + _k("S") + ") three times. These first three instructions belong to picosim's small [b]built-in OS[/b], which starts your program: R0 briefly holds [code]0x00002FFC[/code] and then [code]0x00003001[/code]. Now ▶ is on [code]main[/code], the first line of [i]your[/i] program.\n"
		t += "3. Step twice more and look at the " + _link("registers", "Registers") + ": the one that just changed turns [color=" + _c("changed") + "]yellow[/color]. After [code]movs r0, #'H'[/code], R0 holds [code]0x00000048[/code], the " + _link("numbers", "ASCII code") + " for the letter H.\n"
		t += "4. Step three more times. [code]bl putchar[/code] jumps into the OS's [code]putchar[/code] (address [code]0010[/code]), [code]svc #2[/code] prints the [b]H[/b] in the " + _link("serial", "serial console") + " on the right, and [code]bx lr[/code] comes back to the next line of your program. (" + _k("Step over") + " does all three at once.)\n"
	t += "5. Press " + _k("Run") + " (" + _k("Space") + ") to let the rest go by itself. When the status text at the top says [b]halted[/b], the program has finished; press " + _k("Reload") + " to watch it again.\n"
	t += "Each time you step, the " + _link("explain", "Explain this step") + " panel at the top right says what that instruction did and what the next one will do, so you can follow along without guessing.\n"
	return t


## "The screen at a glance": a map of the panels and every toolbar control
## the current level shows.
func _tour() -> String:
	var t := _h("The screen at a glance")
	t += "Before a program is loaded, picosim shows a welcome page with the examples; click one to open it. After that the window has a toolbar across the top and these panels:\n\n"
	var left := "[b]Registers[/b]\n[color=%s]the CPU's 16 registers and flags[/color]\n\n[b]Disassembly[/b]\n[color=%s]your program, one instruction per row[/color]" % [_c("dim"), _c("dim")]
	if Level.at_least(2):
		left += "\n\n[b]Raspberry Pi Pico board[/b]\n[color=%s]the chip's pins[/color]" % _c("dim")
	var right := "[b]Explain this step[/b]\n[color=%s]what the last step did, and what happens next[/color]\n\n" % _c("dim") + \
		"[b]Serial console[/b]\n[color=%s]what your program prints, and the keys you type[/color]" % _c("dim")
	if Level.at_least(3):
		right = right.replace("\n\n[b]Serial", "\n\n[b]CPU diagram[/b] (when shown)\n[color=%s]the datapath, cycle by cycle, with the Walkthrough beside it: your program, step by step[/color]\n\n[b]Serial" % _c("dim"))
		left = left.replace("[b]Disassembly[/b]\n[color=%s]your program, one instruction per row" % _c("dim"),
			"[b]Disassembly[/b], or [b]Memory[/b] with the diagram shown\n[color=%s]your program, one instruction per row, or the bytes in memory" % _c("dim"))
	t += "[table=2][cell bg=%s border=%s padding=16,12,16,12 expand=1]%s[/cell][cell bg=%s border=%s padding=16,12,16,12 expand=1]%s[/cell][/table]\n" % [
		_c("panel_alt"), _c("edge"), left, _c("panel_alt"), _c("edge"), right]
	t += "Click a panel's name to read about it: " + _link("registers", "Registers") + ", " + _link("memory", "Disassembly") + ", " + _link("explain", "Explain this step") + ", " + _link("serial", "Serial console") + ". Drag the bars between panels to resize them.\n"
	t += _sub("The toolbar, left to right")
	var rows := [
		[_k("Open…"), "Open a [code].s[/code] file of your own (" + _k("Ctrl/Cmd+O") + ")."],
		[_k("Examples"), "Open one of the example programs. One marked [i](Level 2)[/i] or higher is meant for later, but opens all the same."],
		[_k("Reload"), "Read the file again and start the program from the beginning (" + _k("Ctrl/Cmd+R") + "). Use it after you save a change, or to watch again."],
	]
	if Level.at_least(2):
		rows.append([_k("⚙ Settings"), "Only for programs that have settings, such as which pin an LED is on. See " + _link("programs", "Writing programs") + "."])
	rows.append([_k("◀ Step back"), "Undo the last step (" + _k("B") + "; hold it to keep going back). Registers, flags, memory" + (", pins" if Level.at_least(2) else "") + " and the console go back to what they were." + (" Partway through an instruction it goes back one clock cycle." if Level.at_least(3) else "")])
	rows.append([_k("Step instruction"), "Run exactly one instruction, then stop (" + _k("S") + " or " + _k("F10") + "; hold it to keep stepping)." + (" On a call into the built-in OS ([code]bl putchar[/code]) it runs the whole call." if not Level.at_least(2) else "")])
	if Level.at_least(2):
		rows.append([_k("Step over"), "On a [code]bl[/code] line, run the whole function call and stop on the line after it (" + _k("O") + "). On any other line it is the same as Step instruction."])
	if Level.at_least(3):
		rows.append([_k("Step cycle"), "Run one clock cycle of the CPU (" + _k("C") + " or " + _k("F11") + "). See " + _link("cycles", "Cycle by cycle") + "."])
	rows.append([_k("Run") + " / " + _k("Pause"), "Let the program run by itself; press again to pause (" + _k("Space") + " or " + _k("F5") + "). It stops at the end, at a breakpoint, or on a CPU fault."])
	rows.append([_k("Speed"), "How fast Run goes, in clock cycles per second ([code]cyc/s[/code]). An instruction takes several clock cycles (about 6 or 7 on average in the Level 1 examples), so the starting speed of 8 cyc/s runs about one instruction a second. [b]Max[/b] runs as fast as possible." + (" On the 2-stage pipeline it counts pipeline cycles instead." if Level.at_least(4) else "")])
	if Level.at_least(2):
		rows.append([_k("Simulator") + " menu", "Switch to [b]Pico (USB serial)[/b] to run on a real board: see " + _link("hardware", "Running on a real Pico") + "."])
	rows.append(["Status text", "How many instructions have run" + (" and clock cycles" if Level.at_least(3) else "") + ", and why the program stopped: [b]halted[/b] (finished), [b]waiting for input[/b] (type a key in the console) or [b]stopped by a CPU fault[/b] (read the console)."])
	rows.append([_k("−") + " " + _k("100%") + " " + _k("+"), "Zoom the whole window. Click the percentage to fit it to the window again."])
	if Level.at_least(3):
		rows.append([_k("Show CPU Diagram"), "Show the CPU diagram on the right. See " + _link("diagram", "Datapath diagram") + "."])
	if Level.at_least(4):
		rows.append([_k("Multi-cycle") + " menu", "Pick what the diagram shows: the multi-cycle datapath or the " + _link("pipeline", "2-stage pipeline") + "."])
	rows.append([_k("View"), "The colour scheme: [b]System[/b] (follows your computer's light or dark setting), [b]Blue[/b], [b]Dark[/b] or [b]Light[/b]; and [b]Show the tour again[/b]."])
	if Level.at_least(4):
		rows.append([_k("Performance"), "Cycle counts and comparisons (" + _k("F2") + "). See " + _link("performance", "Performance window") + "."])
	rows.append([_k("Level"), "How much of picosim to show (you are on " + Level.title(Level.current) + "). See " + _link("start", "Start here") + "."])
	rows.append([_k("Guide"), "This guide (" + _k("F1") + "). " + _k("Esc") + " closes it."])
	t += _grid(["Control", "What it does"], rows, 1)
	t += "Buttons that cannot be used right now are greyed out: for example Step and Run after the program has halted, until you press " + _k("Reload") + ".\n"
	t += "Every panel has a small " + _k("?") + " at the right of its title: it opens this guide at the page about that panel.\n"
	t += _sub("The tour")
	t += "The first time you load a program, a short tour points out Step, Explain this step, the Registers, " + ("the Walkthrough (the Disassembly while the CPU diagram is hidden)" if Level.at_least(3) else "the Disassembly") + ", the console and this guide, one at a time. " + _k("Next ▶") + " moves on and " + _k("Skip tour") + " (or " + _k("Esc") + ") ends it. To see it again, choose " + _k("View") + " ▸ [b]Show the tour again[/b].\n"
	return t


## "How a program runs": the Say hello example from power-on to halt, with
## the register values it really produces.
func _running() -> String:
	var t := _h("How a program runs")
	var y := _c("changed")
	var l1 := not Level.at_least(2)
	if l1:
		t += "Every program runs the same way: picosim's small [b]built-in OS[/b] starts it, it runs one instruction at a time, it calls functions such as [code]putchar[/code] to print, and it ends by returning to the OS. At Level 1 the OS's own instructions are hidden, so you only step through [i]your[/i] lines. Here is the [b]Say hello[/b] example, exactly as " + _k("Step instruction") + " shows it. Open it from " + _k("Examples") + " and step along.\n"
		t += _sub("Before you press anything")
		t += "When the program loads, the OS has already run its three start-up instructions and called your [code]main[/code]. That is why ▶ starts on [code]main[/code] at [code]3000[/code], and why R0 already holds [code]0x00003001[/code] (the address of main, plus 1) and LR holds [code]0x00000007[/code] (where main returns to, in the OS). At Level 2 you will step through those instructions too.\n"
		var rows := [
			["1", "[code]3000[/code]", _asm("push {r7, lr}"), "Saves R7 and LR on the stack, so SP goes down by 8 to [code]0x0000FFF8[/code]."],
			["2", "[code]3002[/code]", _asm("movs r0, #'H'"), "R0 = [code]0x00000048[/code], the character H. The Z flag is cleared."],
			["3", "[code]3004[/code]", _asm("bl putchar"), "Calls putchar, which prints the character in R0: [b]H[/b] appears in the console. LR = [code]0x00003009[/code] (the next line, plus 1): that is how putchar knew where to come back to."],
			["4–9", "", "", "The same two steps print [code]i[/code], [code]![/code] and a new line."],
			["10", "[code]301A[/code]", _asm("movs r0, #0"), "R0 = 0, which means \"finished without errors\". The result is zero, so the Z flag turns on."],
			["11", "[code]301C[/code]", _asm("pop {r7, pc}"), "Takes R7 and the saved LR back off the stack, putting the LR value into PC: main returns to the OS, which stops the processor. SP is back to [code]0x00010000[/code] and the status text says [color=%s][b]halted[/b][/color]." % y],
		]
		t += _grid(["Press", "Address", "Instruction", "What happens"], rows, 3)
	else:
		t += "Every program runs the same way: a small [b]built-in OS[/b] starts it, it runs one instruction at a time, it calls the OS to print, and it ends by returning to the OS. Here is the [b]Say hello[/b] example with every instruction the CPU runs, the OS's included, exactly as " + _k("Step instruction") + " shows it from Level 2. Open it from " + _k("Examples") + " and step along.\n"
		var rows := [
			["1", "[code]0000[/code]", _asm("ldr r0, [pc, #8]"), "OS: R0 = [code]0x00002FFC[/code], the address where picosim stored the address of your [code]main[/code]."],
			["2", "[code]0002[/code]", _asm("ldr r0, [r0]"), "OS: R0 = [code]0x00003001[/code], the address of [code]main[/code] ([code]3000[/code]). The extra 1 means \"Thumb code\"; every Pico code address has it."],
			["3", "[code]0004[/code]", _asm("blx r0"), "OS: calls [code]main[/code]. LR = [code]0x00000007[/code] (where to come back to), PC = [code]0x00003000[/code]."],
			["4", "[code]3000[/code]", _asm("push {r7, lr}"), "Your program starts. Saves R7 and LR on the stack, so SP goes down by 8 to [code]0x0000FFF8[/code]."],
			["5", "[code]3002[/code]", _asm("movs r0, #'H'"), "R0 = [code]0x00000048[/code], the character H. The Z flag is cleared."],
			["6", "[code]3004[/code]", _asm("bl putchar"), "Calls the OS's putchar: LR = [code]0x00003009[/code] (the next line, plus 1), PC = [code]0x00000010[/code]."],
			["7", "[code]0010[/code]", _asm("svc #2"), "OS: a [i]supervisor call[/i] asks picosim to print the character in R0. [b]H[/b] appears in the console."],
			["8", "[code]0012[/code]", _asm("bx lr"), "OS: returns to the address in LR, the line after the [code]bl[/code] ([code]3008[/code])."],
			["9–20", "", "", "The same three steps print [code]i[/code], [code]![/code] and a new line."],
			["21", "[code]301A[/code]", _asm("movs r0, #0"), "R0 = 0, which means \"finished without errors\". The result is zero, so the Z flag turns on."],
			["22", "[code]301C[/code]", _asm("pop {r7, pc}"), "Takes R7 and the saved LR back off the stack, putting the LR value into PC: this returns to the OS. SP is back to [code]0x00010000[/code]."],
			["23", "[code]0006[/code]", _asm("bkpt #0"), "OS: [i]breakpoint[/i] instruction. The simulator stops and the status text says [color=%s][b]halted[/b][/color]." % y],
		]
		t += _grid(["Step", "Address", "Instruction", "What happens"], rows, 3)
		t += _note("Step over", "Steps 1–3 and the [code]svc[/code] / [code]bx lr[/code] after every [code]bl putchar[/code] are the OS, not your file. On a [code]bl[/code] line, " + _k("Step over") + " (" + _k("O") + ") runs the whole call and stops on the line after it, so you can skip what you do not want to watch.")
	t += _sub("Step, Run and breakpoints")
	if l1:
		t += "• " + _k("Step instruction") + " (" + _k("S") + ") runs one line of your program. On [code]bl putchar[/code] it runs the whole call, so the character is printed in one step.\n"
	else:
		t += "• " + _k("Step instruction") + " (" + _k("S") + ") runs exactly one instruction, the OS's too; " + _k("Step over") + " (" + _k("O") + ") runs a whole [code]bl[/code] call.\n"
	t += "• " + _k("Run") + " (" + _k("Space") + ") keeps going at the " + _link("tour", "Speed") + " you chose, until the program halts, reaches a breakpoint, or you press " + _k("Pause") + ". If the program is waiting for a key ([code]getchar[/code]), Run waits too and carries on once you type one in the console.\n"
	t += "• A [b]breakpoint[/b] (" + _bp_click() + ") makes Run stop just before that instruction. Then you can Step from there.\n"
	t += "• " + _k("◀ Step back") + " (" + _k("B") + ") undoes the last step, so you can go back and watch a line again. Everything goes back with it, the console's text too. A key you typed for [code]getchar[/code] is handed to the program again when it gets there.\n"
	t += "• You can mix them: Run to a breakpoint, Step a few lines, Run again.\n"
	t += _sub("Starting again")
	t += "When the program has halted, Step and Run are greyed out. " + _k("Reload") + " reads the file again and starts it from the beginning, with every register and flag reset. When you save your file, picosim reloads it by itself. The console keeps what was printed before, under a grey [code][lb]loaded …][/code] line that marks the new start; " + _k("Clear") + " empties it.\n"
	t += _sub("Where the numbers come from")
	t += "The addresses are always the same: the OS is at [code]0000[/code]–[code]2FFF[/code] and your program starts at [code]3000[/code]. The stack is at the top of memory, just below [code]0x00010000[/code]" + (". See " + _link("programs", "Memory map") + "." if Level.at_least(2) else ", and you will look inside it at Level 2.") + "\n"
	return t


## "Hex, binary and ASCII".
func _numbers() -> String:
	var t := _h("Hex, binary and ASCII")
	t += "Inside the computer every value is a pattern of bits (0s and 1s). A register holds 32 of them. picosim shows them in [b]hexadecimal[/b] (hex), base 16, because each hex digit stands for exactly 4 bits, so 32 bits are always 8 hex digits.\n"
	t += _sub("The 16 hex digits")
	var rows := []
	for i in 8:
		var j := i + 8
		rows.append(["[b]%X[/b]" % i, str(i), "[code]%s[/code]" % _bits4(i), "[b]%X[/b]" % j, str(j), "[code]%s[/code]" % _bits4(j)])
	t += _grid(["Hex", "Decimal", "Binary", "Hex", "Decimal", "Binary"], rows)
	t += _sub("Reading a hex number")
	t += "Hex numbers start with [code]0x[/code] so you can tell them apart from decimal. Each place is worth 16 times the one to its right (1, 16, 256, 4096, …):\n"
	t += "• [code]0x48[/code] = 4 × 16 + 8 = [b]72[/b]\n"
	t += "• [code]0x2A[/code] = 2 × 16 + 10 = [b]42[/b]\n"
	t += "• [code]0x3000[/code] = 3 × 4096 = [b]12288[/b]\n"
	t += "Leading zeros change nothing: the Registers panel's [code]0x00000048[/code] is just [code]0x48[/code]. And to turn hex into binary, write each digit as its 4 bits: [code]0x48[/code] = [code]0100 1000[/code].\n"
	t += _note("Shortcut", "Hover any register to see its value in hex, decimal, signed decimal and binary at once, or switch the Registers panel's menu from " + _k("Hex") + " to " + _k("Decimal") + ".")
	t += _sub("Negative numbers")
	t += "The same 32 bits can be read two ways. As [b]unsigned[/b], [code]0xFFFFFFFF[/code] is 4 294 967 295. As [b]signed[/b] (two's complement), every value from [code]0x80000000[/code] up is negative, and [code]0xFFFFFFFF[/code] is −1, [code]0xFFFFFFFE[/code] is −2, and so on. The top bit (bit 31) is 1 for a negative number; that is what the N flag reports. Choose " + _k("Signed") + " in the Registers panel's menu to see registers this way.\n"
	t += _sub("Characters are numbers too (ASCII)")
	t += "A character is stored as its [b]ASCII code[/b]. [code]putchar[/code] prints the character whose code is in R0. The ones you will use most:\n"
	t += _grid(["Characters", "Hex", "Decimal"], [
		["[code]0[/code] [code]1[/code] … [code]9[/code]", "[code]0x30[/code] – [code]0x39[/code]", "48 – 57"],
		["[code]A[/code] [code]B[/code] … [code]Z[/code]", "[code]0x41[/code] – [code]0x5A[/code]", "65 – 90"],
		["[code]a[/code] [code]b[/code] … [code]z[/code]", "[code]0x61[/code] – [code]0x7A[/code]", "97 – 122"],
		["space", "[code]0x20[/code]", "32"],
		["[code]![/code]", "[code]0x21[/code]", "33"],
		["new line", "[code]0x0A[/code]", "10"],
	])
	t += "In your program you can write the character in quotes and let the assembler look up its code: [code]movs r0, #'H'[/code] is the same as [code]movs r0, #0x48[/code] and [code]movs r0, #72[/code]. (The Disassembly always shows the hex.)\n"
	t += _sub("Printing a number")
	t += "To print the [i]number[/i] 7 you must print the [i]character[/i] '7', whose code is 7 + [code]0x30[/code] = [code]0x37[/code]. So add [code]'0'[/code] (that is, [code]0x30[/code]) before calling putchar:\n"
	t += _codeblock("        movs    r0, #7\n        adds    r0, #'0'        @ R0 = 0x37, the character '7'\n        bl      putchar")
	t += "This works only for 0–9. For 10 it gives [code]0x3A[/code], which is the character [b]:[/b] — to print bigger numbers you must print one digit at a time.\n"
	return t


## "0100" for 4.
static func _bits4(n: int) -> String:
	var s := ""
	for b in range(3, -1, -1):
		s += "1" if n & (1 << b) else "0"
	return s


## "Instructions": every instruction the level 1 examples use, with the
## forms and ranges the assembler accepts.
func _instructions() -> String:
	var t := _h("Instructions")
	t += "Every instruction in the Level 1 examples. In the forms, [b]Rd[/b] is the register that gets the result, [b]Rn[/b] and [b]Rm[/b] are registers it reads, and [b]#imm[/b] is a constant.\n"
	t += _note("Two rules", "1. Write [code]movs[/code], [code]adds[/code] and [code]subs[/code] with the [b]S[/b] on the end (it means \"set the flags\").   2. Use registers [b]R0 to R7[/b]. Break either rule and the assembler says [i]cannot honor width suffix[/i].", "changed")
	t += _sub("Moving and arithmetic")
	t += _grid(["Form", "Example", "What it does", "Flags"], [
		[_asm("movs Rd, #imm"), _asm("movs r0, #'H'"), "Rd = imm. imm is 0 to 255.", "N Z"],
		[_asm("movs Rd, Rm"), _asm("movs r0, r4"), "Rd = Rm (copies the value).", "N Z"],
		[_asm("adds Rd, Rn, Rm"), _asm("adds r2, r0, r1"), "Rd = Rn + Rm.", "N Z C V"],
		[_asm("adds Rd, #imm"), _asm("adds r0, #'0'"), "Rd = Rd + imm. imm is 0 to 255.", "N Z C V"],
		[_asm("adds Rd, Rn, #imm"), _asm("adds r4, r4, #1"), "Rd = Rn + imm. imm is only 0 to 7.", "N Z C V"],
		[_asm("subs Rd, Rn, Rm"), _asm("subs r3, r1, r0"), "Rd = Rn − Rm.", "N Z C V"],
		[_asm("subs Rd, #imm"), _asm("subs r4, #1"), "Rd = Rd − imm. imm is 0 to 255.", "N Z C V"],
		[_asm("subs Rd, Rn, #imm"), _asm("subs r1, r2, #3"), "Rd = Rn − imm. imm is only 0 to 7.", "N Z C V"],
		[_asm("cmp Rn, #imm"), _asm("cmp r4, #6"), "Works out Rn − imm (0 to 255) only to set the flags. No register changes.", "N Z C V"],
		[_asm("cmp Rn, Rm"), _asm("cmp r0, r1"), "Works out Rn − Rm only to set the flags.", "N Z C V"],
	], 2)
	t += "The [b]Flags[/b] column lists the flags each instruction sets; the others keep their old values. " + _link("branches", "Flags and branches") + " explains them.\n"
	t += _sub("Jumping")
	t += _grid(["Form", "Example", "What it does"], [
		[_asm("b label"), _asm("b done"), "Always jump to the label."],
		[_asm("b<cond> label"), _asm("bne loop"), "Jump only if a condition on the flags is true, otherwise go on to the next line. See " + _link("branches", "Flags and branches") + "."],
		[_asm("bl label"), _asm("bl putchar"), "Call a function: put the address of the next line in LR, then jump."],
		[_asm("bx lr"), _asm("bx lr"), "Jump to the address in LR: how a function returns."],
	], 2)
	t += _sub("Saving registers: push and pop")
	t += _grid(["Form", "Example", "What it does"], [
		[_asm("push {list}"), _asm("push {r4, lr}"), "Save the listed registers on the stack (SP goes down 4 bytes per register)."],
		[_asm("pop {list}"), _asm("pop {r4, pc}"), "Load them back (SP goes up again). Popping into PC jumps to the saved address."],
	], 2)
	t += "Every example's [code]main[/code] begins with [code]push {r7, lr}[/code] (or [code]{r4, lr}[/code]) and ends with [code]pop {r7, pc}[/code]. The reason: [code]bl putchar[/code] overwrites LR, so the address [code]main[/code] must return to is saved first and popped straight into PC at the end.\n"
	if Level.at_least(2):
		t += "Registers are pushed in pairs because the ARM rules for calling functions keep the stack in steps of 8 bytes; R7 is there just to make the pair. If you use R4–R7, put them in the list too, so your program leaves them as it found them.\n"
	t += _sub("Other instructions you may meet")
	t += _grid(["Form", "What it does"], [
		[_asm("ands Rd, Rm") + "  " + _asm("orrs Rd, Rm") + "  " + _asm("eors Rd, Rm"), "Bitwise AND, OR and exclusive OR: Rd = Rd op Rm, bit by bit."],
		[_asm("lsls Rd, Rm, #n") + "  " + _asm("lsrs Rd, Rm, #n"), "Shift Rm's bits left or right by n places into Rd (left by 1 doubles, right by 1 halves an unsigned number)."],
		[_asm("muls Rd, Rm, Rd"), "Rd = Rm × Rd. The register that gets the result must also be the last one."],
		[_asm("mov Rd, Rm"), "Copy without setting flags; this one also works with R8–R12."],
	], 1)
	if Level.at_least(2):
		t += "Loads and stores ([code]ldr[/code], [code]str[/code]) are in the [b]Add up a list in memory[/b] example.\n"
	else:
		t += "Loads and stores ([code]ldr[/code], [code]str[/code]), which read and write memory, come at Level 2.\n"
	t += "Mnemonics can be written in capitals or not. picosim runs only the instructions the Pico's Cortex-M0+ processor has; the ARMv6-M Architecture Reference Manual lists them all.\n"
	return t


## "Flags and branches".
func _branches() -> String:
	var t := _h("Flags and branches")
	t += "A program decides what to do next in two steps: an instruction such as [code]cmp[/code] sets the [b]flags[/b], and then a conditional [b]branch[/b] reads them and either jumps or carries on.\n"
	t += _sub("The four flags")
	t += _grid(["Flag", "Name", "It is 1 when the result…"], [
		["[b]N[/b]", "Negative", "is negative: bit 31 of the result is 1."],
		["[b]Z[/b]", "Zero", "is zero. After [code]cmp a, b[/code] this means a = b."],
		["[b]C[/b]", "Carry", "carried out of bit 31 (adding), or did [i]not[/i] need to borrow (subtracting or [code]cmp[/code]). After [code]cmp a, b[/code], C = 1 means a ≥ b as unsigned numbers."],
		["[b]V[/b]", "Overflow", "was too big or too small to fit as a signed number, such as 0x7FFFFFFF + 1."],
	], 2)
	t += "Instructions that end in [b]S[/b] ([code]movs[/code], [code]adds[/code], [code]subs[/code], …) and [code]cmp[/code] set the flags. Branches, [code]bl[/code], [code]push[/code] and [code]pop[/code] never change them, so a flag keeps its value until the next flag-setting instruction. The flags are the N Z C V boxes in the " + _link("registers", "Registers panel") + ".\n"
	t += _sub("What cmp does")
	t += "[code]cmp r4, #6[/code] works out R4 − 6, sets the flags from the answer, and throws the answer away. From the [b]Count with a loop[/b] example:\n"
	t += _grid(["R4", "R4 − 6", "N", "Z", "C", "bne loop"], [
		["2", "−4", "1", "0", "0", "jumps back (not equal)"],
		["5", "−1", "1", "0", "0", "jumps back (not equal)"],
		["6", "0", "0", "[color=%s][b]1[/b][/color]" % _c("changed"), "1", "goes on to the next line"],
	], 5)
	t += _sub("Conditional branches")
	t += "Write the condition after [code]b[/code]. Each one reads like a question about the [code]cmp[/code] before it, [code]cmp a, b[/code]:\n"
	t += _grid(["Branch", "Jumps if", "Use for", "Flags tested"], [
		[_asm("beq"), "a = b", "any numbers", "Z = 1"],
		[_asm("bne"), "a ≠ b", "any numbers", "Z = 0"],
		[_asm("bgt"), "a > b", "signed numbers", "Z = 0 and N = V"],
		[_asm("bge"), "a ≥ b", "signed numbers", "N = V"],
		[_asm("blt"), "a < b", "signed numbers", "N ≠ V"],
		[_asm("ble"), "a ≤ b", "signed numbers", "Z = 1 or N ≠ V"],
		[_asm("bhi"), "a > b", "unsigned numbers", "C = 1 and Z = 0"],
		[_asm("bhs") + " (" + _asm("bcs") + ")", "a ≥ b", "unsigned numbers", "C = 1"],
		[_asm("blo") + " (" + _asm("bcc") + ")", "a < b", "unsigned numbers", "C = 0"],
		[_asm("bls"), "a ≤ b", "unsigned numbers", "C = 0 or Z = 1"],
		[_asm("bmi"), "result negative", "after any S instruction", "N = 1"],
		[_asm("bpl"), "result positive or zero", "after any S instruction", "N = 0"],
	], 2)
	t += "[b]Signed or unsigned?[/b] If your values can be negative, use the signed branches. For character codes, addresses and counts that never go below 0, either kind works while the numbers are small; the unsigned ones are the safe choice.\n"
	t += _sub("Pattern: a loop")
	t += _codeblock("        movs    r4, #0          @ i = 0\nloop:\n        @ ... the loop body: what to repeat ...\n        adds    r4, #1          @ i = i + 1\n        cmp     r4, #10\n        blt     loop            @ i < 10? go round again")
	t += "The body runs 10 times, with R4 = 0, 1, … 9. Keep the counter in R4–R7: [code]putchar[/code] may change R0–R3.\n"
	t += _sub("Pattern: if / else")
	t += _codeblock("        cmp     r0, r1\n        bgt     bigger          @ R0 > R1?\n        movs    r2, r1          @ no:  R2 = R1\n        b       done\nbigger:\n        movs    r2, r0          @ yes: R2 = R0\ndone:                           @ R2 = the larger of R0 and R1")
	t += "The [code]b done[/code] matters: without it the \"no\" path would run on into the \"yes\" path.\n"
	t += _note("Watch it happen", "Set a breakpoint on a branch, press Run, and look at the flags each time it stops. Then press Step instruction once to see whether it jumped.")
	return t


## "Level 1 examples": the level 1 examples with what to watch and
## something to try in each.
func _examples() -> String:
	var t := _h("Level 1 examples")
	t += "Open each one from " + _k("Examples") + " and keep this page beside it. Every example file also starts with a short [i]What to watch[/i] comment." + (" At Level 1 each [code]bl putchar[/code] is one press of Step, although putchar's two instructions still run (the status text counts them); from Level 2 you also step through the " + _link("running", "built-in OS") + "'s instructions (three at the start, two in each putchar)." if not Level.at_least(2) else " Remember that the first three steps, and the two steps after each [code]bl putchar[/code], are the " + _link("running", "built-in OS") + " (" + _k("Step over") + " skips them).") + "\n"
	var dim := _c("dim")
	var boot := 0 if Level.at_least(2) else 3   # level 1 counts from main
	# [instructions the status text counts, Step presses at level 1] (checked by running them)
	var runs := {"01": [23, 11], "02": [20, 12], "03": [52, 36]}
	var count := func(k: String) -> String:
		if Level.at_least(2):
			return "%d instructions" % runs[k][0]
		return "%d instructions, %d presses of Step" % [runs[k][0] - boot, runs[k][1]]
	t += "\n[font_size=%d][b]1 · Say hello[/b][/font_size]   [color=%s]01_hello.s · %s[/color]\n" % [roundi(21 * text_scale), dim, count.call("01")]
	t += "Prints [code]Hi![/code] one letter at a time. Shows how [code]movs[/code] puts a character's code in R0 and how [code]bl putchar[/code] prints it.\n"
	t += "• Before each [code]bl putchar[/code], R0 holds the code: [code]0x48[/code] H, [code]0x69[/code] i, [code]0x21[/code] !, [code]0x0A[/code] new line.\n"
	t += "• Watch LR change on every [code]bl[/code] ([code]0x00003009[/code], [code]0x0000300F[/code], …): it is always the address of the next line, plus 1.\n"
	t += "• " + _link("running", "How a program runs") + " goes through this example line by line.\n"
	t += "[b]Try it:[/b] make it print your initials. Copy a [code]movs[/code] / [code]bl putchar[/code] pair for each letter, and use [code]#' '[/code] for a space.\n"
	t += "\n[font_size=%d][b]2 · Add two numbers[/b][/font_size]   [color=%s]02_add.s · %s[/color]\n" % [roundi(21 * text_scale), dim, count.call("02")]
	t += "Puts 3 and 4 in registers, adds and subtracts them, and prints the sum.\n"
	t += "• R0 = 3, R1 = 4, then [code]adds r2, r0, r1[/code] makes R2 = 7 and [code]subs r3, r1, r0[/code] makes R3 = 1.\n"
	t += "• The [code]subs[/code] turns the C flag on: 4 − 3 did not need to borrow.\n"
	t += "• [code]adds r0, #'0'[/code] turns 7 into [code]0x37[/code], the character '7', which is printed. See " + _link("numbers", "Printing a number") + ".\n"
	t += "[b]Try it:[/b] change 3 and 4 in the file (keep the sum under 10), save and press " + _k("Reload") + ". Or, without editing: when ▶ reaches [code]movs r0, r2[/code], click R2 in the Registers panel, type 5, press Enter, then Run. It prints 5.\n"
	t += "\n[font_size=%d][b]3 · Count with a loop[/b][/font_size]   [color=%s]03_count_loop.s · %s[/color]\n" % [roundi(21 * text_scale), dim, count.call("03")]
	t += "Prints [b]12345[/b] with a loop: the same five lines run once for each digit.\n"
	t += "• R4 is the counter. It is kept in R4, not R0–R3, because putchar may change those; that is why [code]main[/code] pushes R4.\n"
	t += "• In the Disassembly, the row labelled [code]loop[/code] is where [code]bne loop[/code] jumps back to; it shows as [code]bne #0x3004[/code].\n"
	t += "• After [code]cmp r4, #6[/code]: while R4 is less than 6, N = 1 and Z = 0, so [code]bne[/code] jumps back. When R4 reaches 6, Z = 1 and the loop ends. The table in " + _link("branches", "Flags and branches") + " shows the values.\n"
	t += "[b]Try it:[/b] put a breakpoint on the [code]bne[/code] row and press Run several times to see the flags on each pass. Then make it count to 9 (change [code]#6[/code] to [code]#10[/code]).\n"
	if Level.at_least(2):
		t += "\n" + _link("examples2", "Level 2 examples") + " covers the rest: memory ([code]ldr[/code] / [code]str[/code]), an LED, typing, functions and buttons.\n"
	else:
		t += "\n" + _note("Later", "Five more examples (memory, an LED, typing, functions, buttons) are marked [i](Level 2)[/i] in the Examples menu. They open at Level 1 too, but some of what they show only appears at Level 2.")
	return t


## "Glossary": the words a level 1 student meets, in alphabetical order.
func _glossary() -> String:
	var t := _h("Glossary")
	var rows := [
		["Address", "A number that says where in memory something is, like a house number. Shown in hex, such as [code]3000[/code]."],
		["ASCII", "The code that gives each character a number: 'A' is [code]0x41[/code]. See " + _link("numbers", "Hex, binary and ASCII") + "."],
		["Assembler", "The program that turns your [code].s[/code] file into the bits the CPU runs. picosim runs it when you Open or Reload."],
		["Assembly language", "Writing a program as the processor's own instructions, one per line, with names instead of bits."],
		["Bit", "One binary digit, 0 or 1. A register has 32."],
		["Branch", "An instruction that jumps to another place in the program, always ([code]b[/code]) or only if a condition holds ([code]bne[/code], …)."],
		["Breakpoint", "A red ● on a Disassembly row" + (" or a Walkthrough step" if Level.at_least(3) else "") + ": Run stops just before that instruction."],
		["Built-in OS", "picosim's tiny operating system at addresses [code]0000[/code]–[code]2FFF[/code]. It starts your [code]main[/code] and provides [code]putchar[/code] and [code]getchar[/code]."],
		["Byte", "8 bits: two hex digits, 0 to 255."],
		["Clock cycle", "One tick of the CPU's clock. An instruction takes several." + (" See " + _link("cycles", "Cycle by cycle") + "." if Level.at_least(3) else " You will see them at Level 3.")],
		["Comment", "Text after [code]@[/code] that the assembler ignores; it is for people."],
		["Cortex-M0+", "The processor inside the Pico's RP2040 chip. picosim runs only its instructions."],
		["CPU fault", "A mistake that would crash a real Pico, such as running something that is not an instruction. picosim stops and explains it in the console."],
		["Directive", "A line starting with a dot, such as [code].thumb[/code]: an instruction to the assembler, not to the CPU."],
		["Disassembly", "Your program shown back from the bits in memory: see " + _link("memory", "Disassembly") + "."],
		["Flags", "N, Z, C and V: four bits describing the last result, used by conditional branches. See " + _link("branches", "Flags and branches") + "."],
		["Function", "A piece of code you call with [code]bl[/code] that returns with [code]bx lr[/code] or [code]pop {…, pc}[/code]. [code]putchar[/code] is one."],
		["Halted", "The program has finished (or faulted) and the CPU has stopped. Press Reload to start again."],
		["Hex", "Base 16, written with [code]0x[/code] in front: [code]0x2A[/code] = 42."],
		["Immediate", "A constant written inside the instruction after a [code]#[/code], as in [code]movs r0, #5[/code]."],
		["Instruction", "One step for the CPU, such as [code]adds r2, r0, r1[/code]."],
		["Label", "A name followed by a colon, such as [code]loop:[/code]; it stands for the address of the next line."],
		["LR", "Link register (R14): holds the address a function returns to."],
		["Mnemonic", "The name of an instruction: [code]movs[/code], [code]adds[/code], [code]bne[/code]."],
		["Operand", "What an instruction works on: the registers and constants after the mnemonic."],
		["PC", "Program counter (R15): the address of the next instruction."],
		["Register", "One of 16 storage boxes inside the CPU, each holding a 32-bit number."],
		["Serial console", "The text panel on the right: what your program prints, and where you type to it."],
		["SP and the stack", "The stack is memory used to save registers ([code]push[/code] / [code]pop[/code]); SP (R13) points to its top." + (" See " + _link("memory", "the Memory tab") + "." if Level.at_least(2) else "")],
		["Thumb", "The 16-bit instruction set the Cortex-M0+ runs (a few instructions, like [code]bl[/code], are 32 bits)."],
	]
	if Level.at_least(2):
		rows += [
			["Alignment", "A word must be at an address that is a multiple of 4, a halfword at a multiple of 2. See " + _link("memmap", "Memory and addresses") + "."],
			["BOOTSEL", "The button on the Pico. Hold it while plugging the board in and it appears as a drive (RPI-RP2) you can copy a program to."],
			["Calling convention", "The rules functions follow: arguments and result in R0–R3, R4–R7 kept, LR saved. See " + _link("stack", "The stack and functions") + "."],
			["Flash", "To copy a program into the Pico's flash memory, where it stays when the power is off. Also the name of that memory."],
			["Floating", "An input pin that nothing drives: it could read 0 or 1. A pull resistor prevents it."],
			["GPIO", "General-purpose input/output: a pin your program can drive high or low, or read. See " + _link("gpio", "GPIO: controlling pins") + "."],
			["Halfword", "2 bytes, 16 bits. Most Thumb instructions are one halfword."],
			["Leaf function", "A function that calls no other function, so it need not save LR."],
			["Literal pool", "Numbers the assembler stores after your code for [code]ldr rX, =value[/code]; listed as [code].word[/code] rows."],
			["Little-endian", "Storing a word's lowest byte at the lowest address: [code]0x0000300D[/code] is stored as [code]0D 30 00 00[/code]."],
			["Load / store", "Copy a value from memory into a register ([code]ldr[/code]) or from a register into memory ([code]str[/code])."],
			["Mask", "A number with a 1 in the bits you care about, such as [code]1 << 15[/code] for GP15."],
			["Memory-mapped I/O", "Controlling hardware by reading and writing special addresses with ordinary loads and stores."],
			["Pointer", "A register (or word) holding an address: [code][lb]r1][/code] means the memory R1 points at."],
			["Polling", "Reading an input over and over in a loop to see when it changes."],
			["Pull-up / pull-down", "A weak resistor inside the chip that holds an input at 1 (up) or 0 (down) while nothing else drives it."],
			["SIO", "Single-cycle I/O: the block at 0xD0000000 whose registers set, clear and read the GPIO pins in one clock cycle."],
			["Thumb bit", "Bit 0 of an address you jump to with BX, BLX or POP {PC}. It must be 1, meaning \"Thumb code\"; the instruction itself is fetched from the even address."],
			["UF2", "The file format the Pico loads programs from. Flash makes one from your [code].s[/code] file."],
			["Word", "4 bytes, 32 bits: what one register holds."],
		]
	if Level.at_least(3):
		rows += [
			["ALU", "Arithmetic Logic Unit: the part that adds, subtracts, ANDs and shifts. See " + _link("blocks", "Building blocks") + "."],
			["Bus", "Shared wires that carry one value at a time between the parts of the datapath."],
			["Clock edge", "The tick that ends a clock cycle: the moment registers take their new values."],
			["Combinational logic", "Parts with no memory (muxes, adders, the ALU): their output follows their inputs straight away."],
			["Control signal", "An on/off (or select) line from the control unit that tells one part what to do this cycle. See " + _link("signals", "Control signals") + "."],
			["Control unit (FSM)", "The finite-state machine that turns control signals on and off, one state per cycle."],
			["Datapath", "The parts that hold, move and compute data: registers, bus, ALU, adders, memory."],
			["Condition codes (LD.CC)", "Another name for the N Z C V flags. LD.CC is the control signal that loads them at the clock edge."],
			["Latch (ALU A / B)", "A register that holds an input steady for the next cycle. The ALU's A and B latches (LD.ALUA, LD.ALUB) keep its inputs fixed while it computes."],
			["IR / IR2", "Instruction registers: hold the instruction (and the second half of a 32-bit one) while it runs."],
			["MAR / MDR", "Memory address register and memory data register: the address memory uses, and the data read or to be written."],
			["Multiplexer (mux)", "Picks one of several inputs to pass on, chosen by a select signal."],
			["Phase", "One of the six parts of the instruction cycle: Fetch, Decode, Evaluate address, Fetch operands, Execute, Store result."],
			["Register file", "The block that holds R0–R12, SP and LR (the PC is a separate register), with ports to read two registers and write one."],
			["Sign-extend (SEXT)", "Widen a number by copying its top bit into the new bits, so a negative number stays negative: 8-bit 0xFE (−2) becomes 0xFFFFFFFE."],
			["Special registers", "Registers outside R0–R15 read with MRS and written with MSR: APSR (the flags), PRIMASK (turns interrupts off), CONTROL, and the two stack pointers MSP and PSP."],
			["Sequential logic", "Parts that remember (registers): they change only at the clock edge."],
			["State", "What the control unit is doing in one clock cycle, such as FETCH_ADDR. See " + _link("states", "FSM states") + "."],
			["Tri-state buffer", "A switch between a part and the bus: on, the part drives the bus; off, it is disconnected."],
			["Zero-extend (ZEXT)", "Widen a number by filling the new bits with 0: 8-bit 0xFE becomes 0x000000FE (254)."],
		]
	if Level.at_least(4):
		rows += [
			["AHB-Lite", "The RP2040's main bus between the processor and memory. A transfer takes an address cycle, then a data cycle."],
			["APB", "A slower bus behind AHB-Lite for simple peripherals; IO_BANK0 and PADS_BANK0 are on it."],
			["CPI", "Cycles per instruction: total clock cycles ÷ instructions. See " + _link("perfmath", "Performance formulas") + "."],
			["Clock frequency", "Clock cycles per second, in hertz. The RP2040 normally runs at 125 MHz."],
			["Flush", "Throwing away an instruction a pipeline fetched from the wrong path, after a taken branch."],
			["Hazard", "Something that stops a pipeline finishing one instruction per cycle, such as a taken branch."],
			["Latency", "How long one instruction (or job) takes from start to finish."],
			["Pipeline", "Splitting instructions into stages that work on different instructions at the same time. See " + _link("pipeintro", "Pipelining: the big idea") + "."],
			["Pipeline register", "The register between two stages (IF/EX) that hands an instruction on."],
			["Speed-up", "Old time ÷ new time: how many times faster a change makes a program."],
			["Stage", "One step of a pipeline, such as Fetch or Execute."],
			["Throughput", "How many instructions finish per cycle (or per second)."],
			["IOPORT", "The M0+'s private single-cycle port to the SIO block, so a GPIO access does not use AHB-Lite."],
			["XIP", "Execute in place: the RP2040 runs code straight from external flash, through a cache."],
			["Wait state", "An extra cycle a slow memory makes the processor wait."],
		]
	rows.sort_custom(func(a, b): return str(a[0]).to_lower() < str(b[0]).to_lower())
	t += _grid(["Word", "Meaning"], rows, 1)
	return t


## "New at Level N" reading lists for Start here, one per level reached.
func _reading_paths() -> String:
	var paths := [
		[2, [["memmap", "Memory and addresses", "bytes, words and where things are"],
			["loadstore", "Loads and stores", "moving numbers between memory and registers"],
			["stack", "The stack and functions", "calling your own functions safely"],
			["gpio", "GPIO: controlling pins", "lighting LEDs and reading buttons"],
			["examples2", "Level 2 examples", "step through them with a guide beside you"],
			["memory", "Disassembly & memory", "the Memory tab and the stack view"],
			["pico", "Raspberry Pi Pico board", "reading and clicking the pins"],
			["hardware", "Running on a real Pico", "Flash your program to a board"]]],
		[3, [["cpuintro", "The big picture", "clock cycles, the datapath and the control unit"],
			["blocks", "Building blocks", "registers, the bus, muxes and the ALU"],
			["diagram", "Datapath diagram", "how to read the CPU diagram"],
			["cycles", "Cycle by cycle", "the panel under the diagram"],
			["tutorial", "Tutorial: one ADDS", "an ADDS, all six cycles, with real values"],
			["kinds", "Other instructions", "the other kinds of instruction"],
			["signals", "Control signals", "what every signal name means"]]],
		[4, [["pipeintro", "Pipelining: the big idea", "why the real Pico is faster"],
			["pipeline", "Pipeline diagram", "how to read the pipeline view"],
			["pipecycles", "Pipeline cycle by cycle", "stepping, and where cycles are lost"],
			["perfmath", "Performance formulas", "cycles, CPI and time"],
			["performance", "Performance window", "measuring your own programs"]]],
	]
	var t := ""
	for p in paths:
		if not Level.at_least(p[0]):
			continue
		t += _sub("New at Level %d" % p[0])
		for i in p[1].size():
			var e: Array = p[1][i]
			t += "%d. %s — %s.\n" % [i + 1, _link(e[0], e[1]), e[2]]
	return t


# ── level 2 pages: memory and I/O ───────────────────────────────────────────

## "Memory and addresses".
func _memmap() -> String:
	var t := _h("Memory and addresses")
	t += "The CPU has only 16 registers. Everything else — your program's instructions, its data, the stack — lives in [b]memory[/b]. Memory is one long row of bytes, and each byte has a number, its [b]address[/b], like a house number on a very long street.\n"
	t += _sub("Bytes, halfwords and words")
	t += _grid(["Size", "Bytes", "Bits", "Holds (unsigned)", "Load / store with"], [
		["[b]byte[/b]", "1", "8", "0 to 255", _asm("ldrb") + " " + _asm("strb")],
		["[b]halfword[/b]", "2", "16", "0 to 65 535", _asm("ldrh") + " " + _asm("strh")],
		["[b]word[/b]", "4", "32", "0 to 4 294 967 295", _asm("ldr") + " " + _asm("str")],
	], 3)
	t += "A register holds exactly one word. Most Thumb instructions are one halfword (2 bytes) long, which is why the PC usually moves on by 2.\n"
	t += _sub("Little-endian: the low byte comes first")
	t += "A word takes 4 bytes in a row. The RP2040 stores the [i]lowest[/i] byte at the [i]lowest[/i] address; this is called [b]little-endian[/b]. In the [b]Call a function[/b] example, the word [code]0x0000300D[/code] pushed onto the stack at [code]0xFFF4[/code] sits in memory like this:\n"
	t += _grid(["Address", "FFF4", "FFF5", "FFF6", "FFF7"], [
		["Byte", "[code]0D[/code]", "[code]30[/code]", "[code]00[/code]", "[code]00[/code]"],
	])
	t += "So in the Memory tab's hex dump you read a word's bytes [i]backwards[/i]: [code]0D 30 00 00[/code] is the word [code]0x0000300D[/code]. The Stack view under the dump puts the bytes back together for you.\n"
	t += _sub("Alignment")
	t += "A word must start at an address that is a multiple of 4 (in hex, ending in 0, 4, 8 or C), and a halfword at a multiple of 2. The Cortex-M0+ cannot load a word from [code]0x3002[/code]: picosim stops with an [b]Unaligned access[/b] CPU fault there, as a real Pico would. Bytes can be anywhere. Put [code].align 2[/code] before [code].word[/code] data that comes after bytes or strings.\n"
	t += _sub("picosim's memory map")
	t += _grid(["Addresses", "What is there"], [
		["[code]0x0000[/code] – [code]0x2FFF[/code]", "The built-in OS: [code]_start[/code], [code]putchar[/code], [code]getchar[/code]."],
		["[code]0x3000[/code] upward", "Your program: first its instructions (and their literal pools), then its [code].data[/code], in the order they are in your file."],
		["… up to [code]0xFFFF[/code]", "Free RAM. The stack starts at the top and grows [i]down[/i] towards your program."],
		["[code]0x00010000[/code]", "SP's starting value: just past the end of RAM. The first [code]push[/code] stores below it."],
		["[code]0x4000C000[/code]", "RESETS (see " + _link("gpio", "GPIO") + ")."],
		["[code]0x40014000[/code]", "IO_BANK0: which part of the chip controls each pin."],
		["[code]0x4001C000[/code]", "PADS_BANK0: each pin's pull-up and pull-down resistors."],
		["[code]0xD0000000[/code]", "SIO: set, clear and read the pins."],
		["anything else", "Nothing: reading or writing it stops the program with a [b]bus fault[/b]."],
	], 1)
	t += _note("Real chip", "RAM here is 64 KB ([code]0x10000[/code] bytes) and starts at 0. The real RP2040 has 264 KB of RAM at [code]0x20000000[/code] and runs your program from flash at [code]0x10000000[/code]. So never type a fixed RAM address into a program: use labels, and the same file works in both places. The I/O addresses are the real chip's.")
	t += _sub("Seeing it")
	t += "Open the " + _link("memory", "Memory tab") + " next to Disassembly, type a label such as [code]numbers[/code] (or an address such as [code]0x3000[/code]) in [b]Go to[/b] and press " + _k("Enter") + ". Its map shows the table above as a picture, with PC and SP marked; bytes the last step read are outlined in [color=%s]blue[/color] and bytes it wrote in [color=%s]yellow[/color].\n" % [_c("accent"), _c("changed")]
	return t


## "Loads and stores".
func _loadstore() -> String:
	var t := _h("Loads and stores")
	t += "The ALU only works on registers. To use a number that is in memory, the program must [b]load[/b] it into a register, work on it there, and [b]store[/b] the result back. ARM processors are built this way, which is why they are called [i]load/store[/i] machines.\n"
	t += _sub("The forms")
	t += _grid(["Form", "Example", "What it does"], [
		[_asm("ldr Rd, [Rn]"), _asm("ldr r0, [r1]"), "R0 = the word in memory at the address in R1."],
		[_asm("ldr Rd, [Rn, #imm]"), _asm("ldr r0, [r1, #4]"), "R0 = the word at R1 + 4. imm is 0 to 124, a multiple of 4."],
		[_asm("ldr Rd, [Rn, Rm]"), _asm("ldr r0, [r1, r2]"), "R0 = the word at R1 + R2."],
		[_asm("str Rd, [Rn, #imm]"), _asm("str r2, [r1]"), "The word at R1 (+ imm) = R2. The same three forms as ldr."],
		[_asm("ldrb") + " / " + _asm("strb"), _asm("ldrb r0, [r4]"), "One byte. ldrb fills the other 24 bits of the register with 0. imm is 0 to 31."],
		[_asm("ldrh") + " / " + _asm("strh"), _asm("ldrh r0, [r1, #2]"), "One halfword. imm is 0 to 62, even."],
		[_asm("ldr Rd, [sp, #imm]"), _asm("ldr r0, [sp, #4]"), "A word on the stack. imm is 0 to 1020, a multiple of 4."],
		[_asm("ldr Rd, =value"), _asm("ldr r1, =numbers"), "R1 = any 32-bit number, or the address of a label (below)."],
	], 2)
	t += _note("Brackets", "Square brackets mean [i]the memory at this address[/i]. [code]r1[/code] is the number in R1; [code][lb]r1][/code] is whatever is stored at the address R1 holds. The register in brackets is a [b]pointer[/b]: it points at the data.")
	t += "As with arithmetic, use R0–R7: [code]ldr r8, [lb]r1][/code] gives [i]cannot honor width suffix[/i]. An offset that is too big, not a multiple of the size, or negative gives [i]invalid offset, value too big[/i].\n"
	t += _sub("Getting an address: ldr Rd, =label")
	t += "[code]movs[/code] can only make 0 to 255, but addresses such as [code]0x3024[/code] and I/O addresses such as [code]0xD0000024[/code] are much bigger. [code]ldr r1, =numbers[/code] asks the assembler to store the number in memory just after your code, in a [b]literal pool[/b], and to load it from there:\n"
	t += "• In the Disassembly it shows as [code]ldr r1, [lb]pc, #0x18][/code]: \"the word 0x18 bytes on from here\".\n"
	t += "• After your last instruction you will see rows such as [code].word 0x00003024[/code]. Those are the pool: data, not instructions. The CPU never runs them, because the program's last instruction (a [code]pop[/code] or [code]b[/code]) jumps away first.\n"
	t += "A pool must be within about 1 KB [i]after[/i] the ldr that uses it. In a long program the assembler says [i]invalid offset, value too big[/i] on the ldr line; put [code].ltorg[/code] on a line the CPU never reaches (just after a [code]b[/code] or [code]pop {…, pc}[/code]) and the assembler places a pool there.\n"
	t += _sub("Putting data in your program")
	t += _grid(["Directive", "Example", "What it makes"], [
		[_asm(".data"), _asm(".data"), "Everything after it is data, placed after your code. These lines are not listed in the Disassembly: look at them in the Memory tab."],
		[_asm(".word"), _asm("numbers: .word 10, 20, 30, 40"), "Words (4 bytes each). The label is the address of the first one."],
		[_asm(".hword"), _asm(".hword 500"), "Halfwords (2 bytes)."],
		[_asm(".byte"), _asm(".byte 1, 2, 3"), "Single bytes."],
		[_asm(".asciz"), _asm("msg: .asciz \"Hi\\n\""), "The string's characters, then a 0 byte that marks its end."],
		[_asm(".ascii"), _asm(".ascii \"Hi\""), "The characters only, with no 0 at the end."],
		[_asm(".align 2"), _asm(".align 2"), "Skip ahead to the next multiple of 4, so a .word after bytes or strings is aligned."],
		[_asm(".equ"), _asm(".equ COUNT, 4"), "A name for a number. It takes no memory: the assembler swaps the name for the number everywhere."],
	], 2)
	t += "Data you put after your code [i]without[/i] [code].data[/code] (as the Buttons and LEDs example does with its messages) is listed in the Disassembly as [code].word[/code] and [code].ascii[/code] rows.\n"
	t += _sub("Example: adding up a list")
	t += "From the [b]Add up a list in memory[/b] example:\n"
	t += _codeblock("        ldr     r1, =numbers    @ R1 = the address of the first number\n        movs    r2, #0          @ R2 = running total\n        movs    r3, #4          @ R3 = how many numbers are left\nloop:\n        ldr     r0, [r1]        @ R0 = the number at address R1\n        adds    r2, r2, r0      @ add it to the total\n        adds    r1, #4          @ the next word is 4 bytes further on\n        subs    r3, #1          @ one fewer left (sets Z when it reaches 0)\n        bne     loop\n\n        ldr     r1, =total      @ R1 = the address of \"total\"\n        str     r2, [r1]        @ store the total in memory")
	t += "What each pass loads and adds up:\n"
	t += _grid(["Pass", "R1 (pointer)", "R0 = [lb]r1]", "R2 (total)", "R3 (left)"], [
		["1", "[code]0x3024[/code]", "10 ([code]0x0A[/code])", "10 ([code]0x0A[/code])", "3"],
		["2", "[code]0x3028[/code]", "20 ([code]0x14[/code])", "30 ([code]0x1E[/code])", "2"],
		["3", "[code]0x302C[/code]", "30 ([code]0x1E[/code])", "60 ([code]0x3C[/code])", "1"],
		["4", "[code]0x3030[/code]", "40 ([code]0x28[/code])", "100 ([code]0x64[/code])", "0, so Z = 1 and the loop ends"],
	])
	t += "R1 is shown at the moment of the load; [code]adds r1, #4[/code] then moves it on by one word. Finally [code]ldr r1, =total[/code] makes R1 = [code]0x3034[/code] and [code]str r2, [lb]r1][/code] writes the bytes [code]64 00 00 00[/code] there.\n"
	t += _sub("Arrays: element number n")
	t += "Element [i]n[/i] of a word array (counting from 0) is at [i]start + 4 × n[/i]. Multiplying by 4 is a shift left by 2:\n"
	t += _codeblock("        ldr     r1, =numbers    @ R1 = the address of element 0\n        movs    r3, #2          @ n = 2\n        lsls    r2, r3, #2      @ R2 = n × 4 = 8\n        ldr     r0, [r1, r2]    @ R0 = element 2 = 30")
	t += "For a byte array the step is 1, so no shift is needed: [code]ldrb r0, [lb]r1, r3][/code].\n"
	t += _sub("Strings")
	t += "A string is a row of character bytes that ends with a 0 byte ([code].asciz[/code] adds it). To print one, load each byte with [code]ldrb[/code] until you reach the 0:\n"
	t += _codeblock("print:                          @ prints the string whose address is in R0\n        push    {r4, lr}\n        movs    r4, r0          @ R4 = the address of the next character\n1:      ldrb    r0, [r4]        @ R0 = that character\n        cmp     r0, #0\n        beq     2f              @ the 0 byte: the end of the string\n        bl      putchar\n        adds    r4, #1          @ the next character is 1 byte on\n        b       1b\n2:      pop     {r4, pc}")
	t += "[code]1:[/code] and [code]2:[/code] are [b]local labels[/b]: [code]1b[/code] means \"the nearest 1: [b]b[/b]ack\" and [code]2f[/code] \"the nearest 2: [b]f[/b]orward\", so you can reuse the same numbers in every function. Call it with [code]ldr r0, =msg[/code] and [code]bl print[/code].\n"
	return t


## "The stack and functions".
func _stack() -> String:
	var t := _h("The stack and functions")
	t += "A [b]function[/b] is a piece of code you can call from anywhere and that comes back when it is done. [code]bl name[/code] calls it: it puts the return address (the line after the [code]bl[/code], plus 1) in [b]LR[/b] and jumps. The function ends with [code]bx lr[/code], which jumps back to the address in LR.\n"
	t += _sub("The problem: there is only one LR")
	t += "If a function calls another function — even [code]putchar[/code] — that second [code]bl[/code] overwrites LR, and the first function no longer knows where to return. So a function that calls anything must save LR first, and the place to save it is the [b]stack[/b].\n"
	t += _note("If you forget", "A function that calls putchar and then ends with [code]bx lr[/code] without saving LR jumps back into itself, to its own [code]bx lr[/code], forever. The program never halts. In picosim you see ▶ stuck on that one row while Run keeps going.", "changed")
	t += _sub("The stack")
	t += "The stack is the top part of RAM, used like a pile of plates: the last thing put on is the first taken off. [b]SP[/b] (the stack pointer) holds the address of the item on top. The stack grows [i]down[/i]: SP starts at [code]0x00010000[/code], just past the end of RAM.\n"
	t += "• [code]push {r4, lr}[/code] moves SP down by 4 bytes per register (8 here), then stores the registers there, the lowest-numbered register at the lowest address.\n"
	t += "• [code]pop {r4, pc}[/code] loads them back in the same order and moves SP up again. Popping into PC is a jump: it returns, using the LR value that was pushed.\n"
	t += _sub("Watch it: the Call a function example")
	t += "These are the real values from stepping [b]Call a function[/b] with " + _k("Step instruction") + " (the step numbers count from the first OS instruction):\n"
	t += _grid(["Step", "Instruction", "SP after", "LR after", "What happens"], [
		["4", _asm("push {r7, lr}"), "[code]0xFFF8[/code]", "[code]0x00000007[/code]", "[code]main[/code] saves R7 and its own return address (into the OS)."],
		["6", _asm("bl twice"), "[code]0xFFF8[/code]", "[code]0x00003009[/code]", "Call: LR = the address of the next line ([code]3008[/code]) + 1."],
		["8", _asm("bx lr"), "[code]0xFFF8[/code]", "[code]0x00003009[/code]", "[code]twice[/code] returns. It calls nothing, so it never touched the stack."],
		["9", _asm("bl print_digit"), "[code]0xFFF8[/code]", "[code]0x0000300D[/code]", "Call the second function."],
		["10", _asm("push {lr}"), "[code]0xFFF4[/code]", "[code]0x0000300D[/code]", "[code]print_digit[/code] saves its return address, because it is about to call putchar."],
		["12", _asm("bl putchar"), "[code]0xFFF4[/code]", "[code]0x0000302D[/code]", "LR is overwritten, but the saved copy is safe on the stack."],
		["15", _asm("pop {pc}"), "[code]0xFFF8[/code]", "[code]0x0000302D[/code]", "PC = the saved [code]0x300D[/code] with bit 0 cleared, [code]0x300C[/code]: back in main."],
	], 4)
	t += "The stack at step 12 (Memory tab → Stack, or Go to " + _k("SP") + "):\n"
	t += _grid(["Address", "Holds", "Put there by"], [
		["[code]0xFFF4[/code]  ← SP", "[code]0x0000300D[/code]", "print_digit: its return address (into main)"],
		["[code]0xFFF8[/code]", "[code]0x00000000[/code]", "main: R7"],
		["[code]0xFFFC[/code]", "[code]0x00000007[/code]", "main: its return address (into the OS)"],
		["[code]0x10000[/code]", "", "the end of RAM: where SP started"],
	], 2)
	t += _sub("The rules every function follows")
	t += "So that functions written by different people work together, ARM sets rules for the registers (the [b]calling convention[/b]). Simplified:\n"
	t += _grid(["Registers", "Rule"], [
		["[b]R0–R3[/b]", "Arguments go in here, the first in R0. The result comes back in R0. A function may change all four, so do not expect them to survive a [code]bl[/code]."],
		["[b]R4–R7[/b] (and R8–R11)", "A function that uses them must leave them as it found them: push them at the start, pop them at the end."],
		["[b]LR[/b]", "Changed by every [code]bl[/code]. Push it if your function calls another function."],
		["[b]SP[/b]", "Must be back where it started when the function returns: every push needs a matching pop."],
	], 1)
	t += "putchar and getchar follow these rules, which is why the examples keep their counters in R4. The rules also ask that the stack moves in steps of 8 bytes whenever a library function such as putchar is called on the real Pico; that is why [code]main[/code] pushes a [i]pair[/i] of registers. A small function of your own (like print_digit) can push just [code]{lr}[/code].\n"
	t += _sub("A template")
	t += _codeblock("my_function:                    @ takes a number in R0, returns one in R0\n        push    {r4, lr}        @ save R4 (we use it) and the return address\n        @ ... the work: R4 is ours to use here ...\n        pop     {r4, pc}        @ put R4 back and return")
	t += "A function that calls nothing and uses only R0–R3 (a [b]leaf[/b] function, like [code]twice[/code]) needs no push at all: it ends with [code]bx lr[/code].\n"
	t += _sub("Common mistakes")
	t += "• [b]Forgetting push {lr}[/b] in a function that calls another: it loops forever on its own [code]bx lr[/code].\n"
	t += "• [b]Push and pop that do not match[/b]: the wrong value lands in PC. A [code]pop {r7, pc}[/code] with no push before it reads past the top of RAM and stops with a bus fault.\n"
	t += "• [b]Pushing inside a loop[/b] without popping: SP keeps going down until the stack runs over your data and code.\n"
	return t


## "GPIO: controlling pins".
func _gpio() -> String:
	var t := _h("GPIO: controlling pins")
	t += "The RP2040 has 30 [b]GPIO[/b] pins (general-purpose input/output), GP0 to GP29. Your program can use each one as:\n"
	t += "• an [b]output[/b]: the program makes it [b]high[/b] (3.3 V, a 1) or [b]low[/b] (0 V, a 0) — enough to light an LED through a resistor;\n"
	t += "• an [b]input[/b]: the program reads whether something outside, such as a button, holds it high or low.\n"
	t += _sub("Memory-mapped I/O")
	t += "There are no special instructions for pins. The chip's control registers sit at memory addresses, and you use them with ordinary [code]str[/code] and [code]ldr[/code]. [code]str r4, [lb]r5][/code] with R5 = [code]0xD000001C[/code] does not change memory at all: it flips a pin. This is called [b]memory-mapped I/O[/b].\n"
	t += _sub("One bit per pin")
	t += "Each of these registers has one bit for each pin: bit [i]n[/i] is GP[i]n[/i]. A number with just that one bit set is the pin's [b]mask[/b], [code]1 << n[/code] (1 shifted left n places):\n"
	t += _grid(["Pin", "Mask (hex)", "Mask (binary, low 16 bits)"], [
		["GP0", "[code]0x00000001[/code]", "[code]0000 0000 0000 0001[/code]"],
		["GP2", "[code]0x00000004[/code]", "[code]0000 0000 0000 0100[/code]"],
		["GP15", "[code]0x00008000[/code]", "[code]1000 0000 0000 0000[/code]"],
	])
	t += "In a program write [code]ldr r4, =1 << 15[/code], or give it a name with [code].equ LED_MASK, 1 << 15[/code]. Several pins at once: [code](1 << 14) | (1 << 15)[/code].\n"
	t += _sub("Three steps to drive a pin")
	t += "1. [b]Connect the pin to your program.[/b] Write 5 (function [i]SIO[/i]) to the pin's control register in IO_BANK0, at [code]0x40014004 + 8 × n[/code] (for GP15: [code]0x4001407C[/code]). Every pin starts with function [code]0x1F[/code], connected to nothing, so without this step nothing happens.\n"
	t += "2. [b]Make it an output.[/b] Write its mask to [code]GPIO_OE_SET[/code] ([code]0xD0000024[/code]).\n"
	t += "3. [b]Set its level.[/b] Write the mask to [code]GPIO_OUT_SET[/code] ([code]0xD0000014[/code]) for high, [code]GPIO_OUT_CLR[/code] ([code]0xD0000018[/code]) for low, or [code]GPIO_OUT_XOR[/code] ([code]0xD000001C[/code]) to flip it.\n"
	t += "The [b]Blink an LED[/b] example does exactly this. Stepping it in picosim shows:\n"
	t += _grid(["Instruction", "The board's \"Last I/O write\"", "GP15 afterwards"], [
		[_asm("str r1, [r0]") + "  (R0 = [code]0x4001407C[/code], R1 = 5)", "[code]IO_BANK0 GPIO15_CTRL ← 0x00000005[/code]", "input, floating: connected, but nothing drives it yet"],
		[_asm("str r4, [r0]") + "  (R0 = [code]0xD0000024[/code], R4 = [code]0x8000[/code])", "[code]SIO GPIO_OE_SET ← 0x00008000[/code]", "output, 0"],
		[_asm("str r4, [r5]") + "  (R5 = [code]0xD000001C[/code])", "[code]SIO GPIO_OUT_XOR ← 0x00008000[/code]", "output, [color=%s][b]1[/b][/color] (green on the board)" % _c("green")],
	], 2)
	t += "[b]Why SET, CLR and XOR?[/b] Writing to [code]GPIO_OUT[/code] itself sets [i]every[/i] pin at once. Writing a mask to [code]GPIO_OUT_SET[/code] changes only the pins whose bits are 1 and leaves all the others alone, so you do not have to read the register first.\n"
	t += _sub("The registers")
	t += _grid(["Address", "Name", "What a write (or read) does"], [
		["[code]0xD0000004[/code]", "GPIO_IN", "[b]Read[/b] it: one bit per pin, 1 = that pin is high now."],
		["[code]0xD0000010[/code]", "GPIO_OUT", "Every pin's output level at once."],
		["[code]0xD0000014[/code]", "GPIO_OUT_SET", "Pins whose bit is 1 go high."],
		["[code]0xD0000018[/code]", "GPIO_OUT_CLR", "Pins whose bit is 1 go low."],
		["[code]0xD000001C[/code]", "GPIO_OUT_XOR", "Pins whose bit is 1 flip."],
		["[code]0xD0000020[/code]", "GPIO_OE", "Every pin's direction at once (1 = output)."],
		["[code]0xD0000024[/code]", "GPIO_OE_SET", "Pins whose bit is 1 become outputs."],
		["[code]0xD0000028[/code]", "GPIO_OE_CLR", "Pins whose bit is 1 become inputs."],
		["[code]0xD000002C[/code]", "GPIO_OE_XOR", "Pins whose bit is 1 change direction."],
		["[code]0x40014004 + 8n[/code]", "GPIOn_CTRL", "Bits 0–4 choose what controls pin n: 5 = SIO (your program). Starts at [code]0x1F[/code] (nothing)."],
		["[code]0x4001C004 + 4n[/code]", "PADS GPIOn", "Bit 3 turns on the pull-up resistor, bit 2 the pull-down. On the real chip pins start at [code]0x56[/code] (pull-down on; picosim starts them with no pull at all). [code]0x5A[/code] is the same setting with the pull-up on instead."],
		["[code]0x4000C000[/code]", "RESETS", "Both picosim and the Pico SDK start with the pins' blocks already running, so you do not need it."],
	], 2)
	t += _sub("Reading an input")
	t += "Read [code]GPIO_IN[/code] and keep only your pin's bit with [code]ands[/code]; Z tells you whether it was 0. From the [b]Buttons and LEDs[/b] example, with the button on GP14:\n"
	t += _codeblock("        ldr     r6, =0xD0000004 @ GPIO_IN\n        ldr     r5, =1 << 14    @ the button's bit\ncheck:\n        ldr     r0, [r6]        @ R0 = the level of every pin\n        ands    r0, r5          @ keep only the button's bit (Z = 1 if it is 0)\n        beq     pressed         @ 0: the button connects the pin to GND\n        @ ... not pressed: turn the LED off ...\n        b       check")
	t += "Checking an input over and over like this is called [b]polling[/b].\n\n"
	t += "An input with nothing connected is [b]floating[/b]: on a real chip it can read 0 or 1 at random. A [b]pull-up[/b] resistor (PADS bit 3) gently holds it at 1 until a button connects it to GND; a [b]pull-down[/b] (bit 2) holds it at 0 until a button connects it to 3V3. In picosim a floating pin reads 0.\n"
	t += _sub("GP numbers and header pins")
	t += "GP numbers are the chip's. The board's 40 header pins are numbered separately, 1 to 40 around its edge: GP0 is header pin 1, GP14 is pin 19 and GP15 is pin 20. Hover a pin on the " + _link("pico", "Pico board") + " to see both numbers. The GND pins are 3, 8, 13, 18, 23, 28 and 38, and 3.3 V comes out of pin 36. GP25 is not on the header: on a Raspberry Pi Pico it drives the on-board LED.\n"
	t += _note("Wiring safely", "Always put a resistor (220–1000 Ω) in series with an LED. The pins work at 3.3 V: never connect one to VBUS or VSYS (5 V), and never connect an output straight to GND or 3V3. Use GP0–GP22 and GP26–GP28; 23, 24, 25 and 29 are used on the board itself on some boards (such as the Pico W).", "changed")
	return t


## "The Level 2 examples": each example with what to watch and something to try.
func _examples2() -> String:
	var t := _h("Level 2 examples")
	t += "Open each one from " + _k("Examples") + ". Every file starts with its own [i]What to watch[/i] comment. The values below are what picosim really shows.\n"
	var dim := _c("dim")
	var head := func(n: String, f: String, extra: String) -> String:
		return "\n[font_size=%d][b]%s[/b][/font_size]   [color=%s]%s%s[/color]\n" % [roundi(21 * text_scale), n, dim, f, extra]
	t += head.call("4 · Add up a list in memory", "04_memory.s", " · 32 instructions")
	t += "Loads four numbers with [code]ldr[/code], adds them up, and stores the total with [code]str[/code].\n"
	t += "• Open the Memory tab and Go to [code]numbers[/code]. The dump starts at the row [code]3020[/code]; its first 4 bytes are the end of the literal pool (the address of [code]total[/code]), and [code]numbers[/code] starts at [code]3024[/code]: [code]0A 00 00 00  14 00 00 00  1E 00 00 00  28 00 00 00[/code] are 10, 20, 30 and 40 as little-endian words. [code]total[/code] follows at [code]3034[/code], still 0.\n"
	t += "• Watch R1 step through [code]0x3024[/code], [code]0x3028[/code], [code]0x302C[/code], [code]0x3030[/code] and R2 grow to [code]0x64[/code] (100). " + _link("loadstore", "Loads and stores") + " has the table.\n"
	t += "• At the [code]str[/code], the bytes at [code]3034[/code] turn yellow: [code]64 00 00 00[/code].\n"
	t += "[b]Try it:[/b] add a fifth number ([code].word 10, 20, 30, 40, 50[/code] and [code]movs r3, #5[/code]). The total becomes 150, [code]0x96[/code].\n"
	t += head.call("5 · Blink an LED", "05_blink_led.s", " · runs until you pause it")
	t += "Flips GP15 on and off forever. " + _link("gpio", "GPIO: controlling pins") + " explains every line.\n"
	t += "• Step the first few instructions and watch the caption under the Pico board: the CTRL write, then OE_SET, then OUT_XOR, and GP15 turns green.\n"
	t += "• Between flips, [code]subs r0, #1[/code] / [code]bne wait[/code] counts down from 20 to waste time. On a real board the program counts from 10 000 000 instead (about a quarter of a second), because the Pico build defines [code]PICOSIM_HARDWARE[/code].\n"
	t += "• Set the speed to [b]Max[/b] and press Run to see it blink; Pause to stop.\n"
	t += "[b]Try it:[/b] click " + _k("⚙ Settings") + ", change LED_PIN to 2, " + _k("Apply and reload") + ", and watch GP2 blink instead.\n"
	t += head.call("6 · Echo what you type", "06_echo.s", " · runs until you type q")
	t += "Reads keys with [code]getchar[/code] and prints each one back.\n"
	t += "• Press Run, click in the serial console and type. While the program waits, the status text says [i]waiting for input[/i].\n"
	t += "• Each key arrives in R0 as its ASCII code; [code]cmp r0, #'q'[/code] / [code]beq done[/code] ends the program when you type q.\n"
	t += "[b]Try it:[/b] make it shout. Small letters are [code]0x61[/code]–[code]0x7A[/code] and capitals are 32 lower, so add before [code]bl putchar[/code]:\n"
	t += _codeblock("        cmp     r0, #'a'\n        blo     show            @ below 'a': not a small letter\n        cmp     r0, #'z'\n        bhi     show            @ above 'z': not a small letter\n        subs    r0, #32         @ 'a' (0x61) - 32 = 'A' (0x41)\nshow:")
	t += "Typing [code]Hi, abz![/code] then prints [code]HI, ABZ![/code].\n"
	t += head.call("7 · Call a function", "07_function.s", " · 33 instructions")
	t += "Calls two small functions and prints 4 and 6. " + _link("stack", "The stack and functions") + " follows it step by step: LR, SP and the stack.\n"
	t += "• [code]twice[/code] calls nothing, so it returns with [code]bx lr[/code] and never uses the stack.\n"
	t += "• [code]print_digit[/code] calls putchar, so it saves LR with [code]push {lr}[/code] and returns with [code]pop {pc}[/code].\n"
	t += "[b]Try it:[/b] remove [code]push {lr}[/code] and change [code]pop {pc}[/code] to [code]bx lr[/code]. After printing 4 the program is stuck on that [code]bx lr[/code] forever: see why in the stack page.\n"
	t += head.call("8 · Buttons and LEDs", "08_button_led.s", " · runs until you pause it")
	t += "Two jobs, chosen with " + _k("⚙ Settings") + " → FIND_PINS: [i]find my pins[/i] (which pins your real LEDs and buttons are on; this is what it does at first) or [i]run it[/i] (the LED is on while you hold the button).\n"
	t += "• [b]Run it[/b], in the simulator: in " + _k("⚙ Settings") + " set FIND_PINS to [i]run it[/i] and click " + _k("Apply and reload") + ", then press Run. GP14 (the button, with its pull-up on) shows a blue [b]1[/b]. Click GP14 twice: it is driven to 1 and then to 0, and at 0 (\"pressed\": the button connects the pin to GND) GP15 turns green. Click once more to let go.\n"
	t += "• [b]Find my pins[/b] is meant for a real board (see " + _link("hardware", "Running on a real Pico") + "): it switches each free pin high and low and prints its number, then reports every button you press. In the simulator, type a key in the console each time it asks (twice), let part 1 run, then click pins in part 2 to see them reported.\n"
	t += "• The program is longer than the others: look for [code]ands[/code], [code]orrs[/code], [code]eors[/code], [code]tst[/code] (an AND that only sets flags) and [code]mvns[/code] (NOT), and for functions such as [code]print[/code] and [code]print_num[/code].\n"
	return t


# ── level 3 pages: inside the CPU ───────────────────────────────────────────
# The cycle values quoted come from the walkthrough of the examples.

## "The big picture": clock, datapath, control, phases.
func _cpu_intro() -> String:
	var t := _h("Inside the CPU: the big picture")
	t += "Until now, " + _k("Step instruction") + " did a whole instruction at once. Inside the processor, every instruction is really a short series of smaller steps, one per tick of the processor's clock. Level 3 lets you watch those steps on a drawing of the hardware.\n"
	t += _sub("The clock")
	t += "The processor is driven by a [b]clock[/b]: a signal that ticks at a steady rate (the Pico's RP2040 normally ticks 125 million times a second). The time from one tick to the next is one [b]clock cycle[/b]. During a cycle, values flow through wires and are worked on; at the tick that ends it — the [b]clock edge[/b] — storage boxes called [b]registers[/b] capture their new values all at once. Then the next cycle begins.\n"
	t += _sub("Datapath and control")
	t += "A processor has two halves:\n"
	t += "• The [b]datapath[/b] holds, moves and computes data: registers, a shared [b]bus[/b] (wires that carry one value at a time), the [b]ALU[/b] that does arithmetic, adders for addresses, and the connection to memory.\n"
	t += "• The [b]control unit[/b] decides, every cycle, what each part of the datapath does, by turning [b]control signals[/b] on and off: \"put the PC on the bus\", \"MAR, load the bus\", \"ALU, add\". In this processor it is a [b]finite-state machine[/b] (FSM): in each cycle it is in one [b]state[/b], and each state turns on its own set of signals.\n"
	t += _link("blocks", "Building blocks") + " describes each kind of part.\n"
	t += _sub("Why several cycles per instruction?")
	t += "This teaching processor has [b]one[/b] bus and [b]one[/b] memory. Only one value can travel on the bus in a cycle, and memory does one read or write per cycle. So an instruction has to be done in steps. Just reading the instruction from memory (the [i]fetch[/i]) takes 3 cycles: send the address, read memory, copy the result into the instruction register. Most instructions take 5 to 7 cycles in all.\n"
	t += _sub("The six phases")
	t += "Every instruction goes through some of these phases, in this order. The " + _link("cycles", "cycle inspector") + "'s chips light up for the phase of the cycle you are looking at.\n"
	if ResourceLoader.exists(STATES_PATH):
		var info = load(STATES_PATH)
		var rows := []
		for ph in info.PHASES:
			rows.append(["[b]%s[/b]" % str(ph[0]).capitalize(), _k(ph[1]), ph[2], "[color=%s]%s[/color]" % [_c("dim"), ", ".join(ph[3])]])
		t += _grid(["Phase", "Chip", "What happens", "FSM states"], rows, 2)
	t += "An ADDS, for example, uses Fetch (3 cycles), Decode, Fetch operands and Execute: 6 cycles. " + _link("tutorial", "Tutorial: one ADDS") + " goes through all six with real values.\n"
	t += _sub("Getting started")
	t += "1. Load a program, for example [b]Add two numbers[/b].\n"
	t += "2. Click " + _k("Show CPU Diagram") + " in the toolbar" + (" (with the menu next to it on " + _k("Multi-cycle") + ")" if Level.at_least(4) else "") + ". The diagram appears on the right with the cycle inspector under it.\n"
	t += "3. Press " + _k("Step cycle") + " (" + _k("F11") + "). One clock cycle runs: the parts it used light up, and the inspector explains it.\n"
	t += "4. Keep pressing F11. After three cycles of fetch and a few more, the instruction is done and the next one starts with FETCH_ADDR.\n"
	t += _k("Step instruction") + " still works: it runs the rest of the current instruction's cycles at once. The status text now counts both instructions and clock cycles.\n"
	t += _note("Model", "This datapath is the course's teaching processor (textbook Chapter 7), not a picture of the real Cortex-M0+'s insides. It runs exactly the same programs with exactly the same results; only the way it is built and its cycle counts differ. Level 4 shows the real chip's own design, a pipeline.")
	return t


## "Building blocks": the kinds of part on the diagram.
func _blocks() -> String:
	var t := _h("Building blocks")
	t += "The CPU diagram is made of a few kinds of part, used again and again. Learn these and you can read the whole drawing. Click any block on the diagram for a card about that particular one, or see " + _link("components", "Datapath components") + ".\n"
	t += _grid(["Part", "What it does", "On the diagram", "Examples"], [
		["[b]Register[/b]", "Stores one number. It takes a new value only at the clock edge, and only when its load signal ([code]LD.X[/code]) is on; otherwise it keeps what it has.", "A box showing its value.", "PC, IR, MAR, MDR, the register file (R0–R12, SP, LR), the N Z C V flags"],
		["[b]Bus[/b]", "Shared wires that carry one 32-bit value from one part to any others that want it.", "The long line across the middle; its value is printed above it.", "the bus"],
		["[b]Tri-state buffer[/b]", "A switch between a part and the bus. When its [code]Gate[/code] signal is on, the part drives the bus; when off, the part is disconnected (\"high impedance\"). Only one may be on in a cycle.", "A small triangle on the bus; the lit one is driving.", "gatePC, GateMDR, GateALU, GateADDR, gateVector"],
		["[b]Multiplexer[/b] (mux)", "Picks one of its inputs and passes it through. Its [b]select[/b] signal says which.", "A trapezoid: inputs on the long side, output from the short side, select on a slanted side.", "PCMUX, ADDR1MUX, ADDR2MUX, SR2MUX"],
		["[b]Adder[/b]", "Adds two numbers, all the time.", "A trapezoid.", "the +2 PC incrementer, the address adder, the +4 MAR incrementer"],
		["[b]ALU[/b]", "Arithmetic Logic Unit: adds, subtracts, ANDs, shifts…, picked by the [code]ALUK[/code] signal. It also works out the N Z C V flags of its result.", "A trapezoid labelled ALU. It also shows the values held in its two input latches, A and B.", "the ALU"],
		["[b]Memory[/b]", "Reads or writes the byte, halfword or word at the address in MAR when [code]MEM.EN[/code] is on; [code]R/W[/code] says which.", "The box labelled MEMORY / I-O.", "RAM and the I/O registers"],
		["[b]Control unit[/b]", "The FSM. Each cycle, its current state turns on a set of control signals and chooses the next state.", "The FSM box; its control signals are the purple dashed stubs.", "the FSM"],
	], 1)
	t += _sub("Two kinds of logic")
	t += "• [b]Combinational[/b] parts (muxes, adders, the ALU) have no memory: their output follows their inputs straight away, within the cycle.\n"
	t += "• [b]Sequential[/b] parts (registers, and memory being written) change only at the clock edge.\n"
	t += "So during a cycle, data flows from registers, through combinational parts, to the inputs of other registers, and at the clock edge those registers capture it. That is why the diagram writes a register's new value as a yellow [code]old → new[/code] caption: the box itself changes only when the next cycle starts.\n"
	t += _sub("Why a bus with switches?")
	t += "Connecting every part to every other part would need a huge number of wires. With one shared bus, any part can send a value to any other, but only one at a time. If two tri-state buffers were on together, two parts would try to drive the same wires to different values and fight; the FSM never lets that happen.\n"
	return t


## "Tutorial: one ADDS": ADDS R2, R0, R1 from 02_add.s, cycle by cycle.
func _tutorial() -> String:
	var t := _h("Tutorial: one instruction, cycle by cycle")
	t += "Follow one ADDS through the CPU, with the real values picosim shows. It is the instruction [code]adds r2, r0, r1[/code] at address [code]3006[/code] in the [b]Add two numbers[/b] example, where R0 = 3 and R1 = 4.\n"
	t += _sub("Set it up")
	t += "1. Open [b]Add two numbers[/b] from " + _k("Examples") + " and click " + _k("Show CPU Diagram") + ".\n"
	t += "2. In the [b]Walkthrough[/b] beside the diagram, click the narrow first column of the step [code]3006  adds r2, r0, r1[/code] to set a breakpoint (a red ●).\n"
	t += "3. Press " + _k("Run") + ". It stops just before that instruction. (Clicking the step and pressing " + _k("Go here") + " gets you to the same place.)\n"
	t += "4. Press " + _k("Step cycle") + " (" + _k("F11") + ") once for each row of the table, and compare the diagram and the inspector with it.\n"
	t += _sub("The six cycles")
	t += _grid(["#", "State", "Control signals", "What happens", "At the clock edge"], [
		["1", "FETCH_ADDR", _sig("GatePC LD.MAR PCINC=+2 PCMUX=PC+2 LD.PC"), "The PC drives the bus ([code]0x00003006[/code]) so MAR can take it. The +2 incrementer and PCMUX prepare PC + 2.", "MAR ← [code]0x00003006[/code]\nPC → [code]0x00003008[/code]"],
		["2", "FETCH_MEMORY", _sig("MEM.EN R/W=READ LD.MDR"), "Memory reads the halfword at MAR. Nothing is on the bus: memory's output goes straight to MDR.", "MDR ← [code]0x00001842[/code]"],
		["3", "FETCH_IR", _sig("GateMDR LD.IR"), "MDR drives the bus ([code]0x00001842[/code]) and IR takes it: the instruction is now where the FSM can read it.", "IR ← [code]0x1842[/code]"],
		["4", "DECODE", _sig("DR=R2 SR1=R0 SR2=R1 ALUK=ADD"), "The FSM reads IR: an ADDS, result to R2, inputs R0 and R1. It sets up register numbers and the ALU operation for the next cycles.", "nothing is stored"],
		["5", "FETCH_OPERANDS", _sig("SR1=R0 SR2=R1 LD.ALUA LD.ALUB"), "The register file reads R0 and R1 into the ALU's input latches. The bus is not needed: the register file has its own read ports.", "ALU A ← [code]0x00000003[/code]\nALU B ← [code]0x00000004[/code]"],
		["6", "EXECUTE_COMMIT", _sig("ALUK=ADD GateALU DR=R2 LD.REG LD.CC"), "The ALU adds; GateALU puts the result, [code]0x00000007[/code], on the bus; R2 and the flags load it.", "R2 → [code]0x00000007[/code]\nN Z C V ← 0 0 0 0"],
	], 3)
	t += "(ALU B already held 4 from the instruction before, so picosim lists only ALU A as changed in cycle 5.)\n"
	t += _sub("Decoding 0x1842")
	t += "How does the FSM know, from [code]0x1842[/code], that this is ADDS R2, R0, R1? It looks at groups of bits ([b]fields[/b]). The Thumb encoding of ADDS with three registers is:\n"
	t += _grid(["Bits", "15 – 9", "8 – 6", "5 – 3", "2 – 0"], [
		["Field", "opcode: ADDS (registers)", "Rm", "Rn", "Rd"],
		["0x1842", "[code]0001100[/code]", "[code]001[/code] = R1", "[code]000[/code] = R0", "[code]010[/code] = R2"],
	])
	t += "Write [code]0x1842[/code] in binary ([code]0001 1000 0100 0010[/code]) and split it at those places to check. Each register field is 3 bits, which is why these instructions can only name R0–R7.\n"
	t += _sub("What to notice")
	t += "• The first three cycles are the same for every instruction: that is the fetch.\n"
	t += "• The PC already points at the [i]next[/i] instruction ([code]0x3008[/code]) after the first cycle.\n"
	t += "• The bus carries at most one value per cycle, and in cycles 2, 4 and 5 it carries nothing.\n"
	t += "• R2 changes only at the very last clock edge. Until then the Registers panel still shows the old value.\n"
	t += _sub("Check yourself")
	t += "1. How many cycles does [code]subs r3, r1, r0[/code] take, and what changes?\n"
	t += "2. In which cycle does the PC change, and why so early?\n"
	t += "3. Why does FETCH_MEMORY not use the bus?\n"
	t += "[color=%s]Answers: 1. Six, the same states; DECODE sets ALUK=SUB, DR=R3, SR1=R1, SR2=R0. 2. At the end of FETCH_ADDR: the +2 incrementer works in the same cycle that the PC is sent to MAR, so the PC is ready for the next fetch. 3. Memory's data output is wired straight to MDR; only MDR's [i]output[/i] goes through a tri-state buffer onto the bus.[/color]\n" % _c("dim")
	t += "\nNext: " + _link("kinds", "Other instructions") + " shows the other kinds of instruction the same way.\n"
	return t


## Control signals in a table cell, in the diagram's control-signal colour.
static func _sig(s: String) -> String:
	return "[color=%s][code]%s[/code][/color]" % [_c("purple"), s]


## "Other instructions": the states after the fetch, with real values.
func _kinds() -> String:
	var t := _h("Loads, stores, branches and calls")
	t += "Every instruction starts with the same 3-cycle fetch (see the " + _link("tutorial", "tutorial") + "). What follows depends on the kind of instruction. These are the cycles after the fetch, with the values picosim really shows. Set a breakpoint on the instruction, Run to it, and press " + _k("Step cycle") + " to follow along.\n"
	t += _sub("Load: ldr r0, [lb]r1]  —  7 cycles")
	t += "From [b]Add up a list in memory[/b], address [code]3008[/code], first pass (R1 = [code]0x3024[/code]):\n"
	t += _grid(["State", "What happens", "At the clock edge"], [
		["DECODE", "A word load into R0; the address will be R1 + 0. " + _sig("DR=R0 ADDR1MUX=SR1 ADDR2MUX=ZEXT(IR[lb]10:6])x4 SIZE=word"), "nothing"],
		["EVALUATE_ADDRESS", "The address adder adds R1 and the offset (0) and drives the sum onto the bus through GateADDR. " + _sig("GateADDR LD.MAR"), "MAR ← [code]0x00003024[/code]"],
		["FETCH_OPERANDS", "Memory reads the word at MAR. " + _sig("MEM.EN R/W=READ LD.MDR"), "MDR ← [code]0x0000000A[/code]"],
		["STORE_RESULT", "MDR drives the bus through LOAD EXT (which would fill in the top bits for ldrb or ldrh) and R0 loads it. " + _sig("GateMDR DR=R0 LD.REG"), "R0 → [code]0x0000000A[/code]"],
	], 1)
	t += _sub("Store: str r2, [lb]r1]  —  7 cycles")
	t += "Same example, address [code]3014[/code] (R1 = [code]0x3034[/code], R2 = [code]0x64[/code]):\n"
	t += _grid(["State", "What happens", "At the clock edge"], [
		["EVALUATE_ADDRESS", "As for the load: MAR gets the address.", "MAR ← [code]0x00003034[/code]"],
		["FETCH_OPERANDS", "The data to store must get into MDR. R2 goes through the ALU unchanged ([code]ALUK=PASS[/code]) and onto the bus. " + _sig("SR1=R2 ALUK=PASS GateALU LD.MDR"), "MDR ← [code]0x00000064[/code]"],
		["STORE_RESULT", "Memory writes MDR at the address in MAR. " + _sig("MEM.EN R/W=WRITE"), "memory [code]3034[/code]–[code]3037[/code] ← [code]64 00 00 00[/code]"],
	], 1)
	t += _sub("Branch: bne loop  —  5 cycles")
	t += "Same example, address [code]3010[/code], when Z = 0 (taken):\n"
	t += _grid(["State", "What happens", "At the clock edge"], [
		["DECODE", "A conditional branch: condition NE; the target will be PC + offset. " + _sig("COND=NE ADDR1MUX=PC ADDR2MUX=SEXT(offset)x2"), "nothing"],
		["EXECUTE_PC", "COND EVAL checks the flags: Z = 0, so [code]BranchTaken=1[/code]. The address adder works out the target, [code]0x00003014 + (−12) = 0x00003008[/code], and PCMUX passes it to the PC. " + _sig("BranchTaken=1 PCMUX=ADDER LD.PC"), "PC → [code]0x00003008[/code]"],
	], 1)
	t += "When the branch is [i]not[/i] taken (on the last pass), BranchTaken = 0 and LD.PC stays off, so the PC keeps [code]0x3012[/code], the next instruction, which the fetch already put there. Either way it takes 5 cycles.\n"
	t += _note("PC + 4", "Why [code]0x3014[/code] and not [code]0x3010[/code]? On ARM, an instruction that uses the PC as a value always sees its own address + 4. The assembler knows this and works out the offset (−12) to match.")
	t += _sub("Call: bl twice  —  9 cycles")
	t += "From [b]Call a function[/b], address [code]3004[/code]. BL is 32 bits long, so it is fetched in two halves:\n"
	t += _grid(["State", "What happens", "At the clock edge"], [
		["FETCH_IR", "IR's top bits ([code]0xF000[/code]) say this is a 32-bit instruction, so…", "IR ← [code]0xF000[/code]"],
		["FETCH2_ADDR, FETCH2_MEMORY, FETCH2_IR", "…the fetch runs again for the second halfword, into IR2.", "IR2 ← [code]0xF80C[/code], PC → [code]0x00003008[/code]"],
		["DECODE", "BL: the offset (24) comes from bits of both IR and IR2. " + _sig("DR=LR ADDR1MUX=PC ADDR2MUX=SEXT(offset)x2"), "nothing"],
		["LINK", "The PC (already the address of the next instruction) goes onto the bus with bit 0 set by SET THUMB, into LR. " + _sig("GatePC SET THUMB BIT LD.LR"), "LR → [code]0x00003009[/code]"],
		["EXECUTE_PC", "PC ← [code]0x00003008 + 24[/code]. " + _sig("PCMUX=ADDER LD.PC"), "PC → [code]0x00003020[/code] ([code]twice[/code])"],
	], 1)
	t += _sub("Push and pop: 6 + 2 cycles per register")
	t += "[code]push {lr}[/code] in print_digit ([code]3024[/code], SP = [code]0xFFF8[/code]), 8 cycles:\n"
	t += _grid(["State", "What happens", "At the clock edge"], [
		["EVALUATE_ADDRESS", "The address adder works out SP − 4 × (number of registers). " + _sig("ADDR1MUX=SP ADDR2MUX=-4n GateADDR LD.MAR"), "MAR ← [code]0x0000FFF4[/code]"],
		["FETCH_OPERANDS", "LR passes through the ALU into MDR. " + _sig("SR1=LR ALUK=PASS GateALU LD.MDR"), "MDR ← [code]0x0000300D[/code]"],
		["STORE_RESULT", "Memory writes it; MAR steps on by 4 for the next register. " + _sig("MEM.EN R/W=WRITE MAR+4"), "memory [code]FFF4[/code] ← [code]0x0000300D[/code]"],
		["WRITEBACK", "SP is lowered by the same amount. " + _sig("ADDR1MUX=SP ADDR2MUX=-4n GateADDR LD.SP"), "SP → [code]0x0000FFF4[/code]"],
	], 1)
	t += "[code]pop {pc}[/code] at [code]302C[/code] mirrors it: MAR ← SP ([code]0xFFF4[/code]); memory → MDR ([code]0x0000300D[/code]); then, because the register is the PC, [b]EXECUTE_PC[/b] loads PC ← MDR with bit 0 cleared by CLR THUMB ([code]0x0000300C[/code]); WRITEBACK raises SP to [code]0xFFF8[/code]. With 2 registers ([code]push {r7, lr}[/code]) the FETCH_OPERANDS / STORE_RESULT pair runs twice: 10 cycles.\n"
	t += _sub("All the counts")
	t += "Operate (ADDS, MOVS, CMP …) 6 · load and store 7 · B and B<cond> 5 · BX 6 · BL 9 · PUSH and POP 6 + 2 per register · SVC and BKPT 5. " + _link("states", "FSM states") + " lists every sequence, and the full table is in [code]docs/cycles.md[/code].\n"
	return t


## "Control signals": what every name in the inspector means.
func _signals() -> String:
	var t := _h("Control signals")
	t += "Each cycle, the FSM turns on a few [b]control signals[/b]. The cycle inspector lists them under [b]Control signals asserted[/b], and on the diagram they are the purple dashed stubs (a bright one is on). Most names follow a pattern:\n"
	t += _grid(["Signal", "Meaning", "Examples"], [
		[_sig("GateX"), "Turn on X's tri-state buffer: X drives the bus this cycle. At most one at a time.", _sig("GatePC GateMDR GateALU GateADDR GateVEC")],
		[_sig("LD.X"), "X loads a new value at the clock edge. Without it, X keeps its value.", _sig("LD.PC LD.MAR LD.MDR LD.IR LD.IR2 LD.REG LD.CC LD.LR LD.SP LD.ALUA LD.ALUB")],
		[_sig("DR=Rn"), "Destination register: which register LD.REG writes.", _sig("DR=R2")],
		[_sig("SR1=Rn SR2=Rn"), "Source registers: which registers the register file reads.", _sig("SR1=R0 SR2=R1")],
		[_sig("PCMUX=…"), "Where the PC's next value comes from: the +2 incrementer, the address adder, or the bus.", _sig("PCMUX=PC+2 PCMUX=ADDER PCMUX=BUS")],
		[_sig("PCINC=+2"), "The incrementer adds 2 to the PC (one halfword).", ""],
		[_sig("ADDR1MUX=…"), "The address adder's base: the PC, a register (SR1) or SP.", _sig("ADDR1MUX=SR1")],
		[_sig("ADDR2MUX=…"), "The address adder's offset: an immediate from IR (scaled), a register, or ±4 × the number of registers.", _sig("ADDR2MUX=ZEXT(IR[lb]10:6])x4 ADDR2MUX=-4n")],
		[_sig("SR2MUX=IMM"), "The ALU's B input takes the immediate from the instruction instead of a register.", ""],
		[_sig("ALUK=…"), "The ALU's operation, named after the instruction (ADD, SUB, AND, LSL, MUL …). PASS lets input A through unchanged.", _sig("ALUK=ADD ALUK=PASS")],
		[_sig("MEM.EN R/W=…"), "Memory does an access this cycle; READ or WRITE says which way.", _sig("MEM.EN R/W=READ")],
		[_sig("SIZE=…"), "The access size: word, halfword or byte.", _sig("SIZE=word")],
		[_sig("LOAD EXT=…"), "How LOAD EXT widens a loaded byte or halfword to 32 bits: with zeros (Z…) or copies of its sign bit (S…).", _sig("LOAD EXT=Zword")],
		[_sig("STORE ALIGN"), "Keeps only the byte or halfword a STRB or STRH writes.", ""],
		[_sig("IOSEL=IO"), "The access goes to an I/O register (address 0x10000 and up) instead of RAM.", ""],
		[_sig("COND=… BranchTaken=…"), "The branch condition from DECODE, and COND EVAL's answer (1 = jump).", _sig("COND=NE BranchTaken=1")],
		[_sig("SET THUMB BIT") + " / " + _sig("CLR THUMB BIT"), "Set bit 0 of a return address going into LR; clear it on an address going into the PC.", ""],
		[_sig("MAR+4"), "The +4 incrementer moves MAR on to the next word (PUSH, POP, LDM, STM).", ""],
		[_sig("EXCEPTION") + " / " + _sig("HALT"), "An SVC call into the OS, and BKPT stopping the CPU.", ""],
	], 1)
	t += "Signals listed in DECODE are [i]prepared[/i] there (register numbers, mux selects, the ALU operation) for the cycles that follow; nothing is stored in DECODE itself.\n"
	return t


# ── level 4 pages: pipeline and performance ─────────────────────────────────

## "Pipelining: the big idea", with one pass of the 04_memory.s loop.
func _pipe_intro() -> String:
	var t := _h("Pipelining: the big idea")
	t += "[i]Beyond the textbook: the course text stops at the multi-cycle datapath. This level is for going further.[/i]\n\n"
	t += "On the multi-cycle datapath, one instruction has the whole CPU to itself until it is finished, and most parts sit idle most of the time. The real Pico's processor, the [b]Cortex-M0+[/b], does better by working on two instructions at once.\n"
	t += _sub("The laundry idea")
	t += "Washing and drying two loads of laundry one after the other takes wash + dry + wash + dry. But while the first load is drying, the washer is free: start the second load then. Each load still takes as long as before, but a load comes out of the dryer every [i]one[/i] step instead of every two. That is a [b]pipeline[/b].\n"
	t += _sub("The Cortex-M0+'s two stages")
	t += "The M0+ splits each instruction into two [b]stages[/b] that work at the same time, each on a different instruction:\n"
	t += "• [color=%s][b]Fetch[/b][/color] reads the [i]next[/i] instruction from memory.\n" % _c("stage_f")
	t += "• [color=%s][b]Decode + Execute[/b][/color] decodes and runs the [i]current[/i] one.\n" % _c("stage_e")
	t += "Between them sits a [b]pipeline register[/b] (IF/EX) that hands the fetched instruction on. When nothing gets in the way, one instruction finishes every cycle: [b]CPI = 1[/b] (cycles per instruction).\n"
	t += _sub("Latency and throughput")
	t += "• [b]Latency[/b]: how long one instruction takes from start to finish. Pipelining does not shorten it.\n"
	t += "• [b]Throughput[/b]: how many instructions finish per cycle. Pipelining raises it.\n"
	t += _sub("A real example")
	t += "One pass round the loop of [b]Add up a list in memory[/b], as the Performance window lays it out on the RP2040 (F = fetch, E = execute, R = a taken branch refilling, ✕ = fetched but thrown away):\n"
	var f := "[color=%s][b]F[/b][/color]" % _c("stage_f")
	var e := "[color=%s][b]E[/b][/color]" % _c("stage_e")
	var r := "[color=%s][b]R[/b][/color]" % _c("stage_e")
	var x := "[color=%s][b]✕[/b][/color]" % _c("red")
	t += _grid(["Instruction", "1", "2", "3", "4", "5", "6", "7"], [
		[_asm("ldr r0, [r1]"), e, e, "", "", "", "", ""],
		[_asm("adds r2, r2, r0"), "", f, e, "", "", "", ""],
		[_asm("adds r1, #4"), "", "", f, e, "", "", ""],
		[_asm("subs r3, #1"), "", "", "", f, e, "", ""],
		[_asm("bne loop"), "", "", "", "", f, e, r],
		[_asm("ldr r1, [pc, #0xc]"), "", "", "", "", "", x, ""],
		[_asm("ldr r0, [r1]") + " (next pass)", "", "", "", "", "", "", f],
	], 0)
	t += "• The load needs Execute for 2 cycles (one more to reach memory over the bus), so the [code]adds[/code] behind it can only execute in cycle 3.\n"
	t += "• The staircase from cycle 2 to 5: one instruction fetched while the one before executes.\n"
	t += "• [code]bne[/code] is taken. Fetch had already guessed \"not taken\" and fetched the next instruction in memory; that one is thrown away (✕, a [b]flush[/b]), and the branch spends one more cycle (R) while the right instruction is fetched.\n"
	t += "So a pass takes [b]7 cycles[/b] for 5 instructions on the RP2040. On the multi-cycle datapath the same pass takes 7 + 6 + 6 + 6 + 5 = [b]30 cycles[/b].\n"
	t += _sub("What breaks the pattern")
	t += "A perfect pipeline would reach CPI = 1. Two things cost extra cycles on the M0+:\n"
	t += "• Instructions that need Execute for several cycles: loads and stores (2), PUSH and POP (1 + one per register; 3 + one per register for a POP that loads the PC), BL (3).\n"
	t += "• Taken branches, calls and returns: the instruction fetched behind them is flushed and the target must be fetched (1 extra cycle).\n"
	t += "Problems like these are called [b]hazards[/b]. A third kind, the [i]data hazard[/i] (an instruction needing a result that is not ready yet), cannot happen with only one Execute stage. " + _link("pipecycles", "Pipeline cycle by cycle") + " has the details.\n"
	t += _sub("Deeper pipelines")
	t += "Splitting the work into more stages lets each stage be simpler, so a real chip could tick faster. But a taken branch then throws more away. The Performance window compares a 3-stage design (fetch, decode, execute), which loses 2 cycles per taken branch instead of 1. picosim times all machines at the same clock, so you see only the cycle counts change.\n"
	t += _note("Same results", "Pipelining changes only [i]when[/i] things happen. The same program gives exactly the same register values, memory and output on every machine.")
	t += "Next: " + _link("pipeline", "Pipeline diagram") + " shows how to watch it in picosim.\n"
	return t


## "Performance: the formulas", with the 04_memory.s numbers.
func _perf_math() -> String:
	var t := _h("Performance: the formulas")
	t += "How long does a program take? Three numbers decide it:\n"
	t += _grid(["Number", "Meaning", "Depends on"], [
		["[b]N[/b], instruction count", "How many instructions the program runs (loops count every pass).", "the program and its data"],
		["[b]CPI[/b], cycles per instruction", "The average number of clock cycles each instruction takes.", "the processor's design, and the mix of instructions"],
		["[b]f[/b], clock frequency", "Clock cycles per second. 125 MHz = 125 000 000 per second.", "the chip (and its settings)"],
	], 1)
	t += "They combine like this:\n"
	t += _codeblock("cycles = N × CPI\ntime   = cycles ÷ f   = N × CPI ÷ f")
	t += "One cycle at 125 MHz lasts 1 ÷ 125 000 000 s = 8 ns (nanoseconds), so 125 cycles take 1 µs (a microsecond, a millionth of a second).\n"
	t += _sub("Worked example")
	t += "[b]Add up a list in memory[/b], run to the end (32 instructions, counting the OS's). The Performance window's Metrics tab shows:\n"
	t += _grid(["Machine", "Cycles", "CPI", "Time at 125 MHz"], [
		["Multi-cycle (teaching datapath)", "205", "6.41", "1.64 µs"],
		["2-stage pipeline (RP2040)", "50", "1.61", "0.40 µs"],
		["3-stage pipeline (teaching)", "55", "1.77", "0.44 µs"],
	])
	t += "Check the first row: CPI = 205 ÷ 32 = 6.41, and time = 205 ÷ 125 MHz = 1.64 µs. For the pipelines, the final BKPT is not timed (the datasheet gives no fixed count for it), so their CPI is over 31 instructions: 50 ÷ 31 = 1.61.\n"
	t += _sub("Speed-up")
	t += "[b]Speed-up[/b] = old time ÷ new time. At the same clock that is old cycles ÷ new cycles: 205 ÷ 50 = [b]4.1×[/b]. The RP2040's pipeline runs this program 4.1 times as fast as the teaching datapath. The Compare tab prints this ratio for you.\n"
	t += _sub("Where the cycles go")
	t += "Improving the part that takes most of the time helps most. In the example, the multi-cycle machine spends 96 of its 205 cycles (47 %) just fetching instructions, which is exactly the work a pipeline overlaps with executing. The Metrics tab shows this breakdown by phase and by kind of instruction.\n"
	t += _sub("Check yourself")
	t += "1. A program runs 1 000 instructions with CPI 1.5 at 125 MHz. How many cycles, and how long?\n"
	t += "2. The same program on the multi-cycle machine has CPI 6.3. What is the speed-up of the pipeline?\n"
	t += "3. A loop runs 5 instructions in 7 cycles per pass. What is its CPI?\n"
	t += "[color=%s]Answers: 1. 1 500 cycles; 1 500 ÷ 125 000 000 s = 12 µs. 2. 6.3 ÷ 1.5 = 4.2× (same N and f, so only CPI matters). 3. 7 ÷ 5 = 1.4.[/color]\n" % _c("dim")
	t += "\nTry it on your own programs with the " + _link("performance", "Performance window") + ".\n"
	return t


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")


## A link drawn as a small button.
func _button(meta: String, label: String) -> String:
	return "[url=%s][bgcolor=%s][color=%s] %s [/color][/bgcolor][/url]" % [meta, _c("sig_bg"), _c("accent"), label]


static func _row(k: String, what: String) -> String:
	return "[cell][color=%s][code]%s[/code][/color]    [/cell][cell]%s[/cell]" % [_c("yellow"), k, what]


## How to set a breakpoint, for a sentence: from Level 3 the walkthrough
## beside the CPU diagram lists the program (the Disassembly makes way for
## the Memory tab while the diagram is shown).
func _bp_click() -> String:
	if Level.at_least(3):
		return "click a step's ● column in the Walkthrough beside the CPU diagram, or a row's first column in the Disassembly when the diagram is hidden"
	return "click a row's first column in the Disassembly"


## "The walkthrough beside the diagram" (walk_panel.gd), on the datapath
## diagram's page and, with `pipe`, the pipeline diagram's.
func _walk_help(pipe: bool) -> String:
	var unit := "pipeline cycles" if pipe else "clock cycles"
	var cpu := "the pipeline" if pipe else "the CPU"
	var t := _sub("The walkthrough beside the diagram")
	t += "While the diagram is shown, the walkthrough lists your program instead of the Disassembly, which makes way for the " + _link("memory", "Memory tab") + " under the Registers (" + _k("Hide CPU Diagram") + " brings the Disassembly back).\n"
	t += "To the right of the diagram, the [b]Walkthrough[/b] lists every instruction your program runs, in the order " + cpu + " runs it, starting with the built-in OS's [code]_start[/code]. A heading marks where each function, label or piece of the built-in OS begins, and code that runs again (a loop going round) is listed again, with its pass number. Each row gives the step number, the address and instruction, and its " + unit + "; hover a row for what it does.\n"
	t += "• [color=" + _c("changed") + "][b]▶[/b][/color] and a highlight mark the step " + cpu + (" is executing" if pipe else " is on") + ", and the list scrolls to keep it in view as you step. When something on the diagram is confusing, that row is the instruction it belongs to.\n"
	if pipe:
		t += "• Click a step to read it under the list: what it does, its row of RP2040 datasheet Table 81 and its pipeline cycles (Execute, a taken branch, a refill), with ✓ on the ones already run.\n"
	else:
		t += "• Click a step to read it under the list: what it does and each of its clock cycles with what that cycle does. ✓ marks the cycles already run and ▶ the one the diagram is showing.\n"
	t += "• " + _k("◀ Previous") + " and " + _k("Next ▶") + " read the steps around it without moving " + cpu + "; " + _k("▶ back to where you are") + " returns to the marked step.\n"
	t += "• " + _k("Go here") + " (or a double click on a step) puts " + cpu + " just before that instruction, as if you had stepped there from the start" + ("" if pipe else ", and clicking one of its cycles stops right after that cycle") + ". It replays the program the way " + _k("◀ Step back") + " does, so it works forwards and backwards, and keys you typed in the serial console are typed again. Then carry on stepping from there. It works in Simulator mode.\n"
	t += "• [color=" + _c("bp") + "]●[/color] [b]Breakpoints[/b]: click the narrow first column of a step to put a red ● there, and again to remove it. " + _k("Run") + " stops just before that instruction, every time it comes round (a loop's instruction shows the ● on every pass).\n"
	t += "• " + _k("Skip to main ▸") + " in its title bar runs the built-in OS's start-up and stops on the first line of your [code]main[/code]; it shows while the start-up is running.\n"
	t += "• " + _k("▸") + " folds the walkthrough away to give the diagram more room; " + _k("◂") + " brings it back.\n"
	t += "It stops after 500 steps, or at a [code]getchar[/code] waiting for a key you have not typed yet: type the key and the list carries on. If you change a register or memory by hand, your run can go a different way from the list; the panel says so, and " + _k("Reload") + " starts over.\n"
	return t


## "FSM states": every state of the control unit, from state_info.gd.
func _add_states() -> void:
	if not ResourceLoader.exists(STATES_PATH):
		return
	var info = load(STATES_PATH)
	var dim := _c("dim")
	var t := _h("FSM states")
	t += "The control unit of the multi-cycle datapath is a finite-state machine (FSM). In every clock cycle it is in exactly one [b]state[/b], and the state decides which control signals are on: which tri-state buffer drives the bus, which registers load at the clock edge, what the muxes select and what the ALU does. Stepping one clock cycle runs one state.\n\n"
	t += "You see the state in the FSM box on the " + _link("diagram", "datapath diagram") + ", in the list of the " + _link("cycles", "cycle-by-cycle inspector") + " (hover a row for a summary) and as [b]Next state[/b] in the Registers panel.\n"
	t += _sub("The six phases")
	t += "Each state belongs to one phase of the instruction cycle. The inspector's chips light up for the phase of the cycle shown.\n"
	t += "[table=3]"
	for p in info.PHASES:
		t += "[cell padding=0,3,14,3][color=%s][b]%s[/b][/color][/cell][cell padding=0,3,14,3][b]%s[/b][/cell][cell padding=0,3,0,3]%s [color=%s]%s[/color][/cell]" % [
			_c("yellow"), p[1], str(p[0]).capitalize(), p[2], dim, ", ".join(p[3])]
	t += "[/table]\n"
	t += _sub("Which states each instruction uses")
	t += "Every instruction starts with the same fetch, [code]FETCH_ADDR → FETCH_MEMORY → FETCH_IR[/code] (3 cycles). After that:\n"
	t += "[table=3][cell padding=0,3,14,3][b]Instruction[/b][/cell][cell padding=0,3,14,3][b]States after the fetch[/b][/cell][cell padding=0,3,0,3][b]Cycles[/b][/cell]"
	for q in info.SEQUENCES:
		t += "[cell padding=0,3,14,3]%s[/cell][cell padding=0,3,14,3][color=%s][code]%s[/code][/color][/cell][cell padding=0,3,0,3]%s[/cell]" % [
			q[0], _c("cyan"), q[1], str(q[2])]
	t += "[/table]\n"
	t += "Some states appear in several kinds of instruction and do a different job in each (FETCH_OPERANDS reads memory for a load but feeds the ALU for an ADDS); each state below lists what it does for which instructions.\n"
	t += _sub("Other words you may see")
	t += "• [b]HALTED[/b] — shown as Next state after BKPT (or a CPU fault): the CPU is stopped. " + _k("Reload") + " starts again.\n"
	t += "• [b](next)[/b] — the inspector's row for the cycle that has not run yet; between instructions it is always FETCH_ADDR.\n"
	t += "• [b]IOSEL=IO[/b] — a memory access to an address of 0x10000 or above goes to the Pico's I/O registers instead of RAM.\n"
	for st in info.ORDER:
		t += "\n[hr color=%s height=1]\n" % _c("edge")
		t += info.entry_bbcode(st, roundi(20 * text_scale)) + "\n"
	_topic("states", "FSM states", t)


func _add_components() -> void:
	if not ResourceLoader.exists(INFO_PATH):
		return
	var info = load(INFO_PATH)
	var names := {}
	for id in info.INFO:
		names[id] = str(info.INFO[id]["title"]).get_slice(" — ", 0)
	var t := _h("Datapath components") + "Every block on the multi-cycle " + _link("diagram", "datapath diagram") + ", in the order data usually flows. Click a block on the diagram to see the same text next to it.\n"
	for id in info.ORDER:
		t += "\n[hr color=%s height=1]\n" % _c("edge")
		t += info.bbcode(id, names, roundi(20 * text_scale)) + "\n"
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
		t += "\n[hr color=%s height=1]\n" % _c("edge")
		t += info.entry_bbcode(info.PIPE_INFO[id], names, roundi(20 * text_scale)) + "\n"
	_topic("pipecomponents", "Pipeline components", t)
