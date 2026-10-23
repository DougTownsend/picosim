extends PanelContainer
## Bottom-left memory view.
##   Disassembly — follows the PC; click the ● column to toggle a breakpoint.
##                 The Source column is the line of the .s file each row came from.
##   Memory      — the memory explorer (memory_view.gd): a map of memory, the
##                 bytes coloured by what they are, the stack, and what the
##                 last step read or wrote.

var tabs: TabContainer
var tree: Tree
var follow: CheckBox
var items := {}          # addr -> TreeItem
var cur_item: TreeItem = null
var font: Font

const MemoryView := preload("res://scripts/memory_view.gd")
var mem_view
var _bps_key = null       # the breakpoints the ● column shows
var _cur_key = null       # [address, colour] of the ▶ row
var _dim_labels: Array[Label] = []
const COLUMNS := [[0, "", 22, false], [1, "Address", 86, false], [2, "Raw", 96, false],
	[3, "Label", 100, false], [4, "Instruction", 190, false], [5, "Source (line in your file)", 220, true]]


func _ready() -> void:
	font = Backend.mono_font()

	tabs = TabContainer.new()
	add_child(tabs)
	tabs.add_child(_build_disasm())
	tabs.add_child(_build_hex())
	tabs.set_tab_title(0, "Disassembly")
	tabs.set_tab_title(1, "Memory")
	tabs.set_tab_tooltip(0, "Your program, one instruction per row")
	tabs.set_tab_tooltip(1, "The bytes in memory, and the stack")
	# the Memory tab is kept up to date only while it is open
	tabs.tab_changed.connect(func(_t): if Backend.state.get("loaded", false): _update_memory(Backend.state))
	set_level(Level.current)
	Level.changed.connect(func(): set_level(Level.current))
	if OS.get_cmdline_user_args().has("--memory-tab"):   # screenshots
		tabs.current_tab = 1
	_apply_palette()
	Palette.changed.connect(_apply_palette)


## The Memory tab appears from level 2 (Memory & I/O).
func set_level(n: int) -> void:
	tabs.set_tab_hidden(1, n < 2)
	if n < 2:
		tabs.current_tab = 0


func _apply_palette() -> void:
	add_theme_stylebox_override("panel", Palette.panel_box("panel", 6, 6))
	for l in _dim_labels:
		l.add_theme_color_override("font_color", Palette.c("dim"))
	for it in items.values():
		_color_row(it)
	update_state(Backend.state)


func _on_text_scale(s: float) -> void:
	mem_view.set_text_scale(s)
	for c in COLUMNS:
		tree.set_column_custom_minimum_width(c[0], roundi(c[2] * s))


func _color_row(it: TreeItem) -> void:
	it.set_custom_color(0, Palette.c("bp"))
	it.set_custom_color(1, Palette.c("dim"))
	it.set_custom_color(2, Palette.c("dim"))
	it.set_custom_color(3, Palette.c("label"))
	it.set_custom_color(5, Palette.c("muted"))


func _build_disasm() -> Control:
	var v := VBoxContainer.new()
	var top := HBoxContainer.new()
	v.add_child(top)
	follow = CheckBox.new()
	follow.text = "Follow PC"
	follow.button_pressed = true
	follow.focus_mode = Control.FOCUS_NONE
	follow.tooltip_text = "Keep the instruction being run (▶) in view"
	top.add_child(follow)
	var hint := Label.new()
	hint.text = "click ● to toggle a breakpoint"
	_dim_labels.append(hint)
	hint.add_theme_font_size_override("font_size", 13)
	top.add_child(hint)

	tree = Tree.new()
	tree.size_flags_vertical = Control.SIZE_EXPAND_FILL
	tree.columns = COLUMNS.size()
	tree.hide_root = true
	tree.column_titles_visible = true
	tree.select_mode = Tree.SELECT_SINGLE
	tree.add_theme_font_override("font", font)
	tree.add_theme_font_size_override("font_size", 14)
	for c in COLUMNS:
		tree.set_column_title(c[0], c[1])
		tree.set_column_custom_minimum_width(c[0], c[2])
		tree.set_column_expand(c[0], c[3])
		tree.set_column_clip_content(c[0], true)
	tree.cell_selected.connect(_on_cell_selected)
	v.add_child(tree)
	return v


func _build_hex() -> Control:
	mem_view = MemoryView.new()
	return mem_view


# ── disassembly ─────────────────────────────────────────────────────────────

func set_program(prog: Dictionary) -> void:
	mem_view.set_program(prog)
	tree.clear()
	items.clear()
	cur_item = null
	_bps_key = null
	_cur_key = null
	var root := tree.create_item()
	for row in prog["listing"]:
		var it := tree.create_item(root)
		var a := int(row["addr"])
		it.set_text(1, "%04X" % a)
		it.set_text(2, row["raw"])
		it.set_text(3, row["label"])
		it.set_text(4, row.get("shown", row["text"]))
		var line := int(row.get("line", 0))
		if line > 0:
			it.set_text(5, "%3d   %s" % [line, row["source"]])
			it.set_tooltip_text(5, "Line %d of %s:\n%s" % [line,
				"the built-in OS (os.s)" if int(row["addr"]) < 0x3000 else "your file", row["source"]])
		_color_row(it)
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
	# Only what changed is touched: every TreeItem change redraws the whole
	# list, and states arrive up to 30 times a second while a program runs.
	if st["breakpoints"] != _bps_key:
		_bps_key = st["breakpoints"]
		var bps := {}
		for b in st["breakpoints"]:
			bps[int(b)] = true
		for a in items:
			var it: TreeItem = items[a]
			it.set_text(0, "●" if bps.has(a) else "")
	var addr := int(st["insn_addr"])
	var key := [addr, Palette.c("pc_row")]
	if key != _cur_key:
		_cur_key = key
		if cur_item != null:
			for c in COLUMNS.size():
				cur_item.clear_custom_bg_color(c)
			cur_item.set_text(1, "%04X" % int(cur_item.get_metadata(0)))
		cur_item = items.get(addr)
		if cur_item != null:
			for c in COLUMNS.size():
				cur_item.set_custom_bg_color(c, Palette.c("pc_row"))
			cur_item.set_text(1, "▶%04X" % addr)
	if cur_item != null and follow.button_pressed:
		tree.scroll_to_item(cur_item, true)
	_update_memory(st)


func _update_memory(st: Dictionary) -> void:
	if mem_view.is_visible_in_tree():
		mem_view.update_state(st)
