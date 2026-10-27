extends PanelContainer
## Bottom-left memory view.
##   Disassembly — follows the PC; click the ● column to toggle a breakpoint.
##                 The Source column is the line of the .s file each row came from.
##   Memory      — the memory explorer (memory_view.gd): a map of memory, the
##                 bytes coloured by what they are, the stack, and what the
##                 last step read or wrote.
## At Levels 3 and 4, while the CPU diagram is shown, the Disassembly makes
## way for the Memory tab: the walkthrough beside the diagram (walk_panel.gd)
## lists the program then, with the ▶ marker, the breakpoints and Skip to main.

const HelpButton := preload("res://scripts/help_button.gd")

var tabs: TabContainer
var tree: Tree
var follow: CheckBox
var mem_hint: Button       # "this step wrote 0x3024 · see it in the Memory tab"
var btn_main: Button       # Skip to main: run the OS's start-up (levels 2+)
var disasm_hidden := false   # the walkthrough lists the program instead (set_disasm_hidden)
var items := {}          # addr -> TreeItem
var cur_item: TreeItem = null
var os_item: TreeItem = null   # folds the built-in OS rows: closed at level 1, open from level 2
var _prog := {}
var _text_scale := 1.0
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
	tabs.tab_changed.connect(func(t):
		mem_hint.visible = mem_hint.visible and t == 0
		if Backend.state.get("loaded", false): _update_memory(Backend.state))
	set_level(Level.current)
	Level.changed.connect(func(): set_level(Level.current))
	if OS.get_cmdline_user_args().has("--memory-tab"):   # screenshots
		tabs.current_tab = 1
	_apply_palette()
	Palette.changed.connect(_apply_palette)


## Hide the Disassembly tab and show the Memory tab (the CPU diagram is on
## at Level 3 or 4, with the walkthrough beside it), or bring it back.
func set_disasm_hidden(on: bool) -> void:
	if on == disasm_hidden:
		return
	disasm_hidden = on
	tabs.current_tab = 1 if on else 0
	tabs.set_tab_hidden(0, on)
	if not on and Backend.state.get("loaded", false):
		update_state(Backend.state)


## The Memory tab, the Raw column and the OS rows appear from level 2
## (Memory & I/O); level 1 shows only the student's own program.
func set_level(n: int) -> void:
	tabs.set_tab_hidden(1, n < 2)
	if n < 2 and not disasm_hidden:
		tabs.current_tab = 0
	if tree != null:
		_size_columns()
		if not _prog.is_empty():
			set_program(_prog)


func _apply_palette() -> void:
	add_theme_stylebox_override("panel", Palette.panel_box("panel", 6, 6))
	for l in _dim_labels:
		l.add_theme_color_override("font_color", Palette.c("dim"))
	for it in items.values():
		_color_row(it)
	update_state(Backend.state)


func _on_text_scale(s: float) -> void:
	mem_view.set_text_scale(s)
	_text_scale = s
	_size_columns()


func _size_columns() -> void:
	var raw := Level.at_least(2)
	for c in COLUMNS:
		tree.set_column_custom_minimum_width(c[0], roundi(c[2] * _text_scale) if raw or c[0] != 2 else 0)
	tree.set_column_title(2, "Raw" if raw else "")


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
	hint.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	top.add_child(hint)
	mem_hint = Button.new()
	mem_hint.flat = true
	mem_hint.focus_mode = Control.FOCUS_NONE
	mem_hint.visible = false
	mem_hint.add_theme_font_size_override("font_size", 13)
	mem_hint.tooltip_text = "Open the Memory tab to see the bytes this step used"
	mem_hint.pressed.connect(func(): tabs.current_tab = 1)
	top.add_child(mem_hint)
	btn_main = Button.new()
	btn_main.text = "Skip to main ▸"
	btn_main.visible = false
	btn_main.focus_mode = Control.FOCUS_NONE
	btn_main.add_theme_font_size_override("font_size", 13)
	btn_main.tooltip_text = "Run the built-in OS's start-up instructions and stop at the first line of your main"
	btn_main.pressed.connect(func(): Backend.send("run_to_main"))
	top.add_child(btn_main)
	top.add_child(HelpButton.new("memory", "Disassembly"))

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
		tree.set_column_expand(c[0], c[3])
		tree.set_column_clip_content(c[0], true)
	_size_columns()
	tree.cell_selected.connect(_on_cell_selected)
	v.add_child(tree)
	return v


func _build_hex() -> Control:
	mem_view = MemoryView.new()
	return mem_view


# ── disassembly ─────────────────────────────────────────────────────────────

func set_program(prog: Dictionary) -> void:
	if prog != _prog:
		mem_view.set_program(prog)
	_prog = prog
	tree.clear()
	items.clear()
	cur_item = null
	os_item = null
	_bps_key = null
	_cur_key = null
	var root := tree.create_item()
	var raw := Level.at_least(2)
	var os_names: Array[String] = []
	for row in prog["listing"]:
		if row.get("os", false) and os_item == null:
			os_item = tree.create_item(root)
			os_item.set_selectable(0, false)
			os_item.set_tooltip_text(4, "picosim's built-in OS: it starts your main and provides putchar and getchar. Click the arrow to open it.")
		var it := tree.create_item(os_item if row.get("os", false) else root)
		var a := int(row["addr"])
		if row.get("os", false) and str(row["label"]) not in ["", "_start"] and not str(row["label"]).begins_with("."):
			os_names.append(str(row["label"]))
		it.set_text(1, "%04X" % a)
		if raw:
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
	if os_item != null:
		os_item.set_text(3, "built-in OS")
		os_item.set_text(4, ", ".join(os_names) if not os_names.is_empty() else "start-up code")
		os_item.set_text(5, "" if Level.at_least(2) else "(hidden at Level 1 · click › to look inside)")
		for col in [3, 4, 5]:
			os_item.set_custom_color(col, Palette.c("dim"))
		os_item.collapsed = not Level.at_least(2)
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
			cur_item.set_text(1, "" if cur_item == os_item else "%04X" % int(cur_item.get_metadata(0)))
		cur_item = items.get(addr)
		# inside the closed OS fold, mark the fold itself
		if cur_item != null and cur_item.get_parent() == os_item and os_item.collapsed:
			cur_item = os_item
		if cur_item != null:
			for c in COLUMNS.size():
				cur_item.set_custom_bg_color(c, Palette.c("pc_row"))
			cur_item.set_text(1, "▶%04X" % addr)
	if cur_item != null and follow.button_pressed:
		tree.scroll_to_item(cur_item, true)
	_show_mem_hint(st)
	btn_main.visible = Level.at_least(2) and st.get("at_boot", false) and not st.get("running", false)
	_update_memory(st)


## Point to the Memory tab when the last step read or wrote memory.
func _show_mem_hint(st: Dictionary) -> void:
	var acc: Array = st.get("mem_access", []).filter(func(a): return int(a["addr"]) < 0x40000000)   # not I/O registers
	var on := Level.at_least(2) and tabs.current_tab == 0 and not acc.is_empty()
	if on:
		var a: Dictionary = acc[-1]
		Backend.set_text(mem_hint, "this step %s 0x%04X · see it in the Memory tab ▸" % [
			"wrote" if a["kind"] == "write" else "read", int(a["addr"])])
		Backend.set_color(mem_hint, "font_color", Palette.c("accent"))
	mem_hint.visible = on


func _update_memory(st: Dictionary) -> void:
	if mem_view.is_visible_in_tree():
		mem_view.update_state(st)
