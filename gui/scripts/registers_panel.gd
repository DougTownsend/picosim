extends PanelContainer
## R0–R12, SP, LR, PC, the NZCV flags, the datapath's internal registers
## (MAR, MDR, IR, IR2) and cycle counters.  Values changed by the last step
## are highlighted; register values and flags can be edited in place.
## Text grows with the panel: drag the splitters to give it more room and the
## fonts and fields scale up to fill it.
## Registers can be shown in hex, unsigned decimal or signed decimal (saved in
## settings.cfg as [ui] reg_format). Below level 3 the datapath registers and
## the FSM's next state are hidden: they belong to "Inside the CPU".

const NAMES := ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
	"R8", "R9", "R10", "R11", "R12", "SP", "LR", "PC"]
const DP_NAMES := ["MAR", "MDR", "IR", "IR2"]
const FORMATS := ["Hex", "Decimal", "Signed"]
const SETTINGS := "user://settings.cfg"

var reg_edits: Array[LineEdit] = []
var _dim_labels: Array[Label] = []
var flag_boxes := {}
var dp_labels := {}
var info_label: Label
var dp_row: HBoxContainer
var format_select: OptionButton
var reg_format := 0              # index into FORMATS
var font: Font
var holder: Control
var content: VBoxContainer
var base_size := Vector2.ZERO    # content's minimum size at scale 1
var ui_scale := 1.0
var _fonts: Array = []           # [control, theme item, base size]
var _widths: Array = []          # [control, base minimum width]


func _ready() -> void:
	font = Backend.mono_font()

	# A plain Control between the panel and the content: it keeps the panel's
	# minimum size at the unscaled layout, so a scaled-up panel can still be
	# dragged smaller again.
	holder = Control.new()
	holder.clip_contents = true
	add_child(holder)
	var v := VBoxContainer.new()
	content = v
	holder.add_child(v)
	var top := HBoxContainer.new()
	v.add_child(top)
	var title := Label.new()
	title.text = "Registers"
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_font(title, 15)
	top.add_child(title)
	var cfg := ConfigFile.new()
	if cfg.load(SETTINGS) == OK:
		reg_format = clampi(int(cfg.get_value("ui", "reg_format", 0)), 0, FORMATS.size() - 1)
	format_select = OptionButton.new()
	for f in FORMATS:
		format_select.add_item(f)
	format_select.select(reg_format)
	format_select.focus_mode = Control.FOCUS_NONE
	format_select.tooltip_text = "Show the registers as:\nHex — base 16, like 0x0000002A\nDecimal — an ordinary number, like 42\nSigned — decimal, where values from 0x80000000 up are negative"
	format_select.item_selected.connect(_on_format)
	_font(format_select, 13)
	top.add_child(format_select)

	var grid := GridContainer.new()
	grid.columns = 8
	grid.add_theme_constant_override("h_separation", 6)
	grid.add_theme_constant_override("v_separation", 4)
	v.add_child(grid)
	for i in 16:
		var l := Label.new()
		l.text = NAMES[i]
		_width(l, 34)
		l.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		l.add_theme_font_override("font", font)
		_font(l, 14)
		_dim_labels.append(l)
		grid.add_child(l)
		var e := LineEdit.new()
		e.text = "0x00000000"
		_width(e, 104)
		e.add_theme_font_override("font", font)
		_font(e, 14)
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
	_dim_labels.append(fl)
	_font(fl, 14)
	row.add_child(fl)
	for f in ["N", "Z", "C", "V"]:
		var cb := CheckBox.new()
		cb.text = f
		cb.focus_mode = Control.FOCUS_NONE
		_font(cb, 14)
		cb.tooltip_text = {
			"N": "N — Negative: the last result was negative (its top bit, bit 31, is 1)",
			"Z": "Z — Zero: the last result was zero",
			"C": "C — Carry: an add carried out of bit 31, or a subtract (CMP) did not need to borrow",
			"V": "V — Overflow: the last signed result was too big to fit in 32 bits"}[f] + \
			"\nSet by instructions ending in S (ADDS, SUBS, MOVS…) and by CMP. Click to change it."
		cb.toggled.connect(func(on): Backend.send("set_flag", {"flag": f, "value": 1 if on else 0}))
		row.add_child(cb)
		flag_boxes[f] = cb

	var dp := HBoxContainer.new()
	dp_row = dp
	dp.add_theme_constant_override("separation", 14)
	v.add_child(dp)
	for n in DP_NAMES:
		var l := Label.new()
		l.add_theme_font_override("font", font)
		_font(l, 13)
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
	_font(info_label, 13)
	_dim_labels.append(info_label)
	v.add_child(info_label)

	holder.resized.connect(_fit)
	set_level(Level.current)
	Level.changed.connect(func(): set_level(Level.current))
	_apply_palette()
	Palette.changed.connect(_apply_palette)


## Show the datapath row and the FSM state from level 3 (Inside the CPU).
func set_level(n: int) -> void:
	dp_row.visible = n >= 3
	_measure()
	update_state(Backend.state)
	_fit()


## Measure the layout at scale 1 with placeholder text in the long rows.
func _measure() -> void:
	for f in _fonts:
		f[0].add_theme_font_size_override("font_size", f[1])
	for w in _widths:
		w[0].custom_minimum_size.x = w[1]
	var old_info := info_label.text
	for n in DP_NAMES:
		dp_labels[n].text = "%s 0x00000000" % n
	info_label.text = "Next state: FETCH_ADDR (at instruction boundary)\nInstructions: 0   Cycles: 0"
	info_label.custom_minimum_size.y = 36
	base_size = content.get_combined_minimum_size()
	info_label.text = old_info
	holder.custom_minimum_size = base_size
	ui_scale = -1.0   # make _fit apply the scale again


func _apply_palette() -> void:
	var sb := Palette.panel_box("panel", 6, 8)
	sb.content_margin_left = 10
	sb.content_margin_right = 10
	add_theme_stylebox_override("panel", sb)
	for l in _dim_labels:
		l.add_theme_color_override("font_color", Palette.c("dim"))
	update_state(Backend.state)


func _font(c: Control, base: int) -> void:
	c.add_theme_font_size_override("font_size", base)
	_fonts.append([c, base])


func _width(c: Control, base: int) -> void:
	c.custom_minimum_size.x = base
	_widths.append([c, base])


## Scale the content to the largest size that still fits the holder.
func _fit() -> void:
	var avail := holder.size
	var s := clampf(minf(avail.x / base_size.x, avail.y / base_size.y), 1.0, 4.0)
	if absf(s - ui_scale) > 0.02:
		ui_scale = s
		for f in _fonts:
			f[0].add_theme_font_size_override("font_size", int(round(f[1] * s)))
		for w in _widths:
			w[0].custom_minimum_size.x = w[1] * s
		info_label.custom_minimum_size.y = 36 * s
	content.position = Vector2.ZERO
	content.size = Vector2(avail.x, maxf(avail.y, content.get_combined_minimum_size().y))


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
		reg_edits[i].text = _fmt(int(st["regs"][i]))


## A register value in the chosen format.
func _fmt(val: int) -> String:
	match reg_format:
		1:
			return str(val)
		2:
			return str(val - 0x100000000 if val >= 0x80000000 else val)
	return Backend.hex32(val)


## "0000 0000 0000 0000 0000 0000 0010 1010"
static func _binary(val: int) -> String:
	var out := ""
	for k in range(7, -1, -1):
		var nib := (val >> (k * 4)) & 0xF
		for b in range(3, -1, -1):
			out += "1" if nib & (1 << b) else "0"
		if k > 0:
			out += " "
	return out


func _on_format(i: int) -> void:
	reg_format = i
	var cfg := ConfigFile.new()
	cfg.load(SETTINGS)
	cfg.set_value("ui", "reg_format", i)
	cfg.save(SETTINGS)
	update_state(Backend.state)


func update_state(st: Dictionary) -> void:
	if not st.get("loaded", false):
		return
	var changed: Array = st.get("changed", [])
	for i in 16:
		var e := reg_edits[i]
		if not e.has_focus():
			e.text = _fmt(int(st["regs"][i]))
		var hot := changed.has(NAMES[i])
		e.add_theme_color_override("font_color", Palette.c("changed") if hot else Palette.c("text"))
		var val := int(st["regs"][i])
		var sval := val - 0x100000000 if val >= 0x80000000 else val
		e.tooltip_text = "%s = %s\n= %d (as a signed number: %d)\nbinary %s\nType a new value (0x… for hex, or decimal) and press Enter" % [
			NAMES[i], Backend.hex32(val), val, sval, _binary(val)]
	var flags: Dictionary = st["flags"]
	for f in flag_boxes:
		var cb: CheckBox = flag_boxes[f]
		cb.set_pressed_no_signal(int(flags[f]) != 0)
		var fc := Palette.c("changed") if changed.has(f) else Palette.c("text")
		cb.add_theme_color_override("font_color", fc)
		cb.add_theme_color_override("font_pressed_color", fc)
	var dpv: Dictionary = st["datapath"]
	for n in DP_NAMES:
		var l: Label = dp_labels[n]
		var fmt := "%s %s" % [n, Backend.hex16(dpv[n]) if n.begins_with("IR") else Backend.hex32(dpv[n])]
		l.text = fmt
		l.add_theme_color_override("font_color", Palette.c("changed") if changed.has(n) else Palette.c("dim"))
	var state_txt: String = st["next_state"]
	var where := ""
	if st["in_insn"]:
		where = "cycle %d of %s" % [int(st["cycle_index"]), "?" if int(st["insn_total"]) < 0 else str(int(st["insn_total"]))]
	else:
		where = "at instruction boundary"
	if Level.at_least(3):
		info_label.text = "Next state: %s (%s)\nInstructions: %d   Cycles: %d" % [
			state_txt, where, int(st["steps"]), int(st["cycles"])]
	else:
		info_label.text = "Instructions run so far: %d" % int(st["steps"])
	info_label.tooltip_text = info_label.text
