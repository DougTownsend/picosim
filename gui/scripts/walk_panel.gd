extends PanelContainer
## The walkthrough, beside the CPU diagram: every instruction the program
## runs, in order, with what it does and its clock cycles (or, on the 2-stage
## pipeline, its pipeline cycles).  The step the machine is on is marked
## "▶ you are here" and kept in view as the student steps, so whatever they
## see on the live diagram is explained right next to it.
##
## Clicking a step shows it below the list; Go here (or a double click) puts
## the real machine there, just before that instruction or partway through
## it at one of its cycles, by replaying the program (the backend's goto,
## like Step back).  The student carries on stepping from there.
##
## At Levels 3 and 4 the walkthrough stands in for the Disassembly (which
## makes way for the Memory tab while the diagram is shown), so it also has
## the ● breakpoint column and Skip to main.
##
## The steps come from the backend's walkthrough command (picosim/
## walkthrough.py, compact form), which runs a fresh copy of the program with
## the keys typed so far.  Values typed in by hand can make the live run
## differ; the panel says so when the instruction it expects is not the one
## the machine is on.

const HelpButton := preload("res://scripts/help_button.gd")

const LIMIT := 500                    # steps fetched (the walkthrough stops there)
const CYCLE_KEYS := {"FETCH": "accent", "DECODE": "purple", "EVALUATE ADDRESS": "yellow",
	"FETCH OPERANDS": "cyan", "EXECUTE": "red", "STORE RESULT": "green"}
const PIPE_KEYS := {"FETCH": "stage_f", "EXECUTE": "stage_e", "DECODE": "stage_d", "TAKEN": "red"}

var title: Label
var info: Label
var note: Label
var tree: Tree
var detail: RichTextLabel
var fold_btn: Button
var btn_main: Button                  # Skip to main, while the built-in OS starts up
var open_btn: Button
var body: VBoxContainer
var is_open := true

var _wt: Dictionary = {}              # the compact walkthrough message
var _want := ""                       # what the walkthrough on hand was asked for (path|machine|keys)
var _waiting := false
var _items: Array = []                # TreeItem per step
var _cur := -1                        # step the machine is on (-1: none)
var _cur_k := 0                       # cycles of it already run
var _sel := -1                        # step shown below the list
var _st: Dictionary = {}
var _bps_key = null                   # the breakpoints the ● column shows


func _ready() -> void:
	custom_minimum_size.x = 350
	tooltip_text = "Every step of your program in the order it runs, with ▶ on the one you are on. Click a step to read it; Go here jumps the CPU there. Click ● to set a breakpoint"
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	var cfg := ConfigFile.new()
	if cfg.load(Level.SETTINGS) == OK:
		is_open = bool(cfg.get_value("ui", "walkthrough_open", true))

	var outer := HBoxContainer.new()
	add_child(outer)
	open_btn = Button.new()
	open_btn.text = "◂"
	open_btn.tooltip_text = "Show the walkthrough: every step of your program, and where you are"
	open_btn.focus_mode = Control.FOCUS_NONE
	open_btn.size_flags_vertical = Control.SIZE_EXPAND_FILL
	open_btn.pressed.connect(func(): set_open(true))
	outer.add_child(open_btn)

	body = VBoxContainer.new()
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_theme_constant_override("separation", 6)
	outer.add_child(body)
	var head := HBoxContainer.new()
	body.add_child(head)
	title = Label.new()
	title.text = "Walkthrough"
	title.add_theme_font_size_override("font_size", 15)
	head.add_child(title)
	info = Label.new()
	info.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	info.add_theme_font_size_override("font_size", 12)
	info.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(info)
	btn_main = Button.new()
	btn_main.text = "Skip to main ▸"
	btn_main.visible = false
	btn_main.focus_mode = Control.FOCUS_NONE
	btn_main.add_theme_font_size_override("font_size", 12)
	btn_main.tooltip_text = "Run the built-in OS's start-up instructions and stop at the first line of your main"
	btn_main.pressed.connect(func(): Backend.send("run_to_main"))
	head.add_child(btn_main)
	fold_btn = Button.new()
	fold_btn.text = "▸"
	fold_btn.tooltip_text = "Fold the walkthrough away (the diagram gets the room)"
	fold_btn.focus_mode = Control.FOCUS_NONE
	fold_btn.flat = true
	fold_btn.pressed.connect(func(): set_open(false))
	head.add_child(HelpButton.new("diagram", "Datapath diagram and walkthrough"))
	head.add_child(fold_btn)

	note = Label.new()
	note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	note.add_theme_font_size_override("font_size", 12)
	note.visible = false
	body.add_child(note)

	var split := VSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_child(split)
	tree = Tree.new()
	tree.columns = 4                  # ● breakpoint · step number · instruction · cycles
	tree.hide_root = true
	tree.select_mode = Tree.SELECT_ROW
	tree.size_flags_vertical = Control.SIZE_EXPAND_FILL
	tree.size_flags_stretch_ratio = 1.4
	tree.set_column_expand(0, false)
	tree.set_column_custom_minimum_width(0, 24)
	tree.set_column_clip_content(0, true)     # too narrow for "…": the ● is drawn whole
	tree.set_column_expand(1, false)
	tree.set_column_custom_minimum_width(1, 48)
	tree.set_column_expand(3, false)
	tree.set_column_custom_minimum_width(3, 34)
	tree.add_theme_font_override("font", Backend.mono_font())
	tree.add_theme_font_size_override("font_size", 13)
	tree.focus_mode = Control.FOCUS_CLICK
	tree.item_selected.connect(_on_item_selected)
	tree.item_activated.connect(func(): _goto(_sel, 0))
	tree.gui_input.connect(_on_tree_input)
	split.add_child(tree)
	detail = RichTextLabel.new()
	detail.bbcode_enabled = true
	detail.selection_enabled = true
	detail.size_flags_vertical = Control.SIZE_EXPAND_FILL
	detail.custom_minimum_size.y = 150
	detail.meta_underlined = false
	detail.add_theme_font_override("mono_font", Backend.mono_font())
	detail.add_theme_font_size_override("normal_font_size", 14)
	detail.add_theme_font_size_override("bold_font_size", 14)
	detail.add_theme_font_size_override("mono_font_size", 13)
	detail.meta_clicked.connect(_on_meta)
	split.add_child(detail)

	Backend.walkthrough_received.connect(_on_walkthrough)
	Backend.pipe_walkthrough_received.connect(_on_walkthrough)
	Backend.state_changed.connect(_on_state)
	Backend.program_loaded.connect(func(_p): _want = ""; _wt = {})
	Palette.changed.connect(_apply_palette)
	visibility_changed.connect(func(): if is_visible_in_tree(): _on_state(Backend.state))
	_apply_palette()
	set_open(is_open, false)


## Show the list or fold it to a thin strip beside the diagram.
func set_open(on: bool, save := true) -> void:
	is_open = on
	body.visible = on
	open_btn.visible = not on
	custom_minimum_size.x = 350 if on else 0
	if save:
		var cfg := ConfigFile.new()
		cfg.load(Level.SETTINGS)
		cfg.set_value("ui", "walkthrough_open", on)
		cfg.save(Level.SETTINGS)
	if on:
		_on_state(Backend.state)


func _pipe() -> bool:
	return str(_st.get("machine", "multicycle")) == "m0plus"


# ── keeping up with the machine ─────────────────────────────────────────────

func _on_state(st: Dictionary) -> void:
	_st = st
	if not is_visible_in_tree() or not is_open:
		return
	if not st.get("loaded", false):
		_wt = {}
		_items.clear()
		tree.clear()
		info.text = ""
		detail.text = "[color=%s]Load a program and its walkthrough appears here.[/color]" % Palette.hex("dim")
		return
	btn_main.visible = Level.at_least(2) and st.get("at_boot", false) and not st.get("running", false)
	if st.get("running", false) and not st.get("waiting_input", false):
		return                      # explained where Run stops
	var want := "%s|%s|%d" % [st.get("path", ""), st.get("machine", ""), int(st.get("keys_typed", 0))]
	if want != _want and not _waiting:
		_want = want
		_waiting = true
		Backend.send("pipe_walkthrough" if _pipe() else "walkthrough", {"compact": true, "limit": LIMIT})
		if _wt.is_empty():
			detail.text = "[color=%s]Working through %s…[/color]" % [Palette.hex("dim"), str(st.get("path", "")).get_file()]
		return
	_follow(st)


func _on_walkthrough(data: Dictionary) -> void:
	_waiting = false
	if not data.get("compact", false):
		return                      # asked for by someone else (tests, older guide)
	_wt = data
	_build_tree()
	_cur = -2                       # force _follow to re-mark
	_follow(Backend.state)
	# typed keys or the machine changed while this one was being made (the
	# state as it is then, not now: newer states may arrive in between)
	(func(): _on_state(Backend.state)).call_deferred()


## Which step the machine is on: the instruction being run (partway through
## it, cycle_index cycles done) or, between instructions, the next one.  On
## the pipeline it is the instruction in Execute (the last one run), with
## its Execute cycles so far; with Execute empty (just loaded, or after Step
## back or Go here) it is the one in Fetch.
func _follow(st: Dictionary) -> void:
	if _wt.is_empty() or _items.is_empty() and _wt.get("steps", []).size() > 0:
		return
	var steps: Array = _wt["steps"]
	var in_insn: bool = st.get("in_insn", false) and not _pipe()
	var i := int(st.get("steps", 0)) - (1 if in_insn else 0)
	var k := int(st.get("cycle_index", 0)) if in_insn else 0
	var addr := int(st.get("insn_addr", -1))
	if _pipe():
		var pv = st.get("pipe")
		var e = pv.get("E") if pv is Dictionary else null
		if e is Dictionary:
			i -= 1
			k = int(e.get("k", 1)) + (1 if i == 0 else 0)   # step 0's first cycle is the first fetch
			addr = int(e.get("addr", -1))
		elif i == 0:
			k = 1                                             # the first fetch is on show
	if st.get("halted", false) and i >= steps.size():
		i = steps.size()
	_show_bps(st)
	var moved := i != _cur or k != _cur_k
	if _cur >= 0 and _cur < _items.size():
		_mark(_cur, false)
	_cur = i
	_cur_k = k
	note.visible = false
	if i >= 0 and i < steps.size():
		_mark(i, true)
		if int(steps[i]["addr"]) != addr and not st.get("halted", false):
			_note("Your run has gone a different way here from the walkthrough (a value was changed by hand), so the marker may be off. Reload to start over.")
		if moved:
			_sel = i
			tree.set_selected(_items[i], 0)
			tree.scroll_to_item(_items[i], true)
	elif i >= steps.size():
		if st.get("halted", false):
			_note("The program has finished: every step above has run. Reload to run it again.")
		elif st.get("waiting_input", false):
			_note("The program is waiting for a key: type one in the serial console and the walkthrough carries on from here.")
		else:
			_note("You're past the end of this walkthrough. " + str(_wt.get("stopped", "")))
		if moved:
			_sel = steps.size() - 1
	_show_detail()


func _note(t: String) -> void:
	note.text = t
	note.visible = true


# ── the list ────────────────────────────────────────────────────────────────

func _build_tree() -> void:
	tree.clear()
	_items.clear()
	var root := tree.create_item()
	var steps: Array = _wt.get("steps", [])
	var total := 0
	var passes := {}
	var called := {}
	for i in range(1, steps.size()):
		if str(steps[i - 1]["text"]).strip_edges().get_slice(" ", 0).to_lower() in ["bl", "blx"]:
			called[int(steps[i]["addr"])] = true
	for i in steps.size():
		var s: Dictionary = steps[i]
		total += s["cycles"].size()
		var lab := str(s["label"])
		if lab != "":
			passes[lab] = passes.get(lab, 0) + 1
			var h := tree.create_item(root)
			var what := "function" if called.has(int(s["addr"])) else ("built-in OS" if int(s["addr"]) < 0x3000 else "label")
			h.set_text(2, lab + ("  · pass %d" % passes[lab] if passes[lab] > 1 else "   " + what))
			h.set_tooltip_text(2, "%s: %s%s" % [lab, "a function in your program" if what == "function" else (
				"part of the built-in OS" if what == "built-in OS" else "a label in your program"),
				" · its code runs again (pass %d)" % passes[lab] if passes[lab] > 1 else ""])
			h.set_custom_color(2, Palette.c("label"))
			for c in 4:
				h.set_selectable(c, false)
		var it := tree.create_item(root)
		it.set_text(1, str(i + 1))
		it.set_text(2, "%04X  %s" % [int(s["addr"]), _tidy(str(s["text"]))])
		it.set_text(3, str(s["cycles"].size()))
		it.set_tooltip_text(0, "Toggle a breakpoint: Run stops just before this instruction")
		it.set_tooltip_text(2, str(s["summary"]))
		it.set_metadata(0, i)
		it.set_custom_color(0, Palette.c("bp"))
		it.set_custom_color(1, Palette.c("dim"))
		it.set_custom_color(3, Palette.c("dim"))
		if int(s["addr"]) < 0x3000:
			it.set_custom_color(2, Palette.c("muted"))
		_items.append(it)
	_bps_key = null
	var n := steps.size()
	info.text = ("  %d steps · %d %s" % [n, total, "cycles" if _pipe() else "clock cycles"]) if n > 0 else ""
	info.tooltip_text = str(_wt.get("stopped", ""))


func _mark(i: int, on: bool) -> void:
	var it: TreeItem = _items[i]
	if on:
		it.set_text(1, "▶ %d" % (i + 1))
		for c in 4:
			it.set_custom_bg_color(c, Palette.c("pc_row"))
		it.set_custom_color(1, Palette.c("changed"))
	else:
		it.set_text(1, str(i + 1))
		for c in 4:
			it.clear_custom_bg_color(c)
		it.set_custom_color(1, Palette.c("dim"))


## ● on every step at a breakpoint's address (a loop's instruction shows it
## on each pass), redrawn only when the breakpoints change.
func _show_bps(st: Dictionary) -> void:
	var bps: Array = st.get("breakpoints", [])
	if bps == _bps_key or _items.is_empty():
		return
	_bps_key = bps
	var on := {}
	for b in bps:
		on[int(b)] = true
	var steps: Array = _wt["steps"]
	for i in _items.size():
		_items[i].set_text(0, "●" if on.has(int(steps[i]["addr"])) else "")


## A click in the ● column sets or clears a breakpoint, as in the Disassembly.
func _on_tree_input(ev: InputEvent) -> void:
	var mb := ev as InputEventMouseButton
	if mb == null or not mb.pressed or mb.button_index != MOUSE_BUTTON_LEFT or mb.double_click:
		return
	if tree.get_column_at_position(mb.position) != 0:
		return
	var it := tree.get_item_at_position(mb.position)
	if it == null or it.get_metadata(0) == null:
		return
	Backend.send("toggle_bp", {"addr": int(_wt["steps"][int(it.get_metadata(0))]["addr"])})


func _on_item_selected() -> void:
	var it := tree.get_selected()
	if it == null or it.get_metadata(0) == null:
		return
	_sel = int(it.get_metadata(0))
	_show_detail()


static func _tidy(insn: String) -> String:
	var sp := insn.find(" ")
	if sp < 0:
		return insn
	return insn.substr(0, sp).rpad(6) + insn.substr(sp).strip_edges()


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")


# ── one step, below the list ────────────────────────────────────────────────

func _show_detail() -> void:
	var steps: Array = _wt.get("steps", [])
	if _sel < 0 or _sel >= steps.size():
		detail.text = ""
		return
	var s: Dictionary = steps[_sel]
	var here := _sel == _cur
	var dim := Palette.hex("dim")
	var t := ""
	if here:
		t += "[color=%s][b]▶ You are here[/b][/color]   [color=%s]step %d of %d%s[/color]\n" % [
			Palette.hex("changed"), dim, _sel + 1, steps.size(),
			(", %d of its %d cycles done" % [_cur_k, s["cycles"].size()]) if _cur_k > 0 else ", not started yet"]
	else:
		t += "[color=%s]Step %d of %d[/color]   %s\n" % [dim, _sel + 1, steps.size(),
			_link("here", "▶ back to where you are") if _cur >= 0 and _cur < steps.size() else ""]
	t += "[b][code]%s[/code][/b]\n" % _esc(_tidy(str(s["text"])))
	t += _esc(str(s["summary"])) + "\n"
	if _pipe() and s.has("row"):
		t += "[color=%s]Datasheet Table 81: %s%s[/color]\n" % [dim, _esc(str(s["row"])),
			" · taken branch: the pipeline is flushed" if bool(s.get("flushes", false)) else ""]
	t += "\n"
	var cycles: Array = s["cycles"]
	for k in cycles.size():
		var cy: Dictionary = cycles[k]
		var key: String = (PIPE_KEYS if _pipe() else CYCLE_KEYS).get(str(cy["phase"]), "accent")
		var name := "%d · %s" % [k + 1, str(cy["state"]).replace("_", "_​")]
		var done := here and k < _cur_k
		var now := here and k == _cur_k - 1           # the cycle the diagram is showing
		var line := ""
		if now:
			line = "[bgcolor=%s][color=%s][b] ▶ %s [/b][/color][/bgcolor]" % [Palette.hex("pc_row"), Palette.hex(key), name]
		elif _pipe():
			line = "[color=%s] %s %s[/color]" % [Palette.hex(key), "✓" if done else " ", name]
		else:
			line = "[url=cyc:%d:%d][color=%s] %s %s[/color][/url]" % [_sel, k, Palette.hex(key), "✓" if done else " ", name]
		if str(cy.get("what", "")) != "":
			line += "  [color=%s]%s[/color]" % [dim if not now else Palette.hex("text"), _esc(str(cy["what"]))]
		t += line + "\n"
	t += "\n"
	var nav := ""
	if _sel > 0:
		nav += _link("sel:%d" % (_sel - 1), "◀ Previous") + "   "
	if not (here and _cur_k == 0):
		nav += _link("go:%d" % _sel, "Go here") + "   "
	if _sel < steps.size() - 1:
		nav += _link("sel:%d" % (_sel + 1), "Next ▶")
	t += nav
	if not _pipe():
		t += "\n[color=%s]Go here puts the CPU just before this instruction; click a cycle to stop right after it. Then step on from there.[/color]" % dim
	else:
		t += "\n[color=%s]Go here puts the pipeline at this instruction; step on from there.[/color]" % dim
	detail.text = t


func _link(meta: String, label: String) -> String:
	return "[url=%s][bgcolor=%s][color=%s] %s [/color][/bgcolor][/url]" % [meta, Palette.hex("sig_bg"), Palette.hex("accent"), label]


func _on_meta(m: Variant) -> void:
	var s := str(m)
	if s == "here":
		if _cur >= 0 and _cur < _items.size():
			_sel = _cur
			tree.set_selected(_items[_cur], 0)
			tree.scroll_to_item(_items[_cur], true)
			_show_detail()
	elif s.begins_with("sel:"):
		_sel = int(s.substr(4))
		tree.set_selected(_items[_sel], 0)
		tree.scroll_to_item(_items[_sel])
		_show_detail()
	elif s.begins_with("go:"):
		_goto(int(s.substr(3)), 0)
	elif s.begins_with("cyc:"):
		var p := s.split(":")
		_goto(int(p[1]), int(p[2]) + 1)


## Put the real machine at step i, `cycle` cycles into it.
func _goto(i: int, cycle: int) -> void:
	if i < 0 or not Backend.state.get("loaded", false):
		return
	if str(Backend.state.get("mode", "sim")) != "sim":
		_note("Go here works in Simulator mode.")
		return
	Backend.send("goto", {"step": i, "cycle": cycle})


func _apply_palette() -> void:
	add_theme_stylebox_override("panel", Palette.panel_box("panel", 6, 10))
	info.add_theme_color_override("font_color", Palette.c("dim"))
	note.add_theme_color_override("font_color", Palette.c("yellow"))
	tree.add_theme_stylebox_override("panel", Palette.panel_box("surface", 4, 4))
	tree.add_theme_color_override("font_color", Palette.c("text"))
	if not _wt.is_empty():
		_build_tree()
		_cur = -2
		_follow(Backend.state)
