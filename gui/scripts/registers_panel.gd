extends PanelContainer
## R0–R12, SP, LR, PC, the NZCV flags, the datapath's internal registers
## (MAR, MDR, IR, IR2) and cycle counters.  Values changed by the last step
## are highlighted; register values and flags can be edited in place.
## Text grows with the panel: drag the splitters to give it more room and the
## fonts and fields scale up to fill it.
## Registers can be shown in hex, unsigned decimal or signed decimal (saved in
## settings.cfg as [ui] reg_format; until the student picks one, level 1 shows
## decimal and higher levels hex). SP, LR and PC hold addresses, so they stay
## in hex like every other address in picosim. Under the flags, a line says what the
## last step changed and which line of the program did it.  Below level 3 the datapath registers and
## the FSM's next state are hidden: they belong to "Inside the CPU".

const HelpButton := preload("res://scripts/help_button.gd")

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
var title_label: Label
var change_label: Label
var _rows := {}                  # address -> Disassembly row, for "changed by"
var dp_row: HBoxContainer
var format_select: OptionButton
var reg_format := 0              # index into FORMATS
var _format_chosen := false      # the student picked one; otherwise it follows the level
var font: Font
var holder: Control
var content: VBoxContainer
var base_size := Vector2.ZERO    # content's minimum size at scale 1
var ui_scale := 1.0
var _fonts: Array = []           # [control, theme item, base size]
var _widths: Array = []          # [control, base minimum width]
var _tip_keys := []              # per register: what its tooltip was last built from


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
	title_label = title
	title.text = "Registers"
	title.mouse_filter = Control.MOUSE_FILTER_PASS
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_font(title, 15)
	top.add_child(title)
	var cfg := ConfigFile.new()
	if cfg.load(SETTINGS) == OK and cfg.has_section_key("ui", "reg_format"):
		reg_format = clampi(int(cfg.get_value("ui", "reg_format", 0)), 0, FORMATS.size() - 1)
		_format_chosen = true
	else:
		reg_format = _level_format()
	Level.changed.connect(func():
		if not _format_chosen:
			reg_format = _level_format()
			format_select.select(reg_format)
		update_state(Backend.state))
	format_select = OptionButton.new()
	for f in FORMATS:
		format_select.add_item(f)
	format_select.select(reg_format)
	format_select.focus_mode = Control.FOCUS_NONE
	format_select.tooltip_text = "Show the registers as:\nHex — base 16, like 0x0000002A\nDecimal — an ordinary number, like 42\nSigned — decimal, where values from 0x80000000 up are negative\nSP, LR and PC hold addresses, so they are always in hex."
	format_select.item_selected.connect(_on_format)
	_font(format_select, 14)
	top.add_child(format_select)
	top.add_child(HelpButton.new("registers", "Registers"))

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
			"V": "V — Overflow: the last result, read as a signed number, did not fit in 32 bits (its sign came out wrong)"}[f] + \
			"\nSet by instructions ending in S (ADDS, SUBS, MOVS…) and by CMP. Click to change it."
		cb.toggled.connect(func(on): Backend.send("set_flag", {"flag": f, "value": 1 if on else 0}))
		row.add_child(cb)
		flag_boxes[f] = cb
	row.add_child(HelpButton.new("branches", "Flags and branches"))

	change_label = Label.new()
	change_label.clip_text = true
	change_label.custom_minimum_size.y = 36
	change_label.mouse_filter = Control.MOUSE_FILTER_PASS
	change_label.add_theme_font_override("font", font)
	_font(change_label, 14)
	v.add_child(change_label)

	var dp := HBoxContainer.new()
	dp_row = dp
	dp.add_theme_constant_override("separation", 14)
	v.add_child(dp)
	for n in DP_NAMES:
		var l := Label.new()
		l.add_theme_font_override("font", font)
		_font(l, 14)
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
	_font(info_label, 14)
	_dim_labels.append(info_label)
	v.add_child(info_label)

	holder.resized.connect(_fit)
	Backend.program_loaded.connect(_on_program)
	_on_program(Backend.program)
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
	change_label.custom_minimum_size.y = 36
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
		change_label.custom_minimum_size.y = 36 * s
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
		reg_edits[i].text = _fmt(int(st["regs"][i]), i)


## Decimal at level 1, where the numbers are small; hex from level 2, where
## addresses and memory come in.
static func _level_format() -> int:
	return 1 if Level.current < 2 else 0


## Register `i`'s value in the chosen format (addresses: SP, LR, PC in hex).
func _fmt(val: int, i := 0) -> String:
	if i >= 13:
		return Backend.hex32(val)
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
	_format_chosen = true
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
		var val := int(st["regs"][i])
		if not e.has_focus():
			Backend.set_text(e, _fmt(val, i))
		var hot := changed.has(NAMES[i])
		# R8–R12: most Thumb instructions cannot use them, so they stay dim until they hold something
		var idle := i >= 8 and i <= 12 and val == 0
		Backend.set_color(e, "font_color", Palette.c("changed") if hot else Palette.c("dim") if idle else Palette.c("text"))
		var cause := _cause(NAMES[i], st) if hot else ""
		var key := [val, cause]
		_tip_keys.resize(16)
		if _tip_keys[i] != key:   # the tooltip only changes with these
			_tip_keys[i] = key
			var sval := val - 0x100000000 if val >= 0x80000000 else val
			e.tooltip_text = "%s = %s\n= %d (as a signed number: %d)%s\nbinary %s\n%sType a new value (0x… for hex, or decimal) and press Enter" % [
				NAMES[i], Backend.hex32(val), val, sval, _char_note(val), _binary(val),
				("Changed by " + cause + "\n") if hot else ""]
	var flags: Dictionary = st["flags"]
	for f in flag_boxes:
		var cb: CheckBox = flag_boxes[f]
		cb.set_pressed_no_signal(int(flags[f]) != 0)
		var fc := Palette.c("changed") if changed.has(f) else Palette.c("text")
		Backend.set_color(cb, "font_color", fc)
		Backend.set_color(cb, "font_pressed_color", fc)
	var dpv: Dictionary = st["datapath"]
	for n in DP_NAMES:
		var l: Label = dp_labels[n]
		Backend.set_text(l, "%s %s" % [n, Backend.hex16(dpv[n]) if n.begins_with("IR") else Backend.hex32(dpv[n])])
		Backend.set_color(l, "font_color", Palette.c("changed") if changed.has(n) else Palette.c("dim"))
	_show_changes(st)
	var state_txt: String = st["next_state"]
	var where := ""
	if st["in_insn"]:
		where = ("cycle %d; the total is known after DECODE" % int(st["cycle_index"])) if int(st["insn_total"]) < 0 \
			else "cycle %d of %d" % [int(st["cycle_index"]), int(st["insn_total"])]
	else:
		where = "at instruction boundary"
	if Level.at_least(3):
		Backend.set_text(info_label, "Next state: %s (%s)\nInstructions: %d   Cycles: %d" % [
			state_txt, where, int(st["steps"]), int(st["cycles"])])
	info_label.visible = Level.at_least(3)    # below that, the toolbar already counts instructions
	# Partway through an instruction the CPU diagram shows each box's value
	# during the cycle (old → new for what the edge loads); say that this
	# panel is the "after" side, so the two never seem to disagree.
	var mid: bool = Level.at_least(3) and st["in_insn"]
	Backend.set_text(title_label, "Registers · after this cycle's clock edge" if mid else "Registers")
	title_label.tooltip_text = "These are the values after the clock edge that ended the last cycle.\nThe CPU diagram shows what each box held during that cycle, and old → new for what the edge loaded." if mid else ""
	info_label.tooltip_text = info_label.text


func _on_program(prog: Dictionary) -> void:
	_rows.clear()
	for row in prog.get("listing", []):
		_rows[int(row["addr"])] = row


## "   'H'" for a value that is a printable character.
static func _char_note(val: int) -> String:
	if val >= 32 and val < 127:
		return "   = the character '%s'" % char(val)
	if val == 10:
		return "   = the new line character"
	return ""


## What changed register `name`: an instruction of the program (with its
## source line) or, when no single instruction did, the user's action.
func _cause(name: String, st: Dictionary) -> String:
	var by: Dictionary = st.get("changed_by", {})
	if by.has(name):
		var addr := int(by[name])
		var row: Dictionary = _rows.get(addr, {})
		var text := str(row.get("shown", row.get("text", "?"))).strip_edges()
		text = " ".join(text.split(" ", false))
		var line := int(row.get("line", 0))
		if line > 0 and addr >= 0x3000:
			# the line as the student wrote it, without its comment
			var src := str(row.get("source", "")).get_slice("@", 0).get_slice("/*", 0).strip_edges()
			return "%s   (line %d)" % [src if src != "" else text, line]
		return "%s   (at %04X, the built-in OS)" % [text, addr]
	match str(st.get("action", "")):
		"call": return "the function call (Step over)"
		"run": return "running the program"
		"back": return "Step back"
		"edit": return "your edit"
	return "the last step"


## A changed register's value in a few forms: decimal (signed if negative)
## and the character for R0–R12; hex for SP, LR and PC (they hold addresses).
func _value_note(i: int, val: int) -> String:
	if i >= 13:
		return Backend.hex32(val)
	var t := str(val)
	if val >= 0x80000000:
		t = "%d (signed)" % (val - 0x100000000)
	elif val > 9:
		t += " = 0x%X" % val
	if val >= 32 and val < 127:
		t += " = '%s'" % char(val)
	elif val == 10:
		t += " = new line"
	return t


## The line under the flags: each register the last step changed (not the
## PC, which every step changes), its new value and what changed it.
func _show_changes(st: Dictionary) -> void:
	var changed: Array = st.get("changed", [])
	var lines: Array[String] = []
	var more := 0
	for i in 15:
		if not changed.has(NAMES[i]):
			continue
		if lines.size() == 2:
			more += 1
			continue
		lines.append("%s = %s   ← %s" % [NAMES[i], _value_note(i, int(st["regs"][i])), _cause(NAMES[i], st)])
	if more > 0:
		lines[1] += "   (+%d more)" % more
	if st.get("running", false) and str(st.get("action", "")) == "run":
		lines.clear()
	Backend.set_text(change_label, "\n".join(lines))
	change_label.tooltip_text = change_label.text
	Backend.set_color(change_label, "font_color", Palette.c("changed"))
