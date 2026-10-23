extends VBoxContainer
## The Memory tab: memory as a place students can see and explore.
##
##   Map      — the whole address space as a strip: the built-in OS, your code,
##              your data, free RAM, the stack (growing down from the top) and
##              the I/O registers, with PC and SP marked.  Click a part to go there.
##   Bytes    — 256 bytes, 16 to a row, each coloured by what it is (an
##              instruction, a literal or string, data, the stack, free).
##              Labels sit above the bytes they name, and registers that hold
##              an address (pointers) are tagged where they point.  The bytes
##              the last step read are outlined in blue, the ones it wrote are
##              yellow.  Show as Bytes, Words (4 bytes put together, lowest
##              first) or Characters.
##   Stack    — the words from SP up to the top of RAM as boxes, newest at the
##              bottom, each saying what put it there (which register a PUSH
##              saved, or a return address), grouped by function.  Words the
##              last step popped are shown faded just below SP.
##   Last step — one line on what the last instruction did to memory.
##   Inspector — hover a byte to see everything about it; click one to change it.
##
## Everything is drawn by three custom Controls that redraw only when a new
## state arrives or the mouse moves over them, never every frame.

const RAM_TOP := 0x10000
const OS_TOP := 0x3000
const ROW := 16
const MODES := ["Bytes", "Words", "Characters"]
const SETTINGS := "user://settings.cfg"

## what a byte is: [name, palette colour, tint alpha, words for the inspector]
enum { K_FREE, K_OS_CODE, K_OS, K_INSN, K_LIT, K_DATA, K_BSS, K_STACK, K_POINTER }
const KINDS := {
	K_FREE: ["free RAM", "", 0.0, "free RAM: nothing of the program is stored here"],
	K_OS_CODE: ["OS code", "cyan", 0.10, "an instruction of picosim's built-in OS"],
	K_OS: ["OS", "", 0.0, "the built-in OS's part of memory (unused here)"],
	K_INSN: ["instruction", "cyan", 0.20, "part of an instruction of your program"],
	K_LIT: ["literal / string", "purple", 0.22, "data stored with your code: a literal-pool number or a string"],
	K_DATA: ["data", "green", 0.20, "your program's data (after .data)"],
	K_BSS: ["zeroed data", "green", 0.12, "your program's zero-filled data (.bss)"],
	K_STACK: ["stack", "sel", 0.20, "the stack: between SP and the top of RAM"],
	K_POINTER: ["main's address", "yellow", 0.16, "where the OS keeps the address of your main"],
}

var font: Font
var mono: Font
var st: Dictionary = {}
var prog: Dictionary = {}
var kinds := PackedByteArray()      # 64 KB of K_* codes (the stack is added when drawn)
var label_list: Array = []          # [[addr, name]], sorted, without OS-internal names
var labels_at := {}                 # addr → [names]
var rows_by_addr := {}              # listing row address → row
var insn_of := {}                   # every byte of an instruction / data row → its row address
var mode := 0
var follow := true
var hover := -1
var selected := -1
var text_scale := 1.0
var view_addr := 0x3000             # the 256-byte window the server sends
var data := PackedByteArray()
var _key = null                     # what the drawings were last built from

var map: Control
var grid: Control
var stack: Control
var banner: RichTextLabel
var inspector: RichTextLabel
var goto_edit: LineEdit
var mode_select: OptionButton
var follow_box: CheckBox
var edit_field: LineEdit
var edit_btn: Button
var _dim: Array = []


# ── building ────────────────────────────────────────────────────────────────

func _ready() -> void:
	font = get_theme_default_font()
	mono = Backend.mono_font()
	add_theme_constant_override("separation", 6)
	var cfg := ConfigFile.new()
	if cfg.load(SETTINGS) == OK:
		mode = clampi(int(cfg.get_value("ui", "memory_mode", 0)), 0, MODES.size() - 1)
		follow = bool(cfg.get_value("ui", "memory_follow", true))
	var args := OS.get_cmdline_user_args()
	var mi := args.find("--memory-mode")        # screenshots: 0 bytes, 1 words, 2 characters
	if mi >= 0 and mi + 1 < args.size():
		mode = clampi(int(args[mi + 1]), 0, MODES.size() - 1)
	var hi := args.find("--memory-hover")       # screenshots: the inspector for one byte
	if hi >= 0 and hi + 1 < args.size():
		selected = int(Backend.parse_number(args[hi + 1]))

	var top := HFlowContainer.new()     # wraps rather than widening the panel
	top.add_theme_constant_override("h_separation", 6)
	top.add_theme_constant_override("v_separation", 4)
	add_child(top)
	top.add_child(_label("Go to"))
	goto_edit = LineEdit.new()
	goto_edit.placeholder_text = "address or label"
	goto_edit.custom_minimum_size.x = 130
	goto_edit.add_theme_font_override("font", mono)
	goto_edit.tooltip_text = "Type an address (like 0x3000) or a label from your program and press Enter"
	goto_edit.text_submitted.connect(_on_goto)
	top.add_child(goto_edit)
	for spec in [["PC", "pc", "The instruction that runs next"], ["SP", "sp", "The top of the stack"],
			["main", "main", "The start of your program"]]:
		var b := Button.new()
		b.text = spec[0]
		b.tooltip_text = spec[2]
		b.focus_mode = Control.FOCUS_NONE
		b.pressed.connect(_goto_special.bind(spec[1]))
		top.add_child(b)
	top.add_child(_label("   Show"))
	mode_select = OptionButton.new()
	for m in MODES:
		mode_select.add_item(m)
	mode_select.select(mode)
	mode_select.focus_mode = Control.FOCUS_NONE
	mode_select.tooltip_text = "Bytes — every byte in hex\nWords — 4 bytes put together into one number, lowest byte first (little-endian)\nCharacters — each byte as the character it codes"
	mode_select.item_selected.connect(_on_mode)
	top.add_child(mode_select)
	follow_box = CheckBox.new()
	follow_box.text = "Follow loads and stores"
	follow_box.button_pressed = follow
	follow_box.focus_mode = Control.FOCUS_NONE
	follow_box.tooltip_text = "After each step, show the bytes it read or wrote"
	follow_box.toggled.connect(_on_follow)
	top.add_child(follow_box)

	banner = RichTextLabel.new()
	banner.bbcode_enabled = true
	banner.fit_content = true
	banner.scroll_active = false
	banner.selection_enabled = true
	banner.add_theme_font_override("mono_font", mono)
	add_child(banner)

	var body := HBoxContainer.new()
	body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_theme_constant_override("separation", 8)
	add_child(body)
	map = Part.new()
	map.view = self
	map.which = "map"
	map.custom_minimum_size = Vector2(112, 160)
	map.tooltip_text = " "
	body.add_child(map)
	grid = Part.new()
	grid.view = self
	grid.which = "grid"
	grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	grid.custom_minimum_size = Vector2(200, 160)
	grid.size_flags_stretch_ratio = 2.4
	body.add_child(grid)
	stack = Part.new()
	stack.view = self
	stack.which = "stack"
	stack.custom_minimum_size = Vector2(150, 160)
	stack.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_child(stack)

	var ins := HBoxContainer.new()
	ins.add_theme_constant_override("separation", 6)
	add_child(ins)
	inspector = RichTextLabel.new()
	inspector.bbcode_enabled = true
	inspector.fit_content = true
	inspector.scroll_active = false
	inspector.selection_enabled = true
	inspector.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	inspector.custom_minimum_size = Vector2(120, 48)
	inspector.add_theme_font_override("mono_font", mono)
	ins.add_child(inspector)
	edit_field = LineEdit.new()
	edit_field.placeholder_text = "new bytes"
	edit_field.custom_minimum_size.x = 130
	edit_field.add_theme_font_override("font", mono)
	edit_field.tooltip_text = "Click a byte, type new bytes in hex (separated by spaces) and press Enter to write them there"
	edit_field.text_submitted.connect(func(_t): _write())
	edit_field.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	ins.add_child(edit_field)
	edit_btn = Button.new()
	edit_btn.text = "Write"
	edit_btn.tooltip_text = "Write the bytes at the selected address"
	edit_btn.pressed.connect(_write)
	edit_btn.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	ins.add_child(edit_btn)

	_apply_text_scale()
	_apply_palette()
	Palette.changed.connect(_apply_palette)
	Level.changed.connect(func(): _key = null; update_state(Backend.state))
	_show_inspector()


func _label(t: String) -> Label:
	var l := Label.new()
	l.text = t
	_dim.append(l)
	return l


## One of the three drawings; it hands its drawing and its mouse to the view.
class Part extends Control:
	var view
	var which := ""

	func _draw() -> void:
		view.call("_draw_" + which, self)

	func _gui_input(e: InputEvent) -> void:
		view.call("_input_" + which, self, e)

	func _get_tooltip(at: Vector2) -> String:
		return view.call("_tip_" + which, self, at)

	func _notification(what: int) -> void:
		if what == NOTIFICATION_MOUSE_EXIT and which == "grid":
			view._set_hover(-1)


func _apply_palette() -> void:
	for l in _dim:
		l.add_theme_color_override("font_color", Palette.c("dim"))
	_key = null
	_redraw_all()
	_show_banner()
	_show_inspector()


## The text grows when the panel is dragged bigger (main.gd's text scaling).
func set_text_scale(s: float) -> void:
	text_scale = s
	_apply_text_scale()
	_redraw_all()


func _apply_text_scale() -> void:
	var s := text_scale
	for c in [goto_edit, mode_select, follow_box, edit_field, edit_btn]:
		c.add_theme_font_size_override("font_size", roundi(14 * s))
	for l in _dim:
		l.add_theme_font_size_override("font_size", roundi(14 * s))
	for r in [banner, inspector]:
		r.add_theme_font_size_override("normal_font_size", roundi(14 * s))
		r.add_theme_font_size_override("bold_font_size", roundi(14 * s))
		r.add_theme_font_size_override("mono_font_size", roundi(14 * s))



func _redraw_all() -> void:
	for p in [map, grid, stack]:
		if p:
			p.queue_redraw()


# ── data from the backend ───────────────────────────────────────────────────

func set_program(p: Dictionary) -> void:
	prog = p
	kinds = PackedByteArray()
	kinds.resize(RAM_TOP)                   # all K_FREE (0)
	var reg: Dictionary = p.get("regions", {})
	for r in reg.get("runs", []):
		var k := K_INSN if str(r[2]) == "insn" else K_LIT
		if int(r[0]) < OS_TOP:
			k = K_OS_CODE if k == K_INSN else K_LIT
		for a in range(int(r[0]), mini(int(r[1]), RAM_TOP)):
			kinds[a] = k
	var osend := int(reg.get("os_code_end", 0))
	for a in range(osend, OS_TOP):
		if kinds[a] == K_FREE:
			kinds[a] = K_OS
	for a in range(0x2FFC, OS_TOP):
		kinds[a] = K_POINTER
	var secs: Dictionary = reg.get("sections", {})
	for name in ["data", "bss"]:
		if secs.has(name):
			for a in range(int(secs[name][0]), mini(int(secs[name][1]), RAM_TOP)):
				kinds[a] = K_DATA if name == "data" else K_BSS
	label_list.clear()
	labels_at.clear()
	var labs: Dictionary = p.get("labels", {})
	for name in labs:
		var n := str(name)
		if n.begins_with(".") or n == "_end" or n.begins_with("$"):
			continue
		var a := int(labs[name])
		if a < 0 or a >= RAM_TOP:
			continue
		label_list.append([a, n])
		labels_at[a] = labels_at.get(a, []) + [n]
	label_list.sort_custom(func(x, y): return x[0] < y[0])
	rows_by_addr.clear()
	insn_of.clear()
	var rows: Array = p.get("listing", [])
	for i in rows.size():
		var row: Dictionary = rows[i]
		var a := int(row["addr"])
		rows_by_addr[a] = row
		var end := int(rows[i + 1]["addr"]) if i + 1 < rows.size() else a + 2
		var raw := str(row.get("raw", "")).replace(" ", "").replace("…", "")
		var n := clampi(raw.length() / 2 if raw != "" else 2, 1, 16)
		if not str(row["text"]).begins_with("."):
			n = 4 if raw.length() == 8 else 2
		for b in range(a, mini(a + n, end)):
			insn_of[b] = a
	if not OS.get_cmdline_user_args().has("--memory-hover"):
		selected = -1
	hover = -1
	_key = null


func update_state(s: Dictionary) -> void:
	st = s
	if not s.get("loaded", false):
		return
	var hv: Dictionary = s.get("hexview", {})
	var key := [hv.get("addr"), hv.get("data"), s.get("mem_access"), s.get("mem_changed"),
		s.get("regs"), s.get("stack"), s.get("insn_addr"), s.get("running"), mode, text_scale]
	if key == _key:
		return     # nothing the tab shows has changed
	_key = key
	view_addr = int(hv.get("addr", 0x3000))
	data = PackedByteArray(str(hv.get("data", "")).hex_decode())
	if follow and not s.get("running", false):
		_follow_access()
	_redraw_all()
	_show_banner()
	_show_inspector()


## After a step: if the bytes it read or wrote are not in view, move the view there.
func _follow_access() -> void:
	for acc in st.get("mem_access", []):
		var a := int(acc["addr"])
		if a >= RAM_TOP or int(acc["insn"]) == a:
			continue
		var first := view_addr
		var last := view_addr + _rows_fit() * ROW
		if a < first or a + int(acc["size"]) > last:
			_view((a & ~0xF) - 0x20)
		return


func _view(addr: int) -> void:
	addr = clampi(addr, 0, RAM_TOP - 256) & ~0xF
	if addr != view_addr:
		Backend.send("view_mem", {"addr": addr, "len": 256})


func _on_goto(text: String) -> void:
	var t := text.strip_edges()
	var v = Backend.parse_number(t)
	if v == null and prog.get("labels", {}).has(t):
		v = int(prog["labels"][t])
	if v == null:
		inspector.text = "[color=%s]There is no label [b]%s[/b] in this program (labels are case-sensitive), and it is not a number.[/color]" % [Palette.hex("error"), _esc(t)]
		return
	v = int(v)
	if v >= RAM_TOP:
		inspector.text = _io_text(v)
		return
	selected = v
	_view((v & ~0xF) - 0x20)
	_show_inspector()


func _goto_special(which: String) -> void:
	if not st.get("loaded", false):
		return
	var a := 0
	match which:
		"pc": a = int(st["insn_addr"])
		"sp": a = int(st["regs"][13])
		"main": a = int(prog.get("main", 0x3000))
	selected = mini(a, RAM_TOP - 1)
	_view((a & ~0xF) - 0x40 if which == "sp" else (a & ~0xF) - 0x20)
	_show_inspector()


func _on_mode(i: int) -> void:
	mode = i
	_save("memory_mode", i)
	_key = null
	update_state(st)


func _on_follow(on: bool) -> void:
	follow = on
	_save("memory_follow", on)


func _save(k: String, v: Variant) -> void:
	var cfg := ConfigFile.new()
	cfg.load(SETTINGS)
	cfg.set_value("ui", k, v)
	cfg.save(SETTINGS)


func _write() -> void:
	if selected < 0:
		inspector.text = "[color=%s]Click a byte first, then type the new bytes.[/color]" % Palette.hex("dim")
		return
	var h := edit_field.text.replace(" ", "").replace(",", "").replace("0x", "")
	if h.is_empty() or h.length() % 2 != 0 or not h.is_valid_hex_number():
		inspector.text = "[color=%s]Type bytes in hex, two digits each, for example [code]2a 00 00 00[/code].[/color]" % Palette.hex("error")
		return
	Backend.send("write_mem", {"addr": selected, "data": h})
	edit_field.text = ""


# ── what is where ───────────────────────────────────────────────────────────

func _kind(a: int) -> int:
	if a < 0 or a >= RAM_TOP:
		return K_FREE
	var sp := int(st.get("regs", [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, RAM_TOP])[13])
	if a >= sp and kinds.size() > a and kinds[a] == K_FREE:
		return K_STACK
	return kinds[a] if kinds.size() > a else K_FREE


func _tint(k: int) -> Color:
	var e: Array = KINDS[k]
	if str(e[1]) == "":
		return Color.TRANSPARENT
	var c := Palette.c(e[1])
	c.a = e[2] * (1.4 if Palette.is_light() else 1.0)
	return c


func _byte(a: int) -> int:
	var i := a - view_addr
	if i >= 0 and i < data.size():
		return data[i]
	# outside the window: the stack's bytes come with every state too
	var sk: Dictionary = st.get("stack", {})
	var j := a - int(sk.get("addr", RAM_TOP))
	var raw := str(sk.get("data", ""))
	if j >= 0 and j * 2 + 2 <= raw.length():
		return raw.substr(j * 2, 2).hex_to_int()
	return -1


## The label `a` is in, and how far into it: ["numbers", 4], or ["", 0].
func _label_of(a: int) -> Array:
	var best := ["", 0]
	for e in label_list:
		if int(e[0]) > a:
			break
		best = [str(e[1]), a - int(e[0])]
	if best[0] != "" and _kind(a) != _kind(a - int(best[1])):
		return ["", 0]   # in a different part of memory from its label
	return best


func _line_of(a: int) -> String:
	if not insn_of.has(a):
		return ""
	var row: Dictionary = rows_by_addr[insn_of[a]]
	var line := int(row.get("line", 0))
	var code := str(row.get("source", "")).get_slice("@", 0).get_slice("/*", 0).strip_edges()
	if code == "":
		code = " ".join(str(row.get("shown", row["text"])).split(" ", false))
	return code + (("   (line %d)" % line) if line > 0 and int(row["addr"]) >= OS_TOP else "")


## address → [register names] for R0–R12 and SP that hold an address in RAM.
func _pointers() -> Dictionary:
	var out := {}
	var regs: Array = st.get("regs", [])
	for i in mini(regs.size(), 14):
		var v := int(regs[i])
		if v >= 0x3000 and v < RAM_TOP:
			var name: String = ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "SP"][i]
			out[v] = out.get(v, []) + [name]
	return out


## byte address → "read" | "write" for the last step's data accesses.
func _accessed() -> Dictionary:
	var out := {}
	if st.get("running", false):
		return out
	for acc in st.get("mem_access", []):
		for b in range(int(acc["addr"]), int(acc["addr"]) + int(acc["size"])):
			if b < RAM_TOP:
				out[b] = str(acc["kind"])
	return out


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")


# ── the byte grid ───────────────────────────────────────────────────────────

func _fs(base: float) -> int:
	return roundi(base * text_scale)


func _cell_w() -> float:
	# as wide as reads well, narrower when the grid is squeezed
	var room := (grid.size.x if grid else 600.0) - _addr_w() - 8 * text_scale
	if _text_col():
		room -= 16 * 9 * text_scale + 14 * text_scale
	return clampf(room / 16.3, 17.0 * text_scale, 27.0 * text_scale)


## The "as text" column is shown only when there is room for it.
func _text_col() -> bool:
	return mode != 2 and grid != null and grid.size.x >= _addr_w() + 16 * 21 * text_scale + 16 * 9 * text_scale + 24


func _row_h() -> float:
	return 23.0 * text_scale


func _note_h() -> float:
	return 19.0 * text_scale


func _addr_w() -> float:
	return 52.0 * text_scale


func _rows_fit() -> int:
	if grid == null or grid.size.y <= 0:
		return 8
	var y := _row_h() + 4
	var n := 0
	var notes := _notes_by_row()
	while n < 16:
		var need := _row_h() + (_note_h() if notes.has(n) else 0.0)
		if y + need > grid.size.y - _row_h():
			break
		y += need
		n += 1
	return maxi(n, 1)


## Row index → [[column, text, colour key]]: the labels and pointer tags
## drawn above that row.
func _notes_by_row() -> Dictionary:
	var out := {}
	var ptr := _pointers()
	for r in 16:
		var items := []
		for col in ROW:
			var a := view_addr + r * ROW + col
			for n in labels_at.get(a, []):
				items.append([col, str(n), "label"])
			for reg in ptr.get(a, []):
				items.append([col, str(reg) + " ↓", "accent"])
		if not items.is_empty():
			out[r] = items
	return out


func _draw_grid(c: Control) -> void:
	c.draw_rect(Rect2(Vector2.ZERO, c.size), Palette.c("surface"))
	if data.is_empty() or not st.get("loaded", false):
		c.draw_string(font, Vector2(10, 24 * text_scale), "Load a program to see its memory.",
			HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(14), Palette.c("dim"))
		return
	var cw := _cell_w()
	var rh := _row_h()
	var nh := _note_h()
	var x0 := _addr_w()
	var dim := Palette.c("dim")
	var txt := Palette.c("text")
	var fs := _fs(14)
	var fs2 := _fs(13)
	var asc := Palette.c("muted")
	# header: the column offsets
	var y := rh - 6 * text_scale
	c.draw_string(mono, Vector2(4, y), "addr", HORIZONTAL_ALIGNMENT_LEFT, -1, fs2, dim)
	var span := 4 if mode == 1 else 1
	for col in range(0, ROW, span):
		c.draw_string(mono, Vector2(_col_x(col) + 3 * text_scale, y), "+%X" % col, HORIZONTAL_ALIGNMENT_LEFT, -1, fs2, dim)
	if _text_col():
		c.draw_string(font, Vector2(_col_x(ROW) + 10 * text_scale, y), "as text", HORIZONTAL_ALIGNMENT_LEFT, -1, fs2, dim)
	y = rh + 4
	var notes := _notes_by_row()
	var acc := _accessed()
	var changed := {}
	for a in st.get("mem_changed", []):
		changed[int(a)] = true
	var pc := int(st.get("insn_addr", -1))
	var pc_end := pc + (4 if insn_of.get(pc + 2, -1) == pc else 2)
	var nrows := _rows_fit()
	for r in nrows:
		var row_addr := view_addr + r * ROW
		if notes.has(r):
			var free_x := 0.0          # tags at the same byte sit side by side
			for it in notes[r]:
				var col: int = it[0]
				var tx := maxf(_col_x(col) + 1, free_x)
				free_x = tx + font.get_string_size(it[1], HORIZONTAL_ALIGNMENT_LEFT, -1, fs2).x + 8 * text_scale
				var col_c := Palette.c(it[2])
				c.draw_string(font, Vector2(tx, y + nh - 5 * text_scale), it[1], HORIZONTAL_ALIGNMENT_LEFT, -1, fs2, col_c)
				c.draw_line(Vector2(tx, y + nh - 2), Vector2(tx, y + nh + 3), col_c, 2.0)
			y += nh
		c.draw_string(mono, Vector2(4, y + rh - 7 * text_scale), "%04X" % row_addr, HORIZONTAL_ALIGNMENT_LEFT, -1, fs2, dim)
		var col := 0
		while col < ROW:
			var a := row_addr + col
			var cell := Rect2(_col_x(col), y + 1, cw * span - 2, rh - 2)
			var k := _kind(a)
			var tint := _tint(k)
			if tint.a > 0:
				c.draw_rect(cell, tint)
			if a >= pc and a < pc_end:
				c.draw_rect(cell, Palette.c("pc_bg"))
			var how := ""
			for b in range(a, a + span):
				if acc.has(b):
					how = acc[b]
			var hot := false
			for b in range(a, a + span):
				if changed.has(b):
					hot = true
			if how == "write" or hot:
				var wc := Palette.c("changed")
				wc.a = 0.30
				c.draw_rect(cell, wc)
			if how != "":
				c.draw_rect(cell.grow(-1), Palette.c("changed") if how == "write" else Palette.c("accent"), false, 2.0)
			if hover >= a and hover < a + span:
				c.draw_rect(cell, Palette.c("sel"), false, 1.5)
			if selected >= a and selected < a + span:
				c.draw_rect(cell.grow(1), Palette.c("sel"), false, 2.5)
			var col_t := Palette.c("changed") if hot else (txt if k != K_FREE and k != K_OS else dim)
			var s := ""
			match mode:
				0:
					var v := _byte(a)
					s = "%02X" % v if v >= 0 else "··"
				1:
					var w := _word(a)
					s = "%08X" % w if w >= 0 else "········"
				2:
					var v := _byte(a)
					s = _char(v)
			var sw := mono.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x
			c.draw_string(mono, Vector2(cell.position.x + (cell.size.x - sw) / 2.0, y + rh - 7 * text_scale), s,
				HORIZONTAL_ALIGNMENT_LEFT, -1, fs, col_t)
			col += span
		if _text_col():
			var line := ""
			for i in ROW:
				var v := _byte(row_addr + i)
				line += char(v) if v >= 32 and v < 127 else "·"
			c.draw_string(mono, Vector2(_col_x(ROW) + 10 * text_scale, y + rh - 7 * text_scale), line,
				HORIZONTAL_ALIGNMENT_LEFT, -1, fs2, asc)
		y += rh
	# the colour key
	var lx := 4.0
	var ly := c.size.y - 6 * text_scale
	for e in [[K_INSN, "instruction"], [K_LIT, "literal / string"], [K_DATA, "data"], [K_STACK, "stack"]]:
		var sq := Rect2(lx, ly - 11 * text_scale, 11 * text_scale, 11 * text_scale)
		var t := _tint(e[0])
		t.a = minf(1.0, t.a * 2.5)
		c.draw_rect(sq, t)
		c.draw_string(font, Vector2(lx + 15 * text_scale, ly), e[1], HORIZONTAL_ALIGNMENT_LEFT, -1, fs2, dim)
		lx += font.get_string_size(e[1], HORIZONTAL_ALIGNMENT_LEFT, -1, fs2).x + 30 * text_scale
	for e in [["accent", "read by the last step"], ["changed", "written"]]:
		var sq := Rect2(lx, ly - 11 * text_scale, 11 * text_scale, 11 * text_scale)
		c.draw_rect(sq, Palette.c(e[0]), false, 2.0)
		c.draw_string(font, Vector2(lx + 15 * text_scale, ly), e[1], HORIZONTAL_ALIGNMENT_LEFT, -1, fs2, dim)
		lx += font.get_string_size(e[1], HORIZONTAL_ALIGNMENT_LEFT, -1, fs2).x + 30 * text_scale


func _col_x(col: int) -> float:
	return _addr_w() + col * _cell_w() + (6 * text_scale if col >= 8 else 0.0)


func _word(a: int) -> int:
	var b0 := _byte(a)
	var b1 := _byte(a + 1)
	var b2 := _byte(a + 2)
	var b3 := _byte(a + 3)
	if b0 < 0 or b3 < 0:
		return -1
	return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)


static func _char(v: int) -> String:
	if v < 0:
		return "··"
	if v == 0:
		return "\\0"
	if v == 10:
		return "\\n"
	if v == 13:
		return "\\r"
	if v == 32:
		return "␣"
	if v > 32 and v < 127:
		return char(v)
	return "·"


## The byte under a point of the grid, or -1.
func _grid_hit(at: Vector2) -> int:
	var y := _row_h() + 4
	var notes := _notes_by_row()
	for r in _rows_fit():
		if notes.has(r):
			y += _note_h()
		if at.y >= y and at.y < y + _row_h():
			for col in ROW:
				var x := _col_x(col)
				if at.x >= x and at.x < x + _cell_w():
					var a := view_addr + r * ROW + col
					return (a & ~3) if mode == 1 else a
			return -1
		y += _row_h()
	return -1


func _input_grid(c: Control, e) -> void:
	if e is InputEventMouseMotion:
		_set_hover(_grid_hit(e.position))
	elif e is InputEventMouseButton and e.pressed:
		if e.button_index == MOUSE_BUTTON_WHEEL_UP:
			_view(view_addr - 0x30)
			c.accept_event()
		elif e.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_view(view_addr + 0x30)
			c.accept_event()
		elif e.button_index == MOUSE_BUTTON_LEFT:
			var a := _grid_hit(e.position)
			if a >= 0:
				selected = -1 if a == selected else a
				if selected >= 0:
					var v := _byte(a)
					edit_field.placeholder_text = "new bytes at %04X" % a
					edit_field.text = ("%02X" % v) if v >= 0 and mode != 1 else (("%02X %02X %02X %02X" % [_byte(a), _byte(a + 1), _byte(a + 2), _byte(a + 3)]) if v >= 0 else "")
				grid.queue_redraw()
				_show_inspector()
				c.accept_event()


func _set_hover(a: int) -> void:
	if a == hover:
		return
	hover = a
	grid.queue_redraw()
	_show_inspector()


func _tip_grid(_c: Control, _at: Vector2) -> String:
	return ""        # the inspector line under the grid says it all


# ── the map ─────────────────────────────────────────────────────────────────

## The parts of the address space, top to bottom:
## [name, start, end, palette key, what it is, view address on click]
func _map_parts() -> Array:
	var reg: Dictionary = prog.get("regions", {})
	var secs: Dictionary = reg.get("sections", {})
	var sp := int(st.get("regs", [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, RAM_TOP])[13])
	var text_end := int(secs.get("text", [OS_TOP, OS_TOP])[1])
	var prog_end := int(reg.get("program_end", text_end))
	var parts := []
	parts.append(["built-in OS", 0, OS_TOP, "cyan", "picosim's OS: _start, putchar, getchar", 0])
	parts.append(["your code", OS_TOP, text_end, "cyan", "your instructions, then their literal pools and strings", OS_TOP])
	if prog_end > text_end:
		parts.append(["your data", text_end, prog_end, "green", ".data and .bss: your program's variables", text_end])
	var free_top := mini(sp, RAM_TOP)
	if free_top > prog_end:
		parts.append(["free RAM", prog_end, free_top, "", "unused: the stack can grow down into it", prog_end])
	parts.append(["stack", free_top, RAM_TOP, "sel", "from SP up to the top of RAM; it grows down", free_top])
	return parts


func _map_rects(c: Control) -> Array:
	var parts := _map_parts()
	var s := text_scale
	var io_h := 64 * s
	var top := 4.0
	var avail := c.size.y - io_h - 10 - top
	var fixed := 0.0
	var minh := 40 * s
	for p in parts:
		if p[0] != "free RAM":
			fixed += minh
	var free_h := maxf(minh, avail - fixed)
	var out := []
	var y := top
	for p in parts:
		var h := free_h if p[0] == "free RAM" else minh
		out.append([p, Rect2(4, y, c.size.x - 8, h - 3)])
		y += h
	return out


func _draw_map(c: Control) -> void:
	c.draw_rect(Rect2(Vector2.ZERO, c.size), Palette.c("surface"))
	if not st.get("loaded", false) or prog.is_empty():
		return
	var s := text_scale
	var fs := _fs(13)
	var fs2 := _fs(12)
	var dim := Palette.c("dim")
	var txt := Palette.c("text")
	var pc := int(st.get("insn_addr", 0))
	var sp := int(st["regs"][13])
	var lo := view_addr
	var hi := view_addr + _rows_fit() * ROW
	var acc: Array = [] if st.get("running", false) else st.get("mem_access", [])
	for e in _map_rects(c):
		var p: Array = e[0]
		var r: Rect2 = e[1]
		var bg := Palette.c("box")
		if str(p[3]) != "":
			bg = Palette.c(p[3])
			bg.a = 0.22 if not Palette.is_light() else 0.30
		c.draw_rect(r, Palette.c("box"))
		c.draw_rect(r, bg)
		var seen: bool = lo < int(p[2]) and hi > int(p[1])
		c.draw_rect(r, Palette.c("accent") if seen else Palette.c("edge"), false, 2.0 if seen else 1.0)
		c.draw_string(font, r.position + Vector2(6, 16 * s), p[0], HORIZONTAL_ALIGNMENT_LEFT, r.size.x - 10, fs, txt)
		var rng := "%04X–%04X" % [int(p[1]), int(p[2]) - 1] if int(p[2]) > int(p[1]) else "empty"
		c.draw_string(mono, r.position + Vector2(6, 31 * s), rng, HORIZONTAL_ALIGNMENT_LEFT, r.size.x - 10, fs2, dim)
		# markers: PC in its part, SP at the stack's bottom, the last access
		if pc >= int(p[1]) and pc < int(p[2]):
			_pill(c, Vector2(r.end.x - 4, r.position.y + 4), "PC", Palette.c("changed"))
		if str(p[0]) == "stack":
			_pill(c, Vector2(r.end.x - 4, r.position.y + 4), "SP", Palette.c("sel"))
		for a in acc:
			var ad := int(a["addr"])
			if ad >= int(p[1]) and ad < int(p[2]) and ad < RAM_TOP:
				_pill(c, Vector2(r.end.x - 4, r.end.y - 18 * s), "W" if str(a["kind"]) == "write" else "R",
					Palette.c("changed") if str(a["kind"]) == "write" else Palette.c("accent"))
				break
	# the I/O registers, which are not RAM
	var io := Rect2(4, c.size.y - 64 * s - 6, c.size.x - 8, 64 * s)
	c.draw_rect(io, Palette.c("box"))
	var ioc := Palette.c("purple")
	c.draw_rect(io, ioc, false, 1.0)
	c.draw_string(font, io.position + Vector2(6, 16 * s), "I/O registers", HORIZONTAL_ALIGNMENT_LEFT, io.size.x - 10, fs, txt)
	c.draw_string(font, io.position + Vector2(6, 33 * s), "not RAM: they", HORIZONTAL_ALIGNMENT_LEFT, io.size.x - 10, fs2, dim)
	c.draw_string(font, io.position + Vector2(6, 49 * s), "set the pins", HORIZONTAL_ALIGNMENT_LEFT, io.size.x - 10, fs2, dim)
	for a in acc:
		if int(a["addr"]) >= RAM_TOP:
			_pill(c, Vector2(io.end.x - 4, io.end.y - 20 * s), "W" if str(a["kind"]) == "write" else "R",
				Palette.c("changed") if str(a["kind"]) == "write" else Palette.c("accent"))
			break


## A small tag ending at `right` (its top-right corner).
func _pill(c: Control, right: Vector2, t: String, col: Color) -> void:
	var fs := _fs(12)
	var w := font.get_string_size(t, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x + 10
	var r := Rect2(right.x - w, right.y, w, 17 * text_scale)
	c.draw_rect(r, col)
	c.draw_string(font, r.position + Vector2(5, 13 * text_scale), t, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, Palette.c("bg"))


func _input_map(c: Control, e) -> void:
	if not (e is InputEventMouseButton and e.pressed and e.button_index == MOUSE_BUTTON_LEFT):
		return
	var io_top := c.size.y - 64 * text_scale - 6
	if e.position.y >= io_top:
		inspector.text = _io_text(0xD0000000)
		return
	for r in _map_rects(c):
		if r[1].has_point(e.position):
			var p: Array = r[0]
			var to := int(p[5])
			if str(p[0]) == "stack":
				to = int(st["regs"][13]) - 0x20
			_view(to)
			c.accept_event()
			return


func _tip_map(c: Control, at: Vector2) -> String:
	for r in _map_rects(c):
		if r[1].has_point(at):
			return "%s: %s.\nClick to show it." % [r[0][0], r[0][4]]
	return "The I/O registers are at addresses above RAM. Loads and stores there do not\nchange memory: they read and set the Pico's pins (see the board and the GPIO page)."


func _io_text(a: int) -> String:
	return "[b]0x%08X[/b] is not RAM: addresses from [code]0x40000000[/code] up are the Pico's [b]I/O registers[/b]. A load or store there reads or sets hardware, such as the pins (SIO at [code]0xD0000000[/code], IO_BANK0 at [code]0x40014000[/code]). Watch the Pico board to see what a store there does." % a


# ── the stack picture ───────────────────────────────────────────────────────

## [address, value, note, function, popped] from the newest (SP) word to
## the oldest, plus the words the last step popped (just below SP).
func _stack_words() -> Array:
	var sk: Dictionary = st.get("stack", {})
	var base := int(sk.get("addr", RAM_TOP))
	var raw := PackedByteArray(str(sk.get("data", "")).hex_decode())
	var notes: Dictionary = sk.get("notes", {})
	var out := []
	for off in range(0, raw.size() - 3, 4):
		var a := base + off
		var v := raw.decode_u32(off)
		var n = notes.get(str(a))
		var note := ""
		var fn := ""
		if n is Dictionary:
			note = str(n.get("note", ""))
			fn = str(n.get("func", ""))
		elif n != null:
			note = str(n)
		if note == "":
			note = _guess(v)
		out.append([a, v, note, fn, false])
	return out


## The words the last step read from the stack and left below SP (a POP).
func _popped() -> Array:
	var out := []
	if st.get("running", false):
		return out
	var sp := int(st.get("regs", [])[13]) if st.has("regs") else RAM_TOP
	for acc in st.get("mem_access", []):
		var a := int(acc["addr"])
		if str(acc["kind"]) == "read" and a < sp and a >= sp - 64 and a >= 0x3000 and int(acc["size"]) == 4:
			var d := PackedByteArray(str(acc.get("data", "")).hex_decode())
			if d.size() == 4:
				var into := _popped_into(acc, a)
				out.append([a, d.decode_u32(0), "popped by the last step" + (" into " + into if into != "" else ""), "", true])
	return out


## Which register a POP loaded from stack address `a` ("PC", "R4", or "").
func _popped_into(acc: Dictionary, a: int) -> String:
	var row: Dictionary = rows_by_addr.get(int(acc["insn"]), {})
	var text := str(row.get("text", ""))
	if not text.begins_with("pop"):
		return ""
	var regs := []
	for r in text.get_slice("{", 1).get_slice("}", 0).split(","):
		regs.append(r.strip_edges().to_upper())
	var first := a
	for x in st.get("mem_access", []):
		if int(x["insn"]) == int(acc["insn"]):
			first = mini(first, int(x["addr"]))
	var k := (a - first) / 4
	return str(regs[k]) if k >= 0 and k < regs.size() else ""


## What a value on the stack probably is, when picosim did not see it pushed.
func _guess(v: int) -> String:
	if v & 1 and v < RAM_TOP:
		var t := v & ~1
		if t < OS_TOP and t > 0:
			return "a return address into the built-in OS"
		var before: Dictionary = rows_by_addr.get(t - 4, {})
		var back: Dictionary = rows_by_addr.get(t, {})
		var line := int(back.get("line", 0))
		if str(before.get("text", "")).begins_with("bl"):
			return "a return address: back to %04X%s, the line after a bl" % [t, (" (line %d)" % line) if line > 0 else ""]
		before = rows_by_addr.get(t - 2, {})
		if str(before.get("text", "")).begins_with("blx"):
			return "a return address: back to %04X, after a blx" % t
	return ""


func _draw_stack(c: Control) -> void:
	c.draw_rect(Rect2(Vector2.ZERO, c.size), Palette.c("surface"))
	if not st.get("loaded", false):
		return
	var s := text_scale
	var fs := _fs(14)
	var fs2 := _fs(12.5)
	var dim := Palette.c("dim")
	var txt := Palette.c("text")
	var sp := int(st["regs"][13])
	c.draw_string(font, Vector2(6, 17 * s), "The stack", HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(14), txt)
	c.draw_string(font, Vector2(6, 33 * s), "top of RAM: 0x10000 ↑ · grows down ↓", HORIZONTAL_ALIGNMENT_LEFT, c.size.x - 10, fs2, dim)
	var words := _stack_words()
	var y := 42 * s
	if sp >= RAM_TOP or words.is_empty():
		var msg := "Empty: SP = 0x%08X, just past the top of RAM. The first push stores just below it." % sp
		if sp < 0x3000 or sp > RAM_TOP:
			msg = "SP = 0x%08X is outside RAM: a push now would fault." % sp
		_wrap(c, Rect2(8, y + 4, c.size.x - 16, 80 * s), msg, fs2, dim)
		y += 60 * s
	var box_h := 40 * s
	var room := int((c.size.y - y - 6) / box_h)
	var popped := _popped()
	var shown := words
	var hidden := 0
	if shown.size() + popped.size() > room:
		var keep := maxi(1, room - popped.size() - 1)
		hidden = shown.size() - keep
		shown = shown.slice(0, keep)
	if hidden > 0:
		c.draw_string(font, Vector2(8, y + 14 * s), "▲ %d more word%s above, up to 0x10000" % [hidden, "s" if hidden != 1 else ""],
			HORIZONTAL_ALIGNMENT_LEFT, c.size.x - 12, fs2, dim)
		y += 20 * s
	var acc := _accessed()
	var changed := {}
	for a in st.get("mem_changed", []):
		changed[int(a)] = true
	# oldest (highest address) at the top, SP's word at the bottom
	var list := shown.duplicate()
	list.reverse()
	var last_fn := "§"
	for w in list:
		var a := int(w[0])
		if str(w[3]) != last_fn and str(w[3]) != "":
			c.draw_string(font, Vector2(8, y + 12 * s), str(w[3]) + " saved:", HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(12), Palette.c("label"))
			y += 15 * s
		last_fn = str(w[3])
		_stack_box(c, Rect2(6, y, c.size.x - 12, box_h - 4), w, changed.has(a), acc.get(a, ""), a == (sp & ~3))
		y += box_h
	for w in popped:
		_stack_box(c, Rect2(6, y, c.size.x - 12, box_h - 4), w, false, "read", false)
		y += box_h


func _stack_box(c: Control, r: Rect2, w: Array, hot: bool, how: String, at_sp: bool) -> void:
	var s := text_scale
	var ghost: bool = w[4]
	var bg := Palette.c("box")
	if not ghost:
		var t := _tint(K_STACK)
		c.draw_rect(r, bg)
		c.draw_rect(r, t)
	else:
		bg.a = 0.35
		c.draw_rect(r, bg)
	var edge := Palette.c("changed") if hot else (Palette.c("accent") if how == "read" else Palette.c("edge"))
	if ghost:
		for i in int(r.size.x / 10):
			var x := r.position.x + i * 10
			c.draw_line(Vector2(x, r.position.y), Vector2(x + 5, r.position.y), Palette.c("dim"), 1.0)
			c.draw_line(Vector2(x, r.end.y), Vector2(x + 5, r.end.y), Palette.c("dim"), 1.0)
	else:
		c.draw_rect(r, edge, false, 2.0 if hot or how != "" else 1.0)
	var col := Palette.c("dim") if ghost else Palette.c("text")
	c.draw_string(mono, r.position + Vector2(6, 16 * s), "%04X" % int(w[0]), HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(12), Palette.c("dim"))
	c.draw_string(mono, r.position + Vector2(52 * s, 16 * s), "0x%08X" % int(w[1]), HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(14),
		Palette.c("changed") if hot else col)
	if at_sp:
		_pill(c, Vector2(r.end.x - 3, r.position.y + 2), "◀ SP", Palette.c("sel"))
	var note := str(w[2])
	if note != "":
		c.draw_string(font, r.position + Vector2(6, 32 * s), note, HORIZONTAL_ALIGNMENT_LEFT, r.size.x - 10, _fs(12),
			Palette.c("dim") if ghost else Palette.c("muted"))


func _wrap(c: Control, r: Rect2, t: String, fs: int, col: Color) -> void:
	var words := t.split(" ")
	var line := ""
	var y := r.position.y + fs
	for w in words:
		var tryl := w if line == "" else line + " " + w
		if font.get_string_size(tryl, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x > r.size.x and line != "":
			c.draw_string(font, Vector2(r.position.x, y), line, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, col)
			y += fs + 4
			line = w
		else:
			line = tryl
	if line != "":
		c.draw_string(font, Vector2(r.position.x, y), line, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, col)


func _input_stack(_c: Control, _e) -> void:
	pass


func _tip_stack(c: Control, at: Vector2) -> String:
	return "Each box is one word (4 bytes) on the stack. PUSH stores registers below SP and moves SP down;\nPOP loads them back and moves SP up. The box at the bottom is the one SP points at."


# ── the two text lines ──────────────────────────────────────────────────────

## "Last step: ldr r0, [r1] read the word at 0x3024 (numbers) …"
func _show_banner() -> void:
	if banner == null:
		return
	var dim := Palette.hex("dim")
	if not st.get("loaded", false):
		banner.text = ""
		return
	if st.get("running", false):
		banner.text = "[color=%s]Running: the bytes change as the program runs. Pause or step to see each load and store light up.[/color]" % dim
		return
	var acc: Array = st.get("mem_access", [])
	if acc.is_empty():
		var why := "Step through a load ([code]ldr[/code]), a store ([code]str[/code]), [code]push[/code] or [code]pop[/code] to see which bytes it uses light up here."
		var h: Array = st.get("history", [])
		if str(st.get("action", "")) in ["step"] and not h.is_empty():
			why = "The last instruction worked only on registers: it did not read or write memory (apart from being fetched itself). " + why
		banner.text = "[color=%s]%s[/color]" % [dim, why]
		return
	# one sentence per instruction (a PUSH or POP of several words is summed up)
	var by := {}
	var order := []
	for a in acc:
		var k := int(a["insn"])
		if not by.has(k):
			by[k] = []
			order.append(k)
		by[k].append(a)
	var t := ""
	for k in order:
		t += ("\n" if t != "" else "") + _sentence(k, by[k])
	banner.text = t


func _sentence(insn: int, list: Array) -> String:
	var acc := Palette.hex("accent")
	var hot := Palette.hex("changed")
	var row: Dictionary = rows_by_addr.get(insn, {})
	var code := str(row.get("source", "")).get_slice("@", 0).get_slice("/*", 0).strip_edges()
	if code == "":
		code = " ".join(str(row.get("shown", row.get("text", "?"))).split(" ", false))
	var who := "[code]%s[/code]" % _esc(code)
	var first: Dictionary = list[0]
	var kind := str(first["kind"])
	var verb := ("[color=%s][b]read[/b][/color]" % acc) if kind == "read" else ("[color=%s][b]wrote[/b][/color]" % hot)
	var a := int(first["addr"])
	if a >= RAM_TOP:
		var name := _io_name(a)
		var val = first.get("value")
		var to := (" [code]0x%08X[/code] to" % int(val)) if kind == "write" and val != null else (" from" if kind == "read" else " to")
		return "%s %s%s the I/O register [b]%s[/b] at [code]0x%08X[/code]. That is not RAM: it %s the Pico's pins (look at the board)." % [
			who, verb, to, name, a, "sets" if kind == "write" else "reads"]
	if list.size() > 1:
		var lo := int(list[0]["addr"])
		var hi := int(list[-1]["addr"]) + int(list[-1]["size"]) - 1
		var place := "the stack" if _kind(lo) == K_STACK or lo >= int(st["regs"][13]) - 64 else "memory"
		return "%s %s %d words %s %s, at [code]%04X[/code]–[code]%04X[/code] (see the stack on the right)." % [
			who, verb, list.size(), "onto" if kind == "write" else "from", place, lo, hi]
	var size := int(first["size"])
	var what: String = {1: "a byte", 2: "a halfword", 4: "a word"}.get(size, "%d bytes" % size)
	var lab: Array = _label_of(a)
	var where := "[b]%04X[/b]" % a
	if str(lab[0]) != "":
		where += " (%s%s)" % [lab[0], (" + %d" % int(lab[1])) if int(lab[1]) else ""]
	elif _kind(a) == K_LIT:
		where += " (a literal pool word)"
	elif _kind(a) == K_STACK:
		where += " (on the stack)"
	var d := PackedByteArray(str(first.get("data", "")).hex_decode())
	var bytes := ""
	var val := 0
	for i in d.size():
		bytes += ("%02X " % d[i])
		val |= d[i] << (8 * i)
	bytes = bytes.strip_edges()
	# the register the value went into (a load) or came from (a store)
	var ops := str(row.get("text", "")).split(" ", false)
	var reg := ops[1].trim_suffix(",").to_upper() if ops.size() > 1 and str(ops[0]).begins_with(("ldr" if kind == "read" else "str")) else ""
	if ops.size() > 1 and str(ops[0]) in ["pop", "push"]:          # one register pushed or popped
		reg = str(row.get("text", "")).get_slice("{", 1).get_slice("}", 0).strip_edges().to_upper()
	var t := "%s %s %s %s %s" % [who, verb, what, "at" if kind == "read" else "to", where]
	if bytes != "":
		t += ": the bytes [code]%s[/code]" % bytes
		if size > 1:
			t += ", which [i]backwards[/i] (lowest byte first) make [code]0x%0*X[/code]" % [size * 2, val]
			if val < 0x1000:
				t += " = %d" % val       # a number; bigger values are usually addresses
		else:
			t += " = %d" % val
		if reg == "PC":
			t += ", loaded into [b]PC[/b]: the program jumps back to [code]%04X[/code], where the function was called from" % (val & ~1)
		elif reg != "":
			t += (", now in [b]%s[/b]" if kind == "read" else ", the value of [b]%s[/b]") % reg
	return t + "."


static func _io_name(addr: int) -> String:
	const SIO := {0x04: "GPIO_IN", 0x10: "GPIO_OUT", 0x14: "GPIO_OUT_SET", 0x18: "GPIO_OUT_CLR",
		0x1C: "GPIO_OUT_XOR", 0x20: "GPIO_OE", 0x24: "GPIO_OE_SET", 0x28: "GPIO_OE_CLR", 0x2C: "GPIO_OE_XOR"}
	if addr >= 0xD0000000 and addr < 0xD0000040:
		return "SIO " + SIO.get(addr - 0xD0000000, "+0x%X" % (addr - 0xD0000000))
	if addr >= 0x40014000 and addr < 0x40014800:
		var off := addr - 0x40014000
		return "IO_BANK0 GPIO%d_%s" % [off >> 3, "CTRL" if (off & 7) == 4 else "STATUS"]
	if addr >= 0x4001C000 and addr < 0x4001C100:
		return "PADS_BANK0 " + ("VOLTAGE_SELECT" if addr == 0x4001C000 else "GPIO%d" % ((addr - 0x4001C004) >> 2))
	if addr >= 0x4000C000 and addr < 0x4000C100:
		return "RESETS +0x%X" % (addr - 0x4000C000)
	return "0x%08X" % addr


## Everything about the byte under the mouse (or the selected one).
func _show_inspector() -> void:
	if inspector == null:
		return
	var a := hover if hover >= 0 else selected
	var dim := Palette.hex("dim")
	if a < 0 or not st.get("loaded", false):
		inspector.text = "[color=%s]Hover over a byte to see what it is. Click one to select it, then type new bytes on the right to change memory. Scroll the bytes with the mouse wheel.[/color]" % dim
		return
	var v := _byte(a)
	var k := _kind(a)
	var t := "[b]%04X[/b]" % a
	var lab: Array = _label_of(a)
	if str(lab[0]) != "":
		t += " · label [b]%s[/b]%s" % [lab[0], (" + %d" % int(lab[1])) if int(lab[1]) else ""]
	t += " · " + str(KINDS[k][3])
	var line := _line_of(a)
	if line != "" and k in [K_INSN, K_OS_CODE, K_LIT]:
		t += ": [code]%s[/code]" % _esc(line)
	t += "\n"
	if v >= 0 and k in [K_INSN, K_OS_CODE] and insn_of.has(a):
		var start: int = insn_of[a]
		var row: Dictionary = rows_by_addr[start]
		var raw := str(row.get("raw", ""))
		var bytes := ""
		for b in range(start, start + (4 if raw.length() > 5 else 2)):
			bytes += "%02X " % maxi(0, _byte(b))
		var shown := "0x" + raw if raw.length() == 4 else "0x" + raw.replace(" ", " and 0x") + " (two halfwords)"
		t += "Byte [code]0x%02X[/code] = %d. The instruction at %04X is the number [code]%s[/code], stored lowest byte first as [code]%s[/code]; the CPU fetches it from here." % [
			v, v, start, shown, bytes.strip_edges()]
	elif v >= 0:
		if mode == 1:
			var w := _word(a)
			t += "Word at %04X = [code]0x%08X[/code] = %d. In memory its bytes are [code]%02X %02X %02X %02X[/code]: the lowest byte comes first (little-endian)." % [
				a, w, w, _byte(a), _byte(a + 1), _byte(a + 2), _byte(a + 3)]
		else:
			t += "Byte [code]0x%02X[/code] = %d%s" % [v, v, (", the character '%s'" % char(v)) if v >= 32 and v < 127 else (", a new line" if v == 10 else "")]
			var wa := a & ~3
			var w := _word(wa)
			if w >= 0:
				t += " · the word at %04X (4 bytes, lowest first) is [code]0x%08X[/code] = %d" % [wa, w, w]
	if k == K_STACK:
		var sk: Dictionary = st.get("stack", {})
		var n = sk.get("notes", {}).get(str(a & ~3))
		var note := str(n.get("note", "")) if n is Dictionary else ""
		if note == "":
			note = _guess(_word(a & ~3))
		if note != "":
			t += "\nOn the stack: the word at %04X is %s." % [a & ~3, note[0].to_lower() + note.substr(1) if note.begins_with("a ") else note]
	var ptr: Dictionary = _pointers()
	if ptr.has(a):
		t += "\n[color=%s]%s hold%s this address: %s a [b]pointer[/b] to it, so [code]ldr rX, [lb]%s][/code] reads here.[/color]" % [
			Palette.hex("accent"), ", ".join(ptr[a]), "" if ptr[a].size() > 1 else "s", "each is" if ptr[a].size() > 1 else "it is", str(ptr[a][0]).to_lower()]
	if a == selected:
		t += "\n[color=%s]Selected: type new bytes on the right and press Enter to write them here.[/color]" % dim
	inspector.text = t
