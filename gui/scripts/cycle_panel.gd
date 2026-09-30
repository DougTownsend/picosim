extends PanelContainer
## Cycle inspector: lists the clock cycles of the current instruction,
## explains the selected one in context (what the datapath does and which
## state actually changes), and lets you step back through earlier cycles.

signal cycle_selected(info: Dictionary)

const PHASES := ["FETCH", "DECODE", "EVALUATE ADDRESS", "FETCH OPERANDS", "EXECUTE", "STORE RESULT"]
const PHASE_COLORS := {
	"FETCH": Color("61afef"), "DECODE": Color("c678dd"), "EVALUATE ADDRESS": Color("e5c07b"),
	"FETCH OPERANDS": Color("56b6c2"), "EXECUTE": Color("e06c75"), "STORE RESULT": Color("98c379"),
}
const DIM := Color("7f8794")

var insn_label: Label
var progress_label: Label
var phase_row: HBoxContainer
var phase_chips := {}
var list: ItemList
var state_label: Label
var desc: RichTextLabel
var signals_text: RichTextLabel
var changes_text: RichTextLabel
var btn_prev: Button
var btn_next: Button

var history: Array = []
var selected := -1
var follow_latest := true
var asm := {}
var font: Font


func _ready() -> void:
	font = Backend.mono_font()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color("22262e")
	sb.set_corner_radius_all(6)
	sb.content_margin_left = 10
	sb.content_margin_right = 10
	sb.content_margin_top = 8
	sb.content_margin_bottom = 8
	add_theme_stylebox_override("panel", sb)

	var cols := HBoxContainer.new()
	cols.add_theme_constant_override("separation", 14)
	add_child(cols)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 6)
	v.custom_minimum_size.x = 290
	cols.add_child(v)
	var v2 := VBoxContainer.new()
	v2.add_theme_constant_override("separation", 6)
	v2.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	cols.add_child(v2)

	var t := Label.new()
	t.text = "Cycle by cycle"
	t.add_theme_font_size_override("font_size", 15)
	v.add_child(t)
	insn_label = Label.new()
	insn_label.add_theme_font_override("font", font)
	insn_label.add_theme_font_size_override("font_size", 14)
	insn_label.clip_text = true
	v.add_child(insn_label)
	progress_label = Label.new()
	progress_label.clip_text = true
	progress_label.add_theme_color_override("font_color", DIM)
	v.add_child(progress_label)

	phase_row = HBoxContainer.new()
	phase_row.add_theme_constant_override("separation", 3)
	v.add_child(phase_row)
	for p in PHASES:
		var chip := Label.new()
		chip.text = {"FETCH": "F", "DECODE": "D", "EVALUATE ADDRESS": "EA",
			"FETCH OPERANDS": "FO", "EXECUTE": "EX", "STORE RESULT": "SR"}[p]
		chip.tooltip_text = p
		chip.mouse_filter = Control.MOUSE_FILTER_PASS
		chip.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		chip.custom_minimum_size = Vector2(40, 22)
		chip.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var csb := StyleBoxFlat.new()
		csb.bg_color = Color("2c313a")
		csb.set_corner_radius_all(4)
		chip.add_theme_stylebox_override("normal", csb)
		phase_row.add_child(chip)
		phase_chips[p] = chip

	list = ItemList.new()
	list.custom_minimum_size.y = 110
	list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	list.size_flags_stretch_ratio = 0.8
	list.add_theme_font_override("font", font)
	list.add_theme_font_size_override("font_size", 13)
	list.item_selected.connect(_on_item_selected)
	v.add_child(list)

	var nav := HBoxContainer.new()
	v.add_child(nav)
	btn_prev = Button.new()
	btn_prev.text = "◀ Prev"
	btn_prev.focus_mode = Control.FOCUS_NONE
	btn_prev.pressed.connect(_prev)
	nav.add_child(btn_prev)
	btn_next = Button.new()
	btn_next.text = "Next ▶"
	btn_next.focus_mode = Control.FOCUS_NONE
	btn_next.tooltip_text = "Show the next cycle (clocks the CPU when you are at the latest cycle)"
	btn_next.pressed.connect(_next)
	btn_next.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	nav.add_child(btn_next)

	state_label = Label.new()
	state_label.clip_text = true
	state_label.add_theme_font_override("font", font)
	state_label.add_theme_font_size_override("font_size", 17)
	v2.add_child(state_label)
	desc = _rich(v2, 64)
	_heading(v2, "Control signals asserted")
	signals_text = _rich(v2, 48)
	_heading(v2, "What changed at the clock edge")
	changes_text = _rich(v2, 60)
	changes_text.size_flags_vertical = Control.SIZE_EXPAND_FILL

	Backend.program_loaded.connect(_on_program)


func _heading(parent: Control, text: String) -> void:
	var l := Label.new()
	l.text = text
	l.add_theme_color_override("font_color", DIM)
	l.add_theme_font_size_override("font_size", 12)
	parent.add_child(l)


func _rich(parent: Control, min_h: int) -> RichTextLabel:
	var r := RichTextLabel.new()
	r.bbcode_enabled = true
	# Fixed height (scrolls if needed) so changing text never resizes the panes.
	r.fit_content = false
	r.scroll_active = true
	r.custom_minimum_size.y = min_h
	r.add_theme_font_override("mono_font", font)
	r.add_theme_font_size_override("normal_font_size", 14)
	r.add_theme_font_size_override("mono_font_size", 13)
	r.selection_enabled = true
	parent.add_child(r)
	return r


func _on_program(prog: Dictionary) -> void:
	asm.clear()
	for row in prog["listing"]:
		asm[int(row["addr"])] = str(row["text"]).strip_edges()
	history = []
	selected = -1


func _asm_at(a: int) -> String:
	return asm.get(a, "???")


func update_state(st: Dictionary) -> void:
	if not st.get("loaded", false):
		return
	history = st["history"]
	var total := int(st["insn_total"])
	if history.size() > 0:
		total = int(history[-1]["total"])
	list.clear()
	for i in history.size():
		var h: Dictionary = history[i]
		list.add_item("%2d  %-16s %s" % [i, h["state"], h["phase"]])
		list.set_item_custom_fg_color(i, PHASE_COLORS.get(h["phase"], Color.WHITE))
	if st["in_insn"] and total > history.size():
		for i in range(history.size(), total):
			var idx := list.add_item("%2d  %s" % [i, "(next)" if i == history.size() else "…"])
			list.set_item_custom_fg_color(idx, DIM)
			list.set_item_selectable(idx, false)
	elif not st["in_insn"]:
		var idx := list.add_item("next: FETCH_ADDR  %s" % _asm_at(int(st["pc"])))
		list.set_item_custom_fg_color(idx, DIM)
		list.set_item_selectable(idx, false)

	if follow_latest or selected >= history.size():
		selected = history.size() - 1

	if history.size() > 0:
		var a := int(history[0]["insn_addr"])
		insn_label.text = "%04X:  %s" % [a, _asm_at(a)]
		progress_label.text = ("cycle %d of %s" % [selected + 1, str(total) if total > 0 else "?"]) if st["in_insn"] \
			else "completed in %d cycles — next instruction at %04X" % [history.size(), int(st["pc"])]
	else:
		insn_label.text = "%04X:  %s" % [int(st["pc"]), _asm_at(int(st["pc"]))]
		progress_label.text = "not started — press Step Cycle (F11) to begin its fetch"
	_show_selected()


func _on_item_selected(i: int) -> void:
	if i < history.size():
		selected = i
		follow_latest = selected == history.size() - 1
		_show_selected()


func _prev() -> void:
	if selected > 0:
		selected -= 1
		follow_latest = false
		_show_selected()


func _next() -> void:
	if selected < history.size() - 1:
		selected += 1
		follow_latest = selected == history.size() - 1
		_show_selected()
	else:
		follow_latest = true
		Backend.send("step_cycle")


func _show_selected() -> void:
	btn_prev.disabled = selected <= 0
	for p in phase_chips:
		var chip: Label = phase_chips[p]
		var csb: StyleBoxFlat = chip.get_theme_stylebox("normal")
		csb.bg_color = Color("2c313a")
		chip.add_theme_color_override("font_color", DIM)
	if selected < 0 or selected >= history.size():
		state_label.text = ""
		desc.text = "[color=#7f8794]No cycle executed yet for this instruction.[/color]"
		signals_text.text = ""
		changes_text.text = ""
		cycle_selected.emit({})
		return
	list.select(selected)
	var h: Dictionary = history[selected]
	var ph: String = h["phase"]
	if phase_chips.has(ph):
		var chip: Label = phase_chips[ph]
		var csb: StyleBoxFlat = chip.get_theme_stylebox("normal")
		csb.bg_color = PHASE_COLORS[ph].darkened(0.35)
		chip.add_theme_color_override("font_color", Color.WHITE)
	state_label.text = "%d · %s" % [selected, h["state"]]
	state_label.add_theme_color_override("font_color", PHASE_COLORS.get(ph, Color.WHITE))
	var d := "[b]%s[/b] phase.  %s" % [ph.capitalize(), _esc(h["desc"])]
	if h["bus"] != null:
		d += "\n[color=#7f8794]Bus carries[/color] [code]%s[/code]" % Backend.hex32(h["bus"])
	desc.text = d

	var sigs := ""
	for s in h["signals"]:
		sigs += "[bgcolor=#2f3845] [code]%s[/code] [/bgcolor]  " % _esc(s)
	signals_text.text = sigs if sigs != "" else "[color=#7f8794](none — the FSM only decodes)[/color]"

	var ch := ""
	for c in h["changes"]:
		var kind: String = c["kind"]
		var nm: String = c["name"]
		var o := int(c["old"])
		var n := int(c["new"])
		var line := ""
		match kind:
			"flag":
				line = "flag [b]%s[/b]: %d → %d" % [nm, o, n]
			"mem":
				var w := int(c["width"])
				line = "memory %s (%d byte%s): %s → [b]%s[/b]" % [nm, w, "" if w == 1 else "s",
					_hexw(o, w), _hexw(n, w)]
			"io":
				line = "I/O write %s ← [b]%s[/b]" % [nm, Backend.hex32(n)]
			"datapath":
				line = "[color=#9da5b4]%s[/color]: %s → [b]%s[/b]" % [nm, _hexn(nm, o), _hexn(nm, n)]
			_:
				line = "register [b]%s[/b]: %s → [b]%s[/b]" % [nm, Backend.hex32(o), Backend.hex32(n)]
				if nm == "PC" and h["phase"] != "FETCH":
					line += "  [color=#e06c75](control transfer)[/color]"
		ch += "• " + line + "\n"
	if h["branch"] != null:
		ch += "• [b]%s[/b]\n" % ("branch taken" if h["branch"] else "branch not taken — PC stays sequential")
	changes_text.text = ch if ch != "" else "[color=#7f8794](no architectural or datapath state changed)[/color]"
	cycle_selected.emit(h)


func _hexw(v: int, w: int) -> String:
	return ("0x%0" + str(w * 2) + "X") % v


func _hexn(nm: String, v: int) -> String:
	return Backend.hex16(v) if nm.begins_with("IR") else Backend.hex32(v)


func _esc(s: String) -> String:
	return s.replace("[", "[lb]").replace("<-", "←")
