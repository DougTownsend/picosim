extends PanelContainer
## Bottom-left memory view.
##   Disassembly — follows the PC; click the ● column to toggle a breakpoint.
##   Memory      — hex dump with go-to, byte editing and a stack view.

const PC_BG := Color(0.95, 0.80, 0.38, 0.22)
const BP_COLOR := Color("ff6b6b")
const LABEL_COLOR := Color("7fd1b9")
const DIM := Color("7f8794")
const CHANGED := "f2cc60"

var tabs: TabContainer
var tree: Tree
var follow: CheckBox
var items := {}          # addr -> TreeItem
var cur_item: TreeItem = null
var font: Font

var hex_text: RichTextLabel
var stack_text: RichTextLabel
var goto_edit: LineEdit
var write_addr: LineEdit
var write_data: LineEdit
var hex_addr := 0x3000


func _ready() -> void:
	font = Backend.mono_font()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color("22262e")
	sb.set_corner_radius_all(6)
	sb.content_margin_left = 6
	sb.content_margin_right = 6
	sb.content_margin_top = 6
	sb.content_margin_bottom = 6
	add_theme_stylebox_override("panel", sb)

	tabs = TabContainer.new()
	add_child(tabs)
	tabs.add_child(_build_disasm())
	tabs.add_child(_build_hex())
	tabs.set_tab_title(0, "Disassembly")
	tabs.set_tab_title(1, "Memory")


func _build_disasm() -> Control:
	var v := VBoxContainer.new()
	var top := HBoxContainer.new()
	v.add_child(top)
	follow = CheckBox.new()
	follow.text = "Follow PC"
	follow.button_pressed = true
	follow.focus_mode = Control.FOCUS_NONE
	top.add_child(follow)
	var hint := Label.new()
	hint.text = "click ● to toggle a breakpoint"
	hint.add_theme_color_override("font_color", DIM)
	hint.add_theme_font_size_override("font_size", 12)
	top.add_child(hint)

	tree = Tree.new()
	tree.size_flags_vertical = Control.SIZE_EXPAND_FILL
	tree.columns = 5
	tree.hide_root = true
	tree.column_titles_visible = true
	tree.select_mode = Tree.SELECT_SINGLE
	tree.add_theme_font_override("font", font)
	tree.add_theme_font_size_override("font_size", 13)
	for c in [[0, "", 22, false], [1, "Address", 86, false], [2, "Raw", 96, false],
			[3, "Label", 110, false], [4, "Instruction", 200, true]]:
		tree.set_column_title(c[0], c[1])
		tree.set_column_custom_minimum_width(c[0], c[2])
		tree.set_column_expand(c[0], c[3])
		tree.set_column_clip_content(c[0], true)
	tree.cell_selected.connect(_on_cell_selected)
	v.add_child(tree)
	return v


func _build_hex() -> Control:
	var v := VBoxContainer.new()
	var top := HBoxContainer.new()
	v.add_child(top)
	var gl := Label.new()
	gl.text = "Go to"
	top.add_child(gl)
	goto_edit = LineEdit.new()
	goto_edit.placeholder_text = "0x3000 or label"
	goto_edit.custom_minimum_size.x = 140
	goto_edit.add_theme_font_override("font", font)
	goto_edit.text_submitted.connect(_on_goto)
	top.add_child(goto_edit)
	for spec in [["PC", "pc"], ["SP", "sp"], ["main", "main"]]:
		var b := Button.new()
		b.text = spec[0]
		b.focus_mode = Control.FOCUS_NONE
		b.pressed.connect(_goto_special.bind(spec[1]))
		top.add_child(b)

	hex_text = RichTextLabel.new()
	hex_text.bbcode_enabled = true
	hex_text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	hex_text.add_theme_font_override("normal_font", font)
	hex_text.add_theme_font_size_override("normal_font_size", 13)
	hex_text.scroll_active = true
	hex_text.selection_enabled = true
	v.add_child(hex_text)

	var wr := HBoxContainer.new()
	v.add_child(wr)
	var wl := Label.new()
	wl.text = "Write bytes at"
	wr.add_child(wl)
	write_addr = LineEdit.new()
	write_addr.placeholder_text = "0x2000"
	write_addr.custom_minimum_size.x = 90
	write_addr.add_theme_font_override("font", font)
	wr.add_child(write_addr)
	write_data = LineEdit.new()
	write_data.placeholder_text = "de ad be ef"
	write_data.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	write_data.add_theme_font_override("font", font)
	write_data.text_submitted.connect(func(_t): _on_write())
	wr.add_child(write_data)
	var wb := Button.new()
	wb.text = "Write"
	wb.pressed.connect(_on_write)
	wr.add_child(wb)

	var sl := Label.new()
	sl.text = "Stack (from SP)"
	sl.add_theme_color_override("font_color", DIM)
	v.add_child(sl)
	stack_text = RichTextLabel.new()
	stack_text.bbcode_enabled = true
	stack_text.custom_minimum_size.y = 120
	stack_text.add_theme_font_override("normal_font", font)
	stack_text.add_theme_font_size_override("normal_font_size", 13)
	v.add_child(stack_text)
	return v


# ── disassembly ─────────────────────────────────────────────────────────────

func set_program(prog: Dictionary) -> void:
	tree.clear()
	items.clear()
	cur_item = null
	var root := tree.create_item()
	for row in prog["listing"]:
		var it := tree.create_item(root)
		var a := int(row["addr"])
		it.set_text(1, "%04X" % a)
		it.set_text(2, row["raw"])
		it.set_text(3, row["label"])
		it.set_text(4, row["text"])
		it.set_custom_color(1, DIM)
		it.set_custom_color(2, DIM)
		it.set_custom_color(3, LABEL_COLOR)
		it.set_metadata(0, a)
		it.set_tooltip_text(0, "Toggle breakpoint")
		items[a] = it
	var st := Backend.state
	if st.get("loaded", false):
		update_state(st)


func _on_cell_selected() -> void:
	var it := tree.get_selected()
	if it == null:
		return
	if tree.get_selected_column() == 0:
		Backend.send("toggle_bp", {"addr": it.get_metadata(0)})
	tree.deselect_all()


func update_state(st: Dictionary) -> void:
	if not st.get("loaded", false):
		return
	var bps := {}
	for b in st["breakpoints"]:
		bps[int(b)] = true
	for a in items:
		var it: TreeItem = items[a]
		it.set_text(0, "●" if bps.has(a) else "")
		it.set_custom_color(0, BP_COLOR)
	if cur_item != null:
		for c in 5:
			cur_item.clear_custom_bg_color(c)
		cur_item.set_text(1, "%04X" % int(cur_item.get_metadata(0)))
	var addr := int(st["insn_addr"])
	cur_item = items.get(addr)
	if cur_item != null:
		for c in 5:
			cur_item.set_custom_bg_color(c, PC_BG)
		cur_item.set_text(1, "▶%04X" % addr)
		if follow.button_pressed:
			tree.scroll_to_item(cur_item, true)
	_render_hex(st)


# ── hex / stack ─────────────────────────────────────────────────────────────

func _render_hex(st: Dictionary) -> void:
	var hv: Dictionary = st["hexview"]
	hex_addr = int(hv["addr"])
	var data := PackedByteArray(str(hv["data"]).hex_decode())
	var changed := {}
	for a in st["mem_changed"]:
		changed[int(a)] = true
	var sp := int(st["regs"][13])
	var pc := int(st["insn_addr"])
	var out := "[color=#7f8794]Addr    00 01 02 03 04 05 06 07  08 09 0A 0B 0C 0D 0E 0F  ASCII[/color]\n"
	for row in range(0, data.size(), 16):
		var line := "[color=#7f8794]%04X[/color]    " % (hex_addr + row)
		var ascii := ""
		for i in 16:
			if row + i >= data.size():
				break
			var a := hex_addr + row + i
			var b := data[row + i]
			var cell := "%02X" % b
			if changed.has(a):
				cell = "[color=#%s][b]%s[/b][/color]" % [CHANGED, cell]
			elif a == sp:
				cell = "[bgcolor=#3a4f7a]%s[/bgcolor]" % cell
			elif a >= pc and a < pc + 2:
				cell = "[bgcolor=#5a4a20]%s[/bgcolor]" % cell
			line += cell + (" " if i != 7 else "  ")
			ascii += char(b) if b >= 32 and b < 127 else "."
		out += line + " " + ascii.replace("[", "[lb]") + "\n"
	hex_text.text = out

	var sk: Dictionary = st["stack"]
	var sdata := PackedByteArray(str(sk["data"]).hex_decode())
	var sbase := int(sk["addr"])
	var s := ""
	for off in range(0, sdata.size() - 3, 4):
		var w := sdata.decode_u32(off)
		var a := sbase + off
		var mark := "SP→ " if a == sp else "    "
		var hot := false
		for k in 4:
			if changed.has(a + k):
				hot = true
		var val := "0x%08X" % w
		if hot:
			val = "[color=#%s]%s[/color]" % [CHANGED, val]
		s += "%s[color=#7f8794]%04X[/color]  %s\n" % [mark, a, val]
	if sdata.size() == 0:
		s = "[color=#7f8794](stack is empty — SP at top of RAM)[/color]"
	stack_text.text = s


func _on_goto(text: String) -> void:
	var v = Backend.parse_number(text)
	if v == null:
		var labels: Dictionary = Backend.program.get("labels", {})
		if labels.has(text.strip_edges()):
			v = int(labels[text.strip_edges()])
	if v != null:
		Backend.send("view_mem", {"addr": int(v) & 0xFFF0, "len": 256})


func _goto_special(which: String) -> void:
	var st := Backend.state
	if not st.get("loaded", false):
		return
	var a := 0
	match which:
		"pc": a = int(st["insn_addr"])
		"sp": a = int(st["regs"][13])
		"main": a = int(Backend.program.get("main", 0x3000))
	Backend.send("view_mem", {"addr": max(0, (a & 0xFFF0) - 0x40), "len": 256})


func _on_write() -> void:
	var a = Backend.parse_number(write_addr.text)
	var hexs := write_data.text.replace(" ", "").replace(",", "").replace("0x", "")
	if a == null or hexs.length() == 0 or hexs.length() % 2 != 0 or not hexs.is_valid_hex_number():
		return
	Backend.send("write_mem", {"addr": a, "data": hexs})
