extends PanelContainer
## R0–R12, SP, LR, PC, the NZCV flags, the datapath's internal registers
## (MAR, MDR, IR, IR2) and cycle counters.  Values changed by the last step
## are highlighted; register values and flags can be edited in place.

const NAMES := ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
	"R8", "R9", "R10", "R11", "R12", "SP", "LR", "PC"]
const DP_NAMES := ["MAR", "MDR", "IR", "IR2"]
const HILITE := Color("f2cc60")
const NORMAL := Color("d7dae0")
const DIM := Color("7f8794")

var reg_edits: Array[LineEdit] = []
var flag_boxes := {}
var dp_labels := {}
var info_label: Label
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

	var v := VBoxContainer.new()
	add_child(v)
	var title := Label.new()
	title.text = "Registers"
	title.add_theme_font_size_override("font_size", 15)
	v.add_child(title)

	var grid := GridContainer.new()
	grid.columns = 8
	grid.add_theme_constant_override("h_separation", 6)
	grid.add_theme_constant_override("v_separation", 4)
	v.add_child(grid)
	for i in 16:
		var l := Label.new()
		l.text = NAMES[i]
		l.custom_minimum_size.x = 34
		l.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		l.add_theme_font_override("font", font)
		l.add_theme_color_override("font_color", DIM)
		grid.add_child(l)
		var e := LineEdit.new()
		e.text = "0x00000000"
		e.custom_minimum_size.x = 104
		e.add_theme_font_override("font", font)
		e.add_theme_font_size_override("font_size", 14)
		e.tooltip_text = "%s — type a hex (0x…) or decimal value and press Enter" % NAMES[i]
		e.text_submitted.connect(_on_reg_submitted.bind(i))
		e.focus_exited.connect(func(): _refresh_reg(i))
		grid.add_child(e)
		reg_edits.append(e)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 14)
	v.add_child(row)
	var fl := Label.new()
	fl.text = "Flags"
	fl.add_theme_color_override("font_color", DIM)
	row.add_child(fl)
	for f in ["N", "Z", "C", "V"]:
		var cb := CheckBox.new()
		cb.text = f
		cb.focus_mode = Control.FOCUS_NONE
		cb.tooltip_text = {"N": "Negative", "Z": "Zero", "C": "Carry", "V": "Overflow"}[f]
		cb.toggled.connect(func(on): Backend.send("set_flag", {"flag": f, "value": 1 if on else 0}))
		row.add_child(cb)
		flag_boxes[f] = cb

	var dp := HBoxContainer.new()
	dp.add_theme_constant_override("separation", 14)
	v.add_child(dp)
	for n in DP_NAMES:
		var l := Label.new()
		l.add_theme_font_override("font", font)
		l.add_theme_font_size_override("font_size", 13)
		l.tooltip_text = {"MAR": "Memory Address Register", "MDR": "Memory Data Register",
			"IR": "Instruction Register", "IR2": "Second instruction halfword (32-bit encodings)"}[n]
		l.mouse_filter = Control.MOUSE_FILTER_PASS
		dp.add_child(l)
		dp_labels[n] = l

	info_label = Label.new()
	info_label.clip_text = true
	info_label.custom_minimum_size.y = 36
	info_label.mouse_filter = Control.MOUSE_FILTER_PASS
	info_label.add_theme_font_override("font", font)
	info_label.add_theme_font_size_override("font_size", 13)
	info_label.add_theme_color_override("font_color", DIM)
	v.add_child(info_label)


func _on_reg_submitted(text: String, i: int) -> void:
	var v = Backend.parse_number(text)
	if v == null:
		_refresh_reg(i)
		return
	Backend.send("set_reg", {"reg": i, "value": v})
	reg_edits[i].release_focus()


func _refresh_reg(i: int) -> void:
	var st := Backend.state
	if st.get("loaded", false):
		reg_edits[i].text = Backend.hex32(st["regs"][i])


func update_state(st: Dictionary) -> void:
	if not st.get("loaded", false):
		return
	var changed: Array = st.get("changed", [])
	for i in 16:
		var e := reg_edits[i]
		if not e.has_focus():
			e.text = Backend.hex32(st["regs"][i])
		var hot := changed.has(NAMES[i])
		e.add_theme_color_override("font_color", HILITE if hot else NORMAL)
		var val := int(st["regs"][i])
		var sval := val - 0x100000000 if val >= 0x80000000 else val
		e.tooltip_text = "%s = %d (unsigned %d)\nType a new value and press Enter" % [NAMES[i], sval, val]
	var flags: Dictionary = st["flags"]
	for f in flag_boxes:
		var cb: CheckBox = flag_boxes[f]
		cb.set_pressed_no_signal(int(flags[f]) != 0)
		cb.add_theme_color_override("font_color", HILITE if changed.has(f) else NORMAL)
		cb.add_theme_color_override("font_pressed_color", HILITE if changed.has(f) else NORMAL)
	var dpv: Dictionary = st["datapath"]
	for n in DP_NAMES:
		var l: Label = dp_labels[n]
		var fmt := "%s %s" % [n, Backend.hex16(dpv[n]) if n.begins_with("IR") else Backend.hex32(dpv[n])]
		l.text = fmt
		l.add_theme_color_override("font_color", HILITE if changed.has(n) else DIM)
	var state_txt: String = st["next_state"]
	var where := ""
	if st["in_insn"]:
		where = "cycle %d of %s" % [int(st["cycle_index"]), "?" if int(st["insn_total"]) < 0 else str(int(st["insn_total"]))]
	else:
		where = "at instruction boundary"
	info_label.text = "Next state: %s (%s)\nInstructions: %d   Cycles: %d" % [
		state_txt, where, int(st["steps"]), int(st["cycles"])]
	info_label.tooltip_text = info_label.text
