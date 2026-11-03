extends Node
## Testing window (the mode menu's "Testing"): picosim next to a real RP2040.
##
## A second Pico running Raspberry Pi's debugprobe firmware (or a Raspberry
## Pi Debug Probe) is wired to the target Pico's SWD pins; picosim starts
## OpenOCD for it, loads each test into the target's SRAM and runs it there
## and in the simulator at once (picosim/hwtest).
##
##   left    the connection, the tests (the hwtests/ suite and the loaded
##           program) with their last result, and how to run them
##   right   the selected test's result: for a trace, every step with what
##           picosim and the chip did side by side, and the full registers,
##           flags and memory writes of the step you click; for a benchmark,
##           each timed region's cycles in picosim and on the chip
##
## Everything comes from the backend's hw_* commands as {"type": "hw"}
## messages (picosim/hwtest/session.py).

const HelpButton := preload("res://scripts/help_button.gd")
const KINDS := ["openocd", "gdb", "standin"]
const LOADED := "loaded"            # results key prefix for the loaded program
const MAX_ROWS := 20000             # trace rows kept on screen

var win: Window
var bg: PanelContainer
var kind_select: OptionButton
var addr_edit: LineEdit
var speed_box: HBoxContainer
var speed_spin: SpinBox
var btn_connect: Button
var conn_label: RichTextLabel
var test_tree: Tree
var btn_run_sel: Button
var btn_run_all: Button
var btn_stop: Button
var btn_open: Button
var btn_report: Button
var steps_spin: SpinBox
var stop_check: CheckBox
var input_edit: LineEdit
var head: RichTextLabel
var table: Tree
var detail: RichTextLabel
var save_dialog: FileDialog

var info := {}            # the last "info" message: suite, openocd, standin
var status := {}          # the connection
var results := {}         # key -> {kind, title, path, rows, regions, summary, error, state}
var shown := ""           # key on display
var running := ""         # key of the test running
var _queue: Array = []    # keys sent in the current run, in order
var _items := {}          # key -> TreeItem in test_tree
var _row_items: Array = []
var _demo_run := ""
var _demo_show := ""
var _demo_standin := false


func _ready() -> void:
	win = Window.new()
	win.title = "picosim — Testing: picosim vs. RP2040"
	win.visible = false
	win.transient = true
	win.min_size = Vector2i(900, 560)
	win.close_requested.connect(win.hide)
	win.window_input.connect(_on_window_input)
	add_child(win)
	bg = PanelContainer.new()
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	win.add_child(bg)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	bg.add_child(v)
	v.add_child(_build_connection())
	var split := HSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	split.split_offset = 0
	v.add_child(split)
	split.add_child(_build_tests())
	split.add_child(_build_results())
	save_dialog = FileDialog.new()
	save_dialog.file_mode = FileDialog.FILE_MODE_SAVE_FILE
	save_dialog.access = FileDialog.ACCESS_FILESYSTEM
	save_dialog.use_native_dialog = true
	save_dialog.filters = PackedStringArray(["*.md ; Markdown report (the full data is saved next to it as .json)"])
	save_dialog.title = "Save the test report"
	save_dialog.current_file = "picosim-rp2040-report.md"
	save_dialog.file_selected.connect(func(p): Backend.send("hw_export", {"path": p}))
	add_child(save_dialog)
	Backend.hw_message.connect(_on_hw)
	Backend.program_loaded.connect(func(_p): _refresh_tests())
	Backend.connected.connect(func(): Backend.send("hw_info"))
	_apply_palette()
	Palette.changed.connect(_apply_palette)
	_show_empty()
	# screenshots and demos: --testing opens the window; --testing-standin
	# connects the stand-in, --testing-run a,b (file names, or "all") runs
	# tests once connected, --testing-show NAME[:ROW] shows one at the end
	var args := OS.get_cmdline_user_args()
	if args.has("--testing"):
		open.call_deferred()
	for i in args.size():
		if args[i] == "--testing-run" and i + 1 < args.size():
			_demo_run = args[i + 1]
		if args[i] == "--testing-show" and i + 1 < args.size():
			_demo_show = args[i + 1]
	_demo_standin = args.has("--testing-standin")


func open() -> void:
	var area := get_viewport().get_visible_rect().size
	win.popup_centered(Vector2i(mini(int(area.x * 0.9), 1500), int(area.y * 0.88)))
	Backend.send("hw_info")


func _on_window_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k and k.pressed and not k.echo and k.keycode == KEY_ESCAPE:
		win.hide()
		win.set_input_as_handled()


# ── building ────────────────────────────────────────────────────────────────

func _button(text: String, tip: String, cb: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.tooltip_text = tip
	b.focus_mode = Control.FOCUS_NONE
	b.pressed.connect(cb)
	return b


func _label(text: String, color := "muted") -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_color_override("font_color", Palette.c(color))
	return l


func _rich(fit := true) -> RichTextLabel:
	var r := RichTextLabel.new()
	r.bbcode_enabled = true
	r.selection_enabled = true
	r.fit_content = fit
	r.scroll_active = not fit
	r.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	r.add_theme_font_override("mono_font", Backend.mono_font())
	for k in ["normal_font_size", "bold_font_size", "mono_font_size"]:
		r.add_theme_font_size_override(k, 14)
	return r


func _build_connection() -> Control:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 8)
	h.add_child(_label("Connect to:"))
	kind_select = OptionButton.new()
	kind_select.add_item("Debug probe (OpenOCD)", 0)
	kind_select.add_item("GDB server already running", 1)
	kind_select.add_item("Stand-in (no hardware)", 2)
	kind_select.focus_mode = Control.FOCUS_NONE
	kind_select.tooltip_text = "Debug probe — a second Pico with the debugprobe firmware (or a Raspberry Pi Debug Probe) wired to the target Pico's SWD pins; picosim starts OpenOCD for it\nGDB server — OpenOCD, pyOCD or another GDB server you started yourself (host:port)\nStand-in — an emulator in place of the chip, to try Testing without hardware (it is not the real chip)"
	kind_select.item_selected.connect(func(_i): _update_controls())
	h.add_child(kind_select)
	addr_edit = LineEdit.new()
	addr_edit.text = "127.0.0.1:3333"
	addr_edit.custom_minimum_size.x = 160
	addr_edit.tooltip_text = "Where the GDB server listens (host:port)"
	h.add_child(addr_edit)
	speed_box = HBoxContainer.new()
	speed_box.add_child(_label("SWD kHz"))
	speed_spin = SpinBox.new()
	speed_spin.min_value = 100
	speed_spin.max_value = 24000
	speed_spin.step = 100
	speed_spin.value = 5000
	speed_spin.tooltip_text = "SWD clock. 5000 suits short wires; lower it if loading reports errors."
	speed_box.add_child(speed_spin)
	h.add_child(speed_box)
	btn_connect = _button("Connect", "Connect to the chip (Disconnect stops OpenOCD)", _on_connect)
	h.add_child(btn_connect)
	conn_label = _rich()
	conn_label.custom_minimum_size.x = 200
	conn_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	h.add_child(conn_label)
	h.add_child(HelpButton.new("testing", "Testing: picosim vs. RP2040"))
	return h


func _build_tests() -> Control:
	var v := VBoxContainer.new()
	v.custom_minimum_size.x = 380
	v.add_theme_constant_override("separation", 6)
	v.add_child(_label("Tests", "accent"))
	test_tree = Tree.new()
	test_tree.columns = 2
	test_tree.hide_root = true
	test_tree.size_flags_vertical = Control.SIZE_EXPAND_FILL
	test_tree.set_column_expand(0, true)
	test_tree.set_column_expand(1, false)
	test_tree.set_column_custom_minimum_width(1, 96)
	test_tree.item_selected.connect(_on_test_selected)
	test_tree.item_activated.connect(func(): _run(_selected_keys(true)))
	v.add_child(test_tree)
	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("h_separation", 8)
	grid.add_child(_label("Step limit"))
	steps_spin = SpinBox.new()
	steps_spin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	steps_spin.min_value = 100
	steps_spin.max_value = 1000000
	steps_spin.step = 100
	steps_spin.value = 20000
	steps_spin.tooltip_text = "A trace stops after this many steps (a program that never ends, like a blinking LED, stops here). Everything up to the limit is compared."
	grid.add_child(steps_spin)
	grid.add_child(_label("Input"))
	input_edit = LineEdit.new()
	input_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	input_edit.placeholder_text = "what getchar returns, e.g. hi\\n"
	input_edit.tooltip_text = "Characters getchar returns, in order, on both machines (\\n is a new line). A test's own @ Input: line wins."
	grid.add_child(input_edit)
	v.add_child(grid)
	stop_check = CheckBox.new()
	stop_check.text = "Stop at the first difference"
	stop_check.tooltip_text = "Off: after a difference picosim takes the chip's values and carries on, so you see every difference in one run."
	stop_check.focus_mode = Control.FOCUS_NONE
	v.add_child(stop_check)
	var row1 := HBoxContainer.new()
	btn_run_sel = _button("Run ticked", "Run the tests that are ticked, one after the other (double-click a test to run just that one)", func(): _run(_selected_keys(false)))
	btn_run_all = _button("Run all", "Run every test in the suite", func(): _run(_suite_keys()))
	btn_stop = _button("Stop", "Stop the run after the current step", func(): Backend.send("hw_stop"))
	for b in [btn_run_sel, btn_run_all, btn_stop]:
		b.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row1.add_child(b)
	v.add_child(row1)
	var row2 := HBoxContainer.new()
	btn_open = _button("Open in picosim", "Load the selected test in the main window, to step through it there", _on_open_test)
	btn_report = _button("Save report…", "Save the results as a Markdown report (and the full data as JSON)", func(): save_dialog.popup_centered_ratio(0.6))
	var btn_log := _button("Log", "Open the session log: every message to and from the debugger, and everything OpenOCD printed", func():
		if str(status.get("log", "")) != "": OS.shell_open(str(status["log"])))
	for b in [btn_open, btn_report, btn_log]:
		b.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row2.add_child(b)
	v.add_child(row2)
	return v


func _build_results() -> Control:
	var v := VBoxContainer.new()
	v.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	v.add_theme_constant_override("separation", 6)
	head = _rich()
	head.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(head)
	var vs := VSplitContainer.new()
	vs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	v.add_child(vs)
	table = Tree.new()
	table.hide_root = true
	table.column_titles_visible = true
	table.select_mode = Tree.SELECT_ROW
	table.size_flags_vertical = Control.SIZE_EXPAND_FILL
	table.custom_minimum_size.y = 200
	table.add_theme_font_override("font", Backend.mono_font())
	table.add_theme_font_size_override("font_size", 13)
	table.item_selected.connect(_on_row_selected)
	vs.add_child(table)
	var sc := ScrollContainer.new()
	sc.custom_minimum_size.y = 220
	sc.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	detail = _rich()
	detail.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	sc.add_child(detail)
	vs.add_child(sc)
	return v


func _apply_palette() -> void:
	win.theme = Palette.ui_theme
	bg.add_theme_stylebox_override("panel", Palette.panel_box("bg", 0, 10))
	_refresh_tests()
	_show(shown)
	_update_controls()


# ── the test list ───────────────────────────────────────────────────────────

func _key(path: String, kind: String, loaded: bool) -> String:
	return (LOADED if loaded else path) + "|" + kind


func _refresh_tests() -> void:
	if test_tree == null:
		return
	var ticked := {}
	for k in _items:
		var it: TreeItem = _items[k]
		if it.is_checked(0):
			ticked[k] = true
	test_tree.clear()
	_items.clear()
	var root := test_tree.create_item()
	var loaded := test_tree.create_item(root)
	loaded.set_text(0, "The loaded program")
	loaded.set_selectable(0, false)
	loaded.set_selectable(1, false)
	loaded.set_custom_color(0, Palette.c("accent"))
	var prog := str(Backend.state.get("path", "")).get_file()
	for kind in ["trace", "bench"]:
		var k := _key("", kind, true)
		var it := _add_test(loaded, k, ("Step by step" if kind == "trace" else "Timing") +
			(" — " + prog if prog != "" else ""),
			"Compare the program loaded in the main window: " + ("every step" if kind == "trace" else "its bench_start / bench_stop regions"))
		it.set_meta("path", "")
		it.set_meta("kind", kind)
	var groups := {"trace": "Step by step (registers, flags, memory)", "bench": "Timing (cycles)"}
	for kind in ["trace", "bench"]:
		var g := test_tree.create_item(root)
		g.set_text(0, groups[kind])
		g.set_selectable(0, false)
		g.set_selectable(1, false)
		g.set_custom_color(0, Palette.c("accent"))
		for t in info.get("suite", []):
			if t["kind"] != kind:
				continue
			var k := _key(t["path"], kind, false)
			var it := _add_test(g, k, t["title"], t["about"])
			it.set_meta("path", t["path"])
			it.set_meta("kind", kind)
	for k in ticked:
		if _items.has(k):
			(_items[k] as TreeItem).set_checked(0, true)
	if _items.has(shown):
		(_items[shown] as TreeItem).select(0)


func _add_test(parent: TreeItem, key: String, title: String, about: String) -> TreeItem:
	var it := test_tree.create_item(parent)
	it.set_cell_mode(0, TreeItem.CELL_MODE_CHECK)
	it.set_editable(0, true)
	it.set_text(0, title)
	it.set_tooltip_text(0, about)
	it.set_meta("key", key)
	_items[key] = it
	_mark(key)
	return it


func _mark(key: String) -> void:
	if not _items.has(key):
		return
	var it: TreeItem = _items[key]
	var r: Dictionary = results.get(key, {})
	var text := ""
	var col := "dim"
	if key == running:
		text = "running…"
		col = "accent"
	elif r.get("error", "") != "":
		text = "✗ error"
		col = "error"
	elif r.has("summary"):
		var v: String = r["summary"].get("verdict", "")
		text = {"identical": "✓ same", "different": "✗ differs"}.get(v, v)
		col = {"identical": "green", "different": "error"}.get(v, "dim")
	elif r.get("state", "") == "not run":
		text = "not run"
	elif key in _queue:
		text = "waiting"
	it.set_text(1, text)
	it.set_custom_color(1, Palette.c(col))


func _selected_keys(only_selected: bool) -> Array:
	var out := []
	if only_selected:
		var it := test_tree.get_selected()
		if it and it.has_meta("key"):
			out.append(it.get_meta("key"))
		return out
	for k in _items:
		if (_items[k] as TreeItem).is_checked(0):
			out.append(k)
	return out


func _suite_keys() -> Array:
	var out := []
	for k in _items:
		if not str(k).begins_with(LOADED):
			out.append(k)
	return out


func _on_test_selected() -> void:
	var it := test_tree.get_selected()
	if it and it.has_meta("key"):
		_show(it.get_meta("key"))
	_update_controls()


func _on_open_test() -> void:
	var it := test_tree.get_selected()
	if it and str(it.get_meta("path", "")) != "":
		Backend.send("load", {"path": it.get_meta("path")})


# ── running ─────────────────────────────────────────────────────────────────

func _on_connect() -> void:
	if status.get("connected", "") != "":
		Backend.send("hw_disconnect")
		return
	var msg := {"kind": KINDS[kind_select.selected], "speed": int(speed_spin.value)}
	if kind_select.selected == 1:
		var parts := addr_edit.text.strip_edges().rsplit(":", true, 1)
		msg["host"] = parts[0] if parts.size() == 2 and parts[0] != "" else "127.0.0.1"
		msg["port"] = int(parts[-1]) if parts[-1].is_valid_int() else 3333
	status["busy"] = true
	status["error"] = ""
	_show_connection("Connecting…", "muted")
	Backend.send("hw_connect", msg)
	_update_controls()


func _run(keys: Array) -> void:
	if keys.is_empty():
		head.text = "[color=%s]Tick the tests to run first (or double-click one).[/color]" % Palette.hex("muted")
		return
	var tests := []
	_queue = []
	for k in keys:
		var it: TreeItem = _items[k]
		tests.append({"path": it.get_meta("path"), "kind": it.get_meta("kind"), "title": it.get_text(0)})
		_queue.append(k)
		results[k] = {"kind": it.get_meta("kind"), "title": it.get_text(0), "path": it.get_meta("path"),
			"rows": [], "regions": [], "state": "waiting"}
		_mark(k)
	var inp := input_edit.text.c_unescape()
	Backend.send("hw_run", {"tests": tests, "input": inp, "max_steps": int(steps_spin.value),
		"stop_on_diff": stop_check.button_pressed})
	status["busy"] = true
	_update_controls()


func _on_hw(msg: Dictionary) -> void:
	match str(msg.get("event", "")):
		"info":
			info = msg
			_set_status(msg)
			_refresh_tests()
			if _demo_standin:
				_demo_standin = false
				kind_select.select(2)
				_on_connect()
		"status":
			_set_status(msg)
			if _demo_run != "" and status.get("connected", "") != "" and not status.get("busy", false) and not info.is_empty():
				var want := _demo_run.split(",")
				_demo_run = ""
				var keys := []
				for k in _suite_keys():
					if want.has("all") or want.has(str(k).get_slice("|", 0).get_file().get_basename()):
						keys.append(k)
				_run(keys)
		"start":
			if msg.get("retry", false) and running != "":
				# the connection was lost and remade: the test starts again
				results[running]["rows"] = []
				results[running]["regions"] = []
				if shown == running:
					_show(running)
				return
			running = _queue.pop_front() if not _queue.is_empty() else ""
			if running != "":
				var r: Dictionary = results[running]
				r["state"] = "running"
				r["path_run"] = msg.get("path", "")
				_mark(running)
				if shown == "" or shown == running or not results.get(shown, {}).has("summary"):
					_select(running)
		"steps":
			if running == "":
				return
			var r: Dictionary = results[running]
			var rows: Array = r["rows"]
			for row in msg.get("rows", []):
				if rows.size() < MAX_ROWS:
					rows.append(row)
			if shown == running:
				_append_rows()
				_show_head()
		"region":
			if running == "":
				return
			results[running]["regions"].append(msg.get("row", {}))
			if shown == running:
				_show(shown)
		"summary":
			if running == "":
				return
			results[running]["summary"] = msg.get("summary", {})
			var k := running
			running = ""
			_mark(k)
			if shown == k:
				_show(k)
		"error":
			var k := running
			if k == "":
				head.text = "[color=%s]%s[/color]" % [Palette.hex("error"), _esc(str(msg.get("message", "")))]
				return
			results[k]["error"] = str(msg.get("message", ""))
			running = ""
			_mark(k)
			if shown == k:
				_show(k)
		"done":
			running = ""
			for k in _queue:
				results[k]["state"] = "not run"
				_mark(k)
			_queue = []
			status["busy"] = false
			_update_controls()
			if _demo_show != "":
				_demo_select(_demo_show)
		"exported":
			var paths: Array = msg.get("paths", [])
			_show_connection("Report saved: " + ", ".join(paths.map(func(p): return str(p).get_file())), "green")


func _select(key: String) -> void:
	if _items.has(key):
		(_items[key] as TreeItem).select(0)
	_show(key)


func _set_status(msg: Dictionary) -> void:
	for k in ["connected", "kind", "busy", "error", "calibration", "log"]:
		if msg.has(k):
			status[k] = msg[k]
	var c: String = status.get("connected", "")
	if status.get("error", "") != "":
		_show_connection(str(status["error"]), "error")
	elif c != "":
		var t := "● " + c
		if status.get("kind", "") == "standin":
			t += " — not the real chip: timing and peripherals are picosim's own model"
		_show_connection(t, "yellow" if status.get("kind", "") == "standin" else "green")
	elif status.get("busy", false):
		_show_connection("Connecting…", "muted")
	else:
		var tip := "Not connected."
		if kind_select.selected == 0 and info.has("openocd") and str(info["openocd"]) == "":
			tip += " OpenOCD was not found: install it (macOS: brew install open-ocd), or choose Stand-in to try Testing without hardware."
		_show_connection(tip, "muted")
	_update_controls()


func _show_connection(text: String, color: String) -> void:
	conn_label.text = "[color=%s]%s[/color]" % [Palette.hex(color), _esc(text)]


func _update_controls() -> void:
	if btn_connect == null:
		return
	var connected: bool = status.get("connected", "") != ""
	var busy: bool = status.get("busy", false)
	addr_edit.visible = kind_select.selected == 1
	speed_box.visible = kind_select.selected == 0
	kind_select.disabled = connected or busy
	btn_connect.text = "Disconnect" if connected else "Connect"
	btn_connect.disabled = busy and not connected
	btn_run_sel.disabled = not connected or busy
	btn_run_all.disabled = not connected or busy
	btn_stop.disabled = not busy
	var it := test_tree.get_selected()
	btn_open.disabled = not (it and str(it.get_meta("path", "")) != "")


# ── showing a result ────────────────────────────────────────────────────────

func _show_empty() -> void:
	head.text = ("[b]Testing: picosim next to a real RP2040[/b]\n" +
		"[color=%s]Connect to a chip, tick tests on the left and click Run. Each test runs on the chip and in picosim at the same time. " +
		"[b]Step by step[/b] tests compare every register, flag and memory write after every instruction; [b]Timing[/b] tests compare clock cycles. " +
		"The ? at the top right explains the wiring and what each result means.[/color]") % Palette.hex("muted")
	table.clear()
	table.columns = 1
	detail.text = ""


func _show(key: String) -> void:
	shown = key
	if not results.has(key):
		_show_empty()
		if _items.has(key):
			head.text += "\n\n[b]%s[/b]  [color=%s]%s[/color]" % [_esc((_items[key] as TreeItem).get_text(0)),
				Palette.hex("dim"), _esc((_items[key] as TreeItem).get_tooltip_text(0))]
		return
	var r: Dictionary = results[key]
	table.clear()
	_row_items = []
	if r["kind"] == "bench":
		_setup_columns(["#", "Region", "picosim", "RP2040", "Difference", "Per instr.", "Instructions"], [1, 6, 2, 2, 2, 2, 2])
		var root := table.create_item()
		for g in _regions(r):
			_add_region(root, g)
	else:
		_setup_columns(["Step", "Address", "Instruction", "picosim", "RP2040", ""], [1, 1, 4, 5, 5, 1])
		table.create_item()
		_append_rows()
	_show_head()
	detail.text = "[color=%s]Click a row to see %s.[/color]" % [Palette.hex("dim"),
		"the whole machine after that step, picosim and the chip side by side" if r["kind"] != "bench" else "what the region is made of"]


func _regions(r: Dictionary) -> Array:
	if r.has("summary"):
		return r["summary"].get("regions", [])
	return r["regions"]


func _setup_columns(titles: Array, ratios: Array) -> void:
	table.columns = titles.size()
	for i in titles.size():
		table.set_column_title(i, titles[i])
		table.set_column_title_alignment(i, HORIZONTAL_ALIGNMENT_LEFT)
		table.set_column_expand(i, true)
		table.set_column_expand_ratio(i, ratios[i])
		table.set_column_clip_content(i, true)


func _append_rows() -> void:
	var r: Dictionary = results.get(shown, {})
	if r.is_empty() or r["kind"] == "bench":
		return
	var rows: Array = r["rows"]
	var root := table.get_root()
	if root == null:
		root = table.create_item()
	for i in range(_row_items.size(), rows.size()):
		var row: Dictionary = rows[i]
		var prev: Dictionary = rows[i - 1] if i > 0 else {}
		var it := table.create_item(root)
		it.set_meta("i", i)
		it.set_text(0, str(int(row["n"])))
		it.set_text(1, "%04X" % int(row["addr"]))
		it.set_text(2, str(row["text"]))
		it.set_tooltip_text(2, ("line %d: %s" % [int(row["line"]), row["src"]]) if int(row.get("line", 0)) > 0 else "the built-in OS")
		it.set_text(3, _brief(row, prev, "sim"))
		it.set_text(4, _brief(row, prev, "hw"))
		var st: String = row["status"]
		it.set_text(5, {"same": "✓", "call": "call", "io": "I/O", "diff": "✗"}.get(st, st))
		var col: String = {"same": "green", "call": "dim", "io": "yellow", "diff": "error"}.get(st, "text")
		it.set_custom_color(5, Palette.c(col))
		if st == "diff":
			for c in [3, 4]:
				it.set_custom_color(c, Palette.c("error"))
		elif st == "io":
			it.set_custom_color(4, Palette.c("yellow"))
		_row_items.append(it)


## What a step changed on one machine: the registers that changed (or
## differ), the flags if they changed, the bytes written.
func _brief(row: Dictionary, prev: Dictionary, side: String) -> String:
	var s: Dictionary = row.get(side, {})
	if s.is_empty():
		return ""
	var regs: Array = s["regs"]
	var before: Array = prev.get("sim", {}).get("regs", []) if not prev.is_empty() else []
	var parts := []
	var diffs: Array = row.get("diffs", [])
	for i in 15:
		var name := _reg_name(i)
		var changed := before.size() == 16 and int(before[i]) != int(row["sim"]["regs"][i])
		if changed or name in diffs:
			parts.append("%s=%s" % [name, _hex(int(regs[i]))])
	var pf: int = prev.get("sim", {}).get("flags", -1) if not prev.is_empty() else -1
	if (pf >= 0 and pf != int(row["sim"]["flags"])) or diffs.any(func(d): return str(d).length() == 1):
		parts.append(_flags(int(s["flags"])))
	for w in s.get("writes", []):
		if w[2] == null:
			parts.append("[%04X]←?" % int(w[0]))
		else:
			parts.append("[%04X]←%s" % [int(w[0]), _hexw(int(w[2]), int(w[1]))])
	if s.get("printed", "") != "":
		parts.append("prints %s" % _show_char(str(s["printed"])))
	if row.has("fault") and row["fault"].get(side) != null:
		parts.append("FAULT")
	return "  ".join(parts)


func _add_region(root: TreeItem, g: Dictionary) -> void:
	var it := table.create_item(root)
	it.set_meta("region", g)
	it.set_text(0, str(int(g.get("k", 0))))
	it.set_text(1, str(g.get("name", "")))
	it.set_text(2, _num(g.get("sim")))
	it.set_text(3, _num(g.get("hw")))
	var d = g.get("diff")
	it.set_text(4, "" if d == null else ("%+d" % int(d)))
	it.set_text(5, ("%+.2f" % float(g["per"])) if g.has("per") else "")
	it.set_text(6, str(int(g.get("insns", 0))))
	if d != null:
		it.set_custom_color(4, Palette.c("green" if int(d) == 0 else "error"))
		it.set_custom_color(5, Palette.c("green" if int(d) == 0 else "error"))
	if not g.get("notes", []).is_empty():
		it.set_custom_color(1, Palette.c("yellow"))
	_row_items.append(it)


func _show_head() -> void:
	var r: Dictionary = results.get(shown, {})
	if r.is_empty():
		return
	var t := "[b]%s[/b]   " % _esc(str(r["title"]))
	if r.get("error", "") != "":
		head.text = t + "[color=%s]could not run: %s[/color]" % [Palette.hex("error"), _esc(str(r["error"]))]
		return
	if not r.has("summary"):
		var n: int = (r["rows"] as Array).size() if r["kind"] != "bench" else (r["regions"] as Array).size()
		head.text = t + "[color=%s]%s… %d %s so far[/color]" % [Palette.hex("accent"),
			"running" if r.get("state", "") == "running" else "waiting", n, "steps" if r["kind"] != "bench" else "regions"]
		return
	var s: Dictionary = r["summary"]
	var v: String = s.get("verdict", "")
	t += "[color=%s][b]%s[/b][/color]   " % [Palette.hex({"identical": "green", "different": "error"}.get(v, "dim")),
		{"identical": "✓ identical", "different": "✗ different"}.get(v, v)]
	t += "[color=%s]%s[/color]\n" % [Palette.hex("muted"), _esc(str(s.get("note", "")))]
	if r["kind"] == "bench":
		var cal = s.get("calibration")
		if cal is Dictionary:
			var ov: Dictionary = cal.get("overhead", {})
			t += "[color=%s]Each region's RP2040 count has the measuring overhead taken off: %s cycles (Table 81 predicts %d). Positive differences mean the chip is slower than picosim's model.[/color]" % [
				Palette.hex("dim"), ", ".join(ov.values().map(func(x): return str(int(x)))), int(cal.get("predicted", 0))]
	else:
		var c: Dictionary = s.get("counts", {})
		var m: Dictionary = s.get("memory", {})
		t += "%d steps: [color=%s]%d identical[/color], %d OS calls, [color=%s]%d I/O differences[/color], [color=%s]%d differences[/color].   " % [
			int(s.get("steps", 0)), Palette.hex("green"), int(c.get("same", 0)), int(c.get("call", 0)),
			Palette.hex("yellow"), int(c.get("io", 0)), Palette.hex("error" if int(c.get("diff", 0)) else "muted"), int(c.get("diff", 0))]
		t += "Memory 0x%04X–0x%04X: %s.   " % [int(m.get("start", 0)), int(m.get("end", 0)) - 1,
			"identical" if int(m.get("different_words", 0)) == 0 else "[color=%s]%d words differ[/color]" % [Palette.hex("error"), int(m.get("different_words", 0))]]
		var o: Dictionary = s.get("output", {})
		if str(o.get("sim", "")) != "" or str(o.get("hw", "")) != "":
			t += "Output %s." % ("identical" if o.get("sim") == o.get("hw") else "[color=%s]differs[/color]" % Palette.hex("error"))
		var g: Dictionary = s.get("gpio", {})
		if not g.get("diffs", []).is_empty():
			t += "   [color=%s]GPIO registers differ: %s[/color]" % [Palette.hex("error"), ", ".join(g["diffs"])]
	head.text = t


func _on_row_selected() -> void:
	var it := table.get_selected()
	if it == null:
		return
	if it.has_meta("region"):
		_show_region(it.get_meta("region"))
		return
	var r: Dictionary = results.get(shown, {})
	var i: int = it.get_meta("i", -1)
	if i < 0 or i >= (r.get("rows", []) as Array).size():
		return
	_show_step(r["rows"][i])


func _show_step(row: Dictionary) -> void:
	var diffs: Array = row.get("diffs", [])
	var t := "[b]Step %d[/b]   [code]%04X  %s[/code]" % [int(row["n"]), int(row["addr"]), _esc(str(row["text"]))]
	if int(row.get("line", 0)) > 0:
		t += "   [color=%s]line %d: %s[/color]" % [Palette.hex("dim"), int(row["line"]), _esc(str(row["src"]))]
	t += "\n"
	if row.get("note", "") != "":
		t += "[color=%s]%s[/color]\n" % [Palette.hex("yellow" if row["status"] == "io" else "muted"), _esc(str(row["note"]))]
	if row.has("fault"):
		var f: Dictionary = row["fault"]
		t += "[color=%s]picosim: %s\nRP2040: %s[/color]\n" % [Palette.hex("error"),
			_esc(str(f.get("sim"))) if f.get("sim") != null else "no fault", _esc(str(f.get("hw"))) if f.get("hw") != null else "no fault"]
	var sim: Dictionary = row.get("sim", {})
	var hw: Dictionary = row.get("hw", {})
	if sim.is_empty():
		detail.text = t
		return
	var cells := "[table=6]"
	for h in ["Register", "picosim", "RP2040", "Register", "picosim", "RP2040"]:
		cells += "[cell bg=%s padding=8,3,14,3][b]%s[/b][/cell]" % [Palette.hex("box_active"), h]
	var names := []
	for i in 16:
		names.append(i)
	names.append("flags")
	var half := ceili(names.size() / 2.0)
	for k in half:
		for side in 2:
			var j: int = k + side * half
			if j >= names.size():
				cells += "[cell][/cell][cell][/cell][cell][/cell]"
				continue
			var n = names[j]
			var name: String = "NZCV" if str(n) == "flags" else _reg_name(int(n))
			var a := _flags(int(sim["flags"])) if str(n) == "flags" else _hex(int(sim["regs"][n]))
			var b := _flags(int(hw["flags"])) if str(n) == "flags" else _hex(int(hw["regs"][n]))
			var bad: bool = name in diffs or (str(n) == "flags" and diffs.any(func(d): return str(d).length() == 1))
			var col := Palette.hex("error") if bad else Palette.hex("text")
			cells += "[cell padding=8,2,14,2][color=%s]%s[/color][/cell][cell padding=8,2,14,2][code]%s[/code][/cell][cell padding=8,2,14,2][color=%s][code]%s[/code][/color][/cell]" % [
				Palette.hex("muted"), name, a, col, b]
	cells += "[/table]\n"
	t += cells
	var writes: Array = sim.get("writes", [])
	if not writes.is_empty():
		t += "\n[b]Memory written[/b]   (address: picosim / RP2040 read back)\n"
		var hws: Array = hw.get("writes", [])
		for i in writes.size():
			var w: Array = writes[i]
			var hv = hws[i][2] if i < hws.size() else null
			var bad: bool = ("[%04X]" % int(w[0])) in diffs
			t += "[code]  %04X: %s / [color=%s]%s[/color][/code]\n" % [int(w[0]), _hexw(int(w[2]), int(w[1])),
				Palette.hex("error" if bad else "text"), "?" if hv == null else _hexw(int(hv), int(w[1]))]
	if not sim.get("io", []).is_empty():
		t += "\n[b]Peripheral writes[/b] (picosim): "
		var parts := []
		for w in sim["io"]:
			parts.append("[code]%08X ← %s[/code]" % [int(w[0]), _hex(int(w[2]))])
		t += ", ".join(parts) + "\n"
	t += "\n[color=%s]On the chip every address is picosim's + 0x20000000 (the program runs from SRAM); the RP2040 column shows those as picosim's addresses, so the two columns can be compared directly. The chip's real PC here: 0x%08X.[/color]" % [
		Palette.hex("dim"), int(hw.get("raw_pc", 0))]
	detail.text = t


func _show_region(g: Dictionary) -> void:
	var t := "[b]%s[/b]" % _esc(str(g.get("name", "")))
	if int(g.get("line", 0)) > 0:
		t += "   [color=%s]bench_start on line %d[/color]" % [Palette.hex("dim"), int(g["line"])]
	t += "\n"
	t += "picosim's RP2040 model (datasheet Table 81): [b]%s[/b] cycles   ·   measured on the chip: [b]%s[/b] cycles (SysTick %s, minus the calibration)   ·   picosim's multi-cycle teaching machine: %s cycles\n" % [
		_num(g.get("sim")), _num(g.get("hw")), _num(g.get("raw")), _num(g.get("multicycle"))]
	if g.has("per"):
		t += "%d repetitions: the chip takes [b]%+.2f[/b] cycles per repetition more than the model.\n" % [int(g["reps"]), float(g["per"])]
	var mix: Array = g.get("mix", [])
	if not mix.is_empty():
		t += "\n[b]Instructions in the region[/b] (Table 81 rows)\n"
		for m in mix:
			t += "[code]  %5d  %s[/code]\n" % [int(m[1]), _esc(str(m[0]))]
	for n in g.get("notes", []):
		t += "\n[color=%s]%s[/color]" % [Palette.hex("yellow"), _esc(str(n))]
	detail.text = t


func _demo_select(spec: String) -> void:
	var name := spec.get_slice(":", 0)
	for k in _items:
		if str(k).get_file().begins_with(name):
			_select(k)
			if spec.contains(":") and _row_items.size() > int(spec.get_slice(":", 1)):
				var it: TreeItem = _row_items[int(spec.get_slice(":", 1))]
				it.select(0)
				table.scroll_to_item(it)
			return


# ── formatting ──────────────────────────────────────────────────────────────

static func _reg_name(i: int) -> String:
	return "R%d" % i if i < 13 else ["SP", "LR", "PC"][i - 13]


static func _hex(v: int) -> String:
	return "%08X" % (v & 0xFFFFFFFF)


static func _hexw(v: int, w: int) -> String:
	return ("%0" + str(w * 2) + "X") % v


static func _flags(v: int) -> String:
	var out := ""
	for i in 4:
		out += "NZCV"[i] if v & (8 >> i) else "-"
	return out


static func _num(v) -> String:
	return "—" if v == null else str(int(v))


static func _show_char(s: String) -> String:
	return "'\\n'" if s == "\n" else "'%s'" % s


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")
