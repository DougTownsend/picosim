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
## The third group, "Hardware facts (Level 5 discovery)", lists the
## discovery campaigns (picosim/discover): each one's status, and for the
## one selected its summary, provenance and differences from picosim, each
## with an example that Open in picosim loads as a program.  Run and Resume
## start the same code as `python3 -m picosim.discover <campaign>`.
##
## Everything comes from the backend's hw_* commands as {"type": "hw"}
## messages (picosim/hwtest/session.py, picosim/discover/overview.py).

const HelpButton := preload("res://scripts/help_button.gd")
const KINDS := ["openocd", "gdb", "standin"]
const LOADED := "loaded"            # results key prefix for the loaded program
const MAX_ROWS := 20000             # trace rows kept on screen
const DISCOVER := "discover|"       # results key prefix for a discovery campaign
const CAMPAIGNS := ["identity", "memmap", "registers", "encodings", "timing", "fuzz", "flashed", "peripherals"]
const LETTERS := {"identity": "0", "memmap": "A", "registers": "C", "encodings": "B", "timing": "D",
	"fuzz": "E", "flashed": "F", "peripherals": "G"}
const MAX_LOG := 400                # log lines kept per campaign

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
var camp_bar: HBoxContainer         # the campaign's buttons (shown with a campaign)
var btn_camp_run: Button
var btn_camp_resume: Button
var btn_camp_stop: Button
var btn_camp_flash: Button
var camp_note: RichTextLabel
var flash_dialog: ConfirmationDialog
var flash_text: RichTextLabel
var ask_dialog: ConfirmationDialog
var fresh_dialog: ConfirmationDialog   # Run on a campaign that has measurements: start again, or resume?
var btn_fresh_resume: Button
var _fresh_name := ""
var btn_flash_backup: Button
var btn_flash_restore: Button

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
var _demo_discover := ""  # --testing-discover NAME[:ROW]: show a campaign (screenshots)
var _demo_flash := false  # --testing-flash: then open campaign F's confirmation

var discover := {}        # campaign -> its row (overview.row): state, differences, ...
var discover_kind := ""   # whose results the list shows: "standin" or "openocd"
var flash := {}           # campaign F's flash backup (overview.flash_status)
var flash_steps := {}     # the backup / run / restore steps, for F's confirmation
var camp := {}            # the detail of the campaign shown (overview.detail)
var camp_log := {}        # campaign -> its log lines this session
var camp_progress := {}   # campaign -> "B2: 3 of 8 batches"
var camp_running := ""    # the campaign running, if one is
var _camp_row := -1       # the difference shown in the detail pane
var camp_msg := {}        # campaign -> [text, colour]: how its last run ended
var _flash_fresh := false # campaign F: Run (from the start) or Resume, until it is confirmed


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
	_build_dialogs()
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
	_demo_flash = args.has("--testing-flash")
	var di := args.find("--testing-discover")
	if di >= 0 and di + 1 < args.size():
		_demo_discover = args[di + 1]


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
	kind_select.item_selected.connect(func(_i):
		_update_controls()
		_ask_discover_status())
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
	test_tree.set_column_custom_minimum_width(1, 132)
	test_tree.item_selected.connect(_on_test_selected)
	test_tree.item_activated.connect(func():
		var keys := _selected_keys(true)
		if not keys.is_empty() and not str(keys[0]).begins_with(DISCOVER):
			_run(keys))
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
	v.add_child(_build_campaign_bar())
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
	detail.meta_clicked.connect(_on_detail_meta)
	sc.add_child(detail)
	vs.add_child(sc)
	return v


## Run / Resume / Stop for the campaign shown, F's flash button, and a line
## for its progress or its last message.
func _build_campaign_bar() -> Control:
	camp_bar = HBoxContainer.new()
	camp_bar.add_theme_constant_override("separation", 8)
	camp_bar.visible = false
	btn_camp_run = _button("Run", "Run this campaign from the start (the command line's --fresh). It runs on the chip you are connected to.", func(): _camp_run(true))
	btn_camp_resume = _button("Resume", "Carry on where this campaign stopped: the batches already done are kept", func(): _camp_run(false))
	btn_camp_stop = _button("Stop", "Stop the campaign after the batch it is in; Resume carries on from there", func(): Backend.send("hw_discover_stop"))
	btn_camp_flash = _button("Flash backup…", "Campaign F writes the board's flash: see the backup and restore steps, back the flash up, or put it back", _open_flash_dialog)
	for b in [btn_camp_run, btn_camp_resume, btn_camp_stop, btn_camp_flash]:
		camp_bar.add_child(b)
	camp_note = _rich()
	camp_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	camp_note.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	camp_bar.add_child(camp_note)
	camp_bar.add_child(HelpButton.new("discovery", "Hardware facts (Level 5 discovery)"))
	return camp_bar


func _build_dialogs() -> void:
	flash_dialog = ConfirmationDialog.new()
	flash_dialog.title = "Campaign F writes the target Pico's flash"
	flash_dialog.ok_button_text = "Start campaign F"
	flash_dialog.cancel_button_text = "Cancel"
	flash_dialog.min_size = Vector2i(720, 520)
	btn_flash_backup = flash_dialog.add_button("Back up the flash now", true, "backup")
	btn_flash_restore = flash_dialog.add_button("Restore the flash now", true, "restore")
	flash_dialog.confirmed.connect(func(): _camp_send_run("flashed", false, true))
	flash_dialog.custom_action.connect(func(action):
		flash_dialog.hide()
		_camp_note("Starting the flash %s…" % action, "accent")
		Backend.send("hw_discover_flash", {"action": action}))
	var sc := ScrollContainer.new()
	sc.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	flash_text = _rich()
	flash_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	sc.add_child(flash_text)
	flash_dialog.add_child(sc)
	win.add_child(flash_dialog)
	ask_dialog = ConfirmationDialog.new()
	ask_dialog.title = "The campaign needs you"
	ask_dialog.ok_button_text = "Done: carry on"
	ask_dialog.cancel_button_text = "Stop the campaign"
	ask_dialog.dialog_autowrap = true
	ask_dialog.min_size = Vector2i(520, 200)
	ask_dialog.confirmed.connect(func(): Backend.send("hw_discover_answer"))
	ask_dialog.canceled.connect(func(): Backend.send("hw_discover_stop"))
	win.add_child(ask_dialog)
	# Run means --fresh.  On 2026-11-11 a Run of campaign B restarted B1 while
	# B2's finished run and B3's first 60 batches were waiting for a Resume.
	fresh_dialog = ConfirmationDialog.new()
	fresh_dialog.title = "Start this campaign again from the beginning?"
	fresh_dialog.ok_button_text = "Start again"
	fresh_dialog.cancel_button_text = "Cancel"
	fresh_dialog.dialog_autowrap = true
	fresh_dialog.min_size = Vector2i(600, 240)
	btn_fresh_resume = fresh_dialog.add_button("Resume instead", true, "resume")
	fresh_dialog.confirmed.connect(func(): _camp_send_run(_fresh_name, true, false))
	fresh_dialog.custom_action.connect(func(action):
		fresh_dialog.hide()
		if action == "resume":
			_camp_send_run(_fresh_name, false, false))
	win.add_child(fresh_dialog)


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
			"Compare the program loaded in the main window: " + ("every step" if kind == "trace" else "its bench_start / bench_stop regions, or, if it has none, every stretch of it between OS calls"))
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
			it.set_meta("calls", t.get("calls", ""))
			it.set_meta("kind", kind)
	_add_campaigns(root)
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
	if key.begins_with(DISCOVER):
		_mark_campaign(key.substr(DISCOVER.length()))
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
		if not str(k).begins_with(LOADED) and not str(k).begins_with(DISCOVER):
			out.append(k)
	return out


func _on_test_selected() -> void:
	var it := test_tree.get_selected()
	if it and it.has_meta("key"):
		_show(it.get_meta("key"))
	_update_controls()


func _on_open_test() -> void:
	if shown.begins_with(DISCOVER):
		_open_case(_camp_row)
		return
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
			_ask_discover_status()
			if _demo_standin:
				_demo_standin = false
				kind_select.select(2)
				_on_connect()
		"status":
			var kind_was := discover_kind
			_set_status(msg)
			if not status.get("busy", false) or _discover_kind() != kind_was:
				_ask_discover_status()
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
		var e when str(e).begins_with("discover_"):
			_on_discover(msg)


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
	if shown.begins_with(DISCOVER):
		btn_open.disabled = not _openable(_camp_row)
	_update_campaign_controls()


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
	if camp_bar:
		camp_bar.visible = key.begins_with(DISCOVER)
	if key.begins_with(DISCOVER):
		_show_campaign(key.substr(DISCOVER.length()))
		return
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
	var calls: String = (_items[key] as TreeItem).get_meta("calls", "") if _items.has(key) else ""
	if calls != "":
		detail.text += "\n\n[b]OS calls in this test[/b]\n%s%s" % [_esc(calls),
			"" if r["kind"] == "bench" else "\nClick a step marked [b]call[/b] to see what that call did on each machine, and how, why and when."]


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
		it.set_text(1, "%08X" % int(row["addr"]))
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
			parts.append("[%08X]←?" % int(w[0]))
		else:
			parts.append("[%08X]←%s" % [int(w[0]), _hexw(int(w[2]), int(w[1]))])
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
		t += "Memory (code, data, stack): %s.   " % [
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
	if it.has_meta("diff"):
		_show_difference(int(it.get_meta("diff")))
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
	var t := "[b]Step %d[/b]   [code]%08X  %s[/code]" % [int(row["n"]), int(row["addr"]), _esc(str(row["text"]))]
	if int(row.get("line", 0)) > 0:
		t += "   [color=%s]line %d: %s[/color]" % [Palette.hex("dim"), int(row["line"]), _esc(str(row["src"]))]
	t += "\n"
	if row.get("note", "") != "":
		t += "[color=%s]%s[/color]\n" % [Palette.hex("yellow" if row["status"] == "io" else "muted"), _esc(str(row["note"]))]
	if row.has("fault"):
		var f: Dictionary = row["fault"]
		t += "[color=%s]picosim: %s\nRP2040: %s[/color]\n" % [Palette.hex("error"),
			_esc(str(f.get("sim"))) if f.get("sim") != null else "no fault", _esc(str(f.get("hw"))) if f.get("hw") != null else "no fault"]
	if row.has("os"):
		t += _os_call(row["os"]) + "\n"
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
			var bad: bool = ("[0x%08X]" % int(w[0])) in diffs
			t += "[code]  %08X: %s / [color=%s]%s[/color][/code]\n" % [int(w[0]), _hexw(int(w[2]), int(w[1])),
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
	if g.get("stretch", false) and int(g.get("line", 0)) > 0:
		t += "   [color=%s]lines %d–%d[/color]" % [Palette.hex("dim"), int(g["line"]), int(g.get("stop_line", g["line"]))]
	elif int(g.get("line", 0)) > 0:
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
	if g.has("os"):
		t += "\n" + _os_call(g["os"])
	detail.text = t


## A call into the built-in OS (oscalls.py): what it did, how each machine
## did it, why it is done that way, and when it happened.
func _os_call(info: Dictionary) -> String:
	var t := "\n[b]The OS call: %s[/b]\n[table=2]" % _esc(str(info.get("name", "")))
	for k in [["what", "What"], ["how", "How"], ["why", "Why"], ["when", "When"]]:
		t += "[cell padding=0,3,12,3][color=%s][b]%s[/b][/color][/cell][cell expand=1 padding=0,3,0,3]%s[/cell]" % [
			Palette.hex("accent"), k[1], _esc(str(info.get(k[0], "")))]
	return t + "[/table]"


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


# ── hardware facts (Level 5 discovery) ─────────────────────────────────────

## Whose results the list shows: the connection's kind, or, with no
## connection, the one chosen in "Connect to".
func _discover_kind() -> String:
	var k: String = str(status.get("kind", "")) if str(status.get("connected", "")) != "" else KINDS[kind_select.selected]
	return "standin" if k == "standin" else "openocd"


func _ask_discover_status() -> void:
	Backend.send("hw_discover_status", {"kind": _discover_kind()})


func _add_campaigns(root: TreeItem) -> void:
	var g := test_tree.create_item(root)
	g.set_text(0, "Hardware facts (Level 5 discovery)")
	g.set_selectable(0, false)
	g.set_selectable(1, false)
	g.set_custom_color(0, Palette.c("accent"))
	var whose := "stand-in" if discover_kind == "standin" else "RP2040"
	g.set_text(1, whose)
	g.set_custom_color(1, Palette.c("dim"))
	g.set_tooltip_text(0, "What the discovery campaigns measured on the chip, and where picosim differs. These are the %s's results." % ("stand-in (an emulator, not the real chip)" if discover_kind == "standin" else "real chip"))
	for name in CAMPAIGNS:
		var r: Dictionary = discover.get(name, {})
		var it := test_tree.create_item(g)
		var title := str(r.get("title", name))
		it.set_text(0, "%s   %s" % [LETTERS[name], title.get_slice(":", 0)])
		it.set_tooltip_text(0, title + "\n" + str(r.get("about", "")))
		it.set_meta("key", DISCOVER + name)
		it.set_meta("campaign", name)
		_items[DISCOVER + name] = it
		_mark_campaign(name)


## [text, colour] of a campaign's status, for its row and its heading.
func _camp_state(name: String) -> Array:
	var r: Dictionary = discover.get(name, {})
	if name == camp_running:
		var n := " %d/%d" % [int(r["done"]), int(r["total"])] if r.has("done") and r.has("total") else ""
		return ["running…" + n, "accent"]
	match str(r.get("state", "not run")):
		"done":
			var d = r.get("differences")
			if d == null:
				return ["✓ done", "green"]
			if int(d) == 0:
				return ["✓ done · same", "green"]
			if str(r.get("compared_with", "")) == "datasheet":
				return ["done · %d vs datasheet" % int(d), "yellow"]   # picosim does not model these
			return ["done · %d differ" % int(d), "error"]
		"partial":
			if r.has("done") and r.has("total"):
				return ["partial %d/%d" % [int(r["done"]), int(r["total"])], "yellow"]
			return ["partial", "yellow"]
	if not r.is_empty() and not r.get("available", true):
		return ["not available", "dim"]
	return ["not run", "dim"]


func _mark_campaign(name: String) -> void:
	if not _items.has(DISCOVER + name):
		return
	var it: TreeItem = _items[DISCOVER + name]
	var st := _camp_state(name)
	it.set_text(1, st[0])
	it.set_custom_color(1, Palette.c(st[1]))
	var r: Dictionary = discover.get(name, {})
	var tip := str(r.get("about", ""))
	if str(r.get("step", "")) != "" and r.get("state", "") == "partial":
		tip += "\nStopped in: " + str(r["step"])
	if str(r.get("error", "")) != "":
		tip += "\nIts code could not be loaded: " + str(r["error"])
	it.set_tooltip_text(1, tip)


func _show_campaign(name: String) -> void:
	if str(camp.get("name", "")) != name:
		camp = {}
		_camp_row = -1
	Backend.send("hw_discover_detail", {"campaign": name, "kind": _discover_kind()})
	_render_campaign(name)


func _render_campaign(name: String) -> void:
	var r: Dictionary = camp if str(camp.get("name", "")) == name else discover.get(name, {})
	var st := _camp_state(name)
	var t := "[b]%s   %s[/b]   [color=%s][b]%s[/b][/color]" % [LETTERS[name], _esc(str(r.get("title", name))), Palette.hex(st[1]), st[0]]
	if r.get("state", "") == "done" and r.get("differences") != null and int(r["differences"]) > 0:
		t += "[color=%s] from %s[/color]" % [Palette.hex(st[1]),
			"the datasheet" if str(r.get("compared_with", "")) == "datasheet" else "picosim"]
	if str(r.get("step", "")) != "" and r.get("state", "") == "partial":
		t += "[color=%s]  (stopped in %s)[/color]" % [Palette.hex("dim"), _esc(str(r["step"]))]
	if str(r.get("updated", "")) != "":
		t += "   [color=%s]%s[/color]" % [Palette.hex("dim"), _esc(str(r["updated"]))]
	t += "\n[color=%s]%s[/color]" % [Palette.hex("muted"), _esc(str(r.get("about", "")))]
	if not r.is_empty() and not r.get("available", true):
		t += "\n[color=%s]Its code could not be loaded, so it can't run yet (%s).[/color]" % [Palette.hex("yellow"), _esc(str(r.get("error", "")))]
	var prov: Array = r.get("provenance", [])
	if not prov.is_empty():
		t += "\n[table=4]"
		for i in prov.size():
			var p: Array = prov[i]
			var col := "yellow" if str(p[0]) == "Source" else "text"
			t += "[cell padding=0,1,10,1][color=%s]%s[/color][/cell][cell padding=0,1,24,1][color=%s]%s[/color][/cell]" % [
				Palette.hex("dim"), _esc(str(p[0])), Palette.hex(col), _esc(str(p[1]))]
		if prov.size() % 2 == 1:
			t += "[cell][/cell][cell][/cell]"
		t += "[/table]"
	if str(r.get("facts_error", "")) != "":
		t += "\n[color=%s]%s[/color]" % [Palette.hex("error"), _esc(str(r["facts_error"]))]
	head.text = t
	_camp_table(r)
	if _camp_row >= 0 and _camp_row < (r.get("diffs", []) as Array).size():
		_show_difference(_camp_row)
	else:
		_camp_detail_default(name)
	_camp_note_default(name)
	_update_controls()


## The differences, or, if there are none, the facts measured.
func _camp_table(r: Dictionary) -> void:
	table.clear()
	_row_items = []
	var diffs: Array = r.get("diffs", [])
	var root := table.create_item()
	if not diffs.is_empty():
		_setup_columns(["#", "Kind", "What", "Cases", "What differs"], [1, 3, 6, 1, 12])
		for d in diffs:
			var it := table.create_item(root)
			it.set_meta("diff", int(d["index"]))
			it.set_text(0, str(int(d["index"]) + 1))
			var k := str(d.get("kind", ""))
			it.set_text(1, {"picosim": "differs", "expected": "not as expected", "pending": "pending"}.get(k, k))
			it.set_custom_color(1, Palette.c({"picosim": "error", "expected": "yellow"}.get(k, "dim")))
			it.set_text(2, str(d.get("what", "")))
			it.set_tooltip_text(2, str(d.get("what", "")) + ("\n" + str(d["where"]) if str(d.get("where", "")) != "" else ""))
			it.set_text(3, "" if d.get("cases") == null else str(int(d["cases"])))
			it.set_text(4, ("▶ " if d.get("openable", false) else "") + str(d.get("detail", "")))
			it.set_tooltip_text(4, str(d.get("detail", "")))
			_row_items.append(it)
		return
	var facts: Array = r.get("facts_rows", [])
	if facts.is_empty():
		table.columns = 1
		table.set_column_title(0, "")
		return
	_setup_columns(["Fact measured", "Value", "Expected"], [5, 5, 4])
	for f in facts:
		var it := table.create_item(root)
		it.set_text(0, str(f["id"]))
		it.set_text(1, str(f["value"]) + (" " + str(f["unit"]) if str(f.get("unit", "")) != "" else ""))
		it.set_tooltip_text(1, str(f["value"]) + ("\n" + str(f["note"]) if str(f.get("note", "")) != "" else ""))
		it.set_text(2, str(f.get("expected", "")))
		_row_items.append(it)


## Below the table when no difference is picked: what to do, the command
## line, campaign F's flash, and the log of this session's run.
func _camp_detail_default(name: String) -> void:
	var r: Dictionary = camp if str(camp.get("name", "")) == name else discover.get(name, {})
	var t := ""
	var diffs: Array = r.get("diffs", [])
	if not diffs.is_empty():
		var count := {"picosim": 0, "datasheet": 0, "expected": 0, "pending": 0}
		var openable := false
		for d in diffs:
			count[str(d.get("kind", "pending"))] = int(count.get(str(d.get("kind", "pending")), 0)) + 1
			openable = openable or d.get("openable", false)
		var parts := []
		if count["picosim"] > 0:
			parts.append("%d difference%s from picosim" % [count["picosim"], "" if count["picosim"] == 1 else "s"])
		if count["datasheet"] > 0:
			parts.append("%d difference%s from the datasheet (picosim does not model these)" % [count["datasheet"], "" if count["datasheet"] == 1 else "s"])
		if count["expected"] > 0:
			parts.append("%d fact%s not as the datasheet or SDK predicts" % [count["expected"], "" if count["expected"] == 1 else "s"])
		if count["pending"] > 0:
			parts.append("%d that picosim cannot model yet (pending)" % count["pending"])
		var n: int = int(r.get("n_diffs", diffs.size()))
		t += "[color=%s]%s%s. Click a row to see its example%s.[/color]\n" % [
			Palette.hex("dim"), ", ".join(parts),
			(" (the first %d of %d shown)" % [diffs.size(), n]) if n > diffs.size() else "",
			"; those marked ▶ have a program that [b]Open in picosim[/b] loads in the main window" if openable else ""]
	elif r.get("state", "") == "done":
		t += "[color=%s]No differences recorded: the table lists what was measured (%d facts).[/color]\n" % [Palette.hex("dim"), int(r.get("n_facts", 0))]
	if name == "flashed":
		t += "\n" + _flash_summary() + "\n"
	var lines: Array = camp_log.get(name, [])
	if not lines.is_empty():
		t += "\n[b]Log[/b]\n[code]"
		for l in lines.slice(maxi(0, lines.size() - 80)):
			t += _esc(str(l)) + "\n"
		t += "[/code]"
	var target := "standin" if discover_kind == "standin" else "openocd"
	t += "\n[color=%s]From a terminal: [code]python3 -m picosim.discover %s --target %s[/code] (add [code]--fresh[/code] to start over); [code]python3 -m picosim.discover status%s[/code] lists every campaign.%s[/color]" % [
		Palette.hex("dim"), name, target, " --standin" if target == "standin" else "",
		("  Facts: " + _esc(str(r["facts_path"]))) if str(r.get("facts_path", "")) != "" else ""]
	detail.text = t


func _flash_summary() -> String:
	var col := "green" if flash.get("ready", false) else ("error" if flash.get("pending_restore", false) else "yellow")
	return "[b]The flash backup[/b]   [color=%s]%s[/color]" % [Palette.hex(col), _esc(str(flash.get("message", "Not known yet.")))]


func _show_difference(i: int) -> void:
	var d := _diff(i)
	if d.is_empty():
		return
	_camp_row = i
	var k := str(d.get("kind", ""))
	var t := "[b]%d   %s[/b]   [color=%s]%s[/color]" % [i + 1, _esc(str(d.get("what", ""))),
		Palette.hex({"picosim": "error", "datasheet": "yellow", "expected": "yellow"}.get(k, "dim")),
		{"picosim": "differs from picosim", "datasheet": "differs from the datasheet", "expected": "not what was expected", "pending": "pending: picosim cannot model this yet"}.get(k, k)]
	var bits := []
	for f in ["class", "where"]:
		if str(d.get(f, "")) != "":
			bits.append(str(d[f]))
	if d.get("cases") != null:
		bits.append("%d case%s" % [int(d["cases"]), "" if int(d["cases"]) == 1 else "s"])
	if not bits.is_empty():
		t += "   [color=%s]%s[/color]" % [Palette.hex("dim"), _esc(" · ".join(bits))]
	t += "\n" + _esc(str(d.get("detail", ""))) + "\n"
	if str(d.get("known", "")) != "":
		t += "[color=%s]Known: %s[/color]\n" % [Palette.hex("green"), _esc(str(d["known"]))]
	var ex = d.get("example")
	if ex is Dictionary:
		t += "\n[b]Minimal example[/b] (case %s): the instruction%s [code]%s[/code]%s, with these registers and flags on the chip\n" % [
			_plain(ex.get("id", "?")), "s" if str(ex.get("code", "")).length() > 8 else "", _halfwords(str(ex.get("code", ""))),
			(" (" + _esc(str(ex["name"])) + ")") if str(ex.get("name", "")) != "" else ""]
		var regs: Array = ex.get("regs", [])
		var cells := "[table=8]"
		for j in regs.size():
			cells += "[cell padding=0,1,8,1][color=%s]R%d[/color][/cell][cell padding=0,1,20,1][code]%s[/code][/cell]" % [Palette.hex("muted"), j, _esc(str(regs[j]))]
		var extra := [["NZCV", _plain(ex.get("nzcv", ""))], ["PSP", _plain(ex.get("psp", ""))], ["PRIMASK", _plain(ex.get("primask", ""))]]
		for e in extra:
			cells += "[cell padding=0,1,8,1][color=%s]%s[/color][/cell][cell padding=0,1,20,1][code]%s[/code][/cell]" % [Palette.hex("muted"), e[0], _esc(e[1])]
		var n := regs.size() + extra.size()
		while n % 4 != 0:
			cells += "[cell][/cell][cell][/cell]"
			n += 1
		t += cells + "[/table]\n"
	if d.get("openable", false):
		t += "\n[url=open:%d][bgcolor=%s][color=%s] ▶ Open in picosim [/color][/bgcolor][/url]   [color=%s]loads a small program in the main window that sets these registers and flags, runs the instruction and stops, so you can step through what picosim does[/color]" % [
			i, Palette.hex("sig_bg"), Palette.hex("accent"), Palette.hex("dim")]
	detail.text = t
	_update_controls()


## A JSON number as written (JSON gives Godot every number as a float).
static func _plain(v) -> String:
	if v is float and v == floorf(v):
		return str(int(v))
	return str(v)


## Code bytes in hex ("0048") as the halfwords the CPU reads ("4800").
static func _halfwords(code: String) -> String:
	var out := []
	for i in range(0, code.length() - 3, 4):
		out.append((code.substr(i + 2, 2) + code.substr(i, 2)).to_upper())
	return " ".join(out)


func _diff(i: int) -> Dictionary:
	for d in camp.get("diffs", []):
		if int(d["index"]) == i:
			return d
	return {}


func _openable(i: int) -> bool:
	return i >= 0 and _diff(i).get("openable", false)


func _open_case(i: int) -> void:
	if not _openable(i):
		return
	Backend.send("hw_discover_open_case", {"campaign": str(camp.get("name", "")), "index": i, "kind": _discover_kind()})


func _on_detail_meta(meta: Variant) -> void:
	var m := str(meta)
	if m.begins_with("open:"):
		_open_case(int(m.substr(5)))


func _camp_note(text: String, color: String) -> void:
	camp_note.text = "[color=%s]%s[/color]" % [Palette.hex(color), _esc(text)]


func _camp_note_default(name: String) -> void:
	if name == camp_running:
		_camp_note("Running " + str(camp_progress.get(name, "…")), "accent")
	elif camp_msg.has(name):
		_camp_note(camp_msg[name][0], camp_msg[name][1])
	elif str(status.get("connected", "")) == "":
		_camp_note("Connect to a chip (or the stand-in) to run a campaign.", "muted")
	else:
		_camp_note("", "muted")


func _update_campaign_controls() -> void:
	if btn_camp_run == null:
		return
	var name := shown.substr(DISCOVER.length()) if shown.begins_with(DISCOVER) else ""
	var connected: bool = str(status.get("connected", "")) != ""
	var busy: bool = status.get("busy", false)
	var r: Dictionary = discover.get(name, {})
	var can: bool = name != "" and connected and not busy and r.get("available", true)
	btn_camp_run.disabled = not can
	btn_camp_resume.disabled = not can or r.get("state", "") != "partial"
	btn_camp_stop.disabled = not busy or camp_running == ""
	btn_camp_flash.visible = name == "flashed" or flash.get("pending_restore", false)
	btn_camp_run.tooltip_text = "Run this campaign from the start (the command line's --fresh) on the chip you are connected to." if connected else "Connect to a chip (or the stand-in) first."


func _camp_run(fresh: bool) -> void:
	var name := shown.substr(DISCOVER.length()) if shown.begins_with(DISCOVER) else ""
	if name == "":
		return
	if name == "flashed":
		_flash_fresh = fresh
		_open_flash_dialog()
		return
	var st := str(discover.get(name, {}).get("state", ""))
	if fresh and (st == "partial" or st == "done"):
		_open_fresh_dialog(name, st)
		return
	_camp_send_run(name, fresh, false)


## Run (= --fresh) on a campaign that already has measurements: say what
## starting again sets aside, and offer Resume for a part-run campaign.
func _open_fresh_dialog(name: String, st: String) -> void:
	_fresh_name = name
	var r: Dictionary = discover.get(name, {})
	var title := str(r.get("title", name)).split(":")[0]
	if st == "partial":
		var where := ""
		if r.has("total") and int(r.get("total", 0)) > 0:
			where = " (%s%d of %d batches)" % [(str(r.get("step", "")) + ": ") if str(r.get("step", "")) != "" else "", int(r.get("done", 0)), int(r.get("total", 0))]
		fresh_dialog.dialog_text = ("%s stopped part-way%s.\n\nStart again runs every one of its stages on the chip from the first batch. " +
			"What it has measured so far stays on disk, but its facts will come from the new runs only.\n\n" +
			"Resume carries on where it stopped and keeps those measurements.") % [title, where]
	else:
		fresh_dialog.dialog_text = ("%s is done.\n\nStart again measures all of it on the chip once more, " +
			"and its facts in hwfacts/ are replaced by the new run's. The earlier runs stay on disk.") % title
	btn_fresh_resume.visible = st == "partial"
	fresh_dialog.popup_centered(Vector2i(620, 280))


func _camp_send_run(name: String, fresh: bool, confirmed: bool) -> void:
	if name == "flashed":
		fresh = _flash_fresh
	camp_msg.erase(name)
	_camp_note("Starting…", "accent")
	Backend.send("hw_discover_run", {"campaign": name, "fresh": fresh, "confirmed": confirmed})
	status["busy"] = true
	_update_controls()


## Campaign F's confirmation: every backup and restore step, the backup's
## state, and buttons to back up or restore.  F can't start without a checked
## backup (the backend refuses too).
func _open_flash_dialog() -> void:
	var connected: bool = str(status.get("connected", "")) != ""
	var busy: bool = status.get("busy", false)
	var t := _flash_summary() + "\n\n"
	t += "[b]Which Pico is which[/b]\n"
	t += "• The [b]PROBE[/b] Pico runs the debug probe firmware. Its USB cable goes to this computer and three wires go from it to the other Pico. [color=%s]Never press its BOOTSEL button[/color], and keep it plugged in the whole time.\n" % Palette.hex("error")
	t += "• The [b]TARGET[/b] Pico is the one being tested: its three debug pins (SWCLK, GND, SWDIO) are wired to the probe, and it has its own USB cable to this computer. Only this one is ever put in BOOTSEL mode.\n"
	t += "• Don't press anything in advance: a message pops up at each moment you're needed.\n\n"
	t += "Campaign F is the only campaign that changes the board for good: it writes the target Pico's flash. So the original contents are saved first, twice and by two different routes, and put back at the end, bit for bit. [b]It cannot start until both backups have been made and match.[/b]\n"
	var parts := [["backup", "Before anything is written: the backup"], ["run", "Then the campaign"],
		["restore", "At the end (or, if a session ends early, before anything else): the restore"]]
	for p in parts:
		t += "\n[b]%s[/b]\n" % p[1]
		var steps: Array = flash_steps.get(p[0], [])
		for j in steps.size():
			t += "%d. %s\n" % [j + 1, _esc(str(steps[j]))]
	t += "\n[color=%s]The same from a terminal: [code]python3 -m picosim.discover backup-flash[/code], [code]python3 -m picosim.discover flashed[/code], [code]python3 -m picosim.discover restore-flash[/code].[/color]" % Palette.hex("dim")
	if not connected:
		t += "\n\n[color=%s]Connect to the debug probe first.[/color]" % Palette.hex("yellow")
	flash_text.text = t
	flash_text.custom_minimum_size = Vector2(680, 0)
	flash_dialog.get_ok_button().disabled = not (flash.get("ready", false) and connected and not busy)
	btn_flash_backup.disabled = not connected or busy or flash.get("pending_restore", false)
	btn_flash_restore.visible = flash.get("restorable", false) or flash.get("pending_restore", false)
	btn_flash_restore.disabled = not connected or busy
	flash_dialog.popup_centered(Vector2i(780, 600))


func _on_discover(msg: Dictionary) -> void:
	var name := str(msg.get("campaign", ""))
	var on_screen := shown == DISCOVER + name
	match str(msg.get("event", "")):
		"discover_status":
			discover_kind = str(msg.get("kind", ""))
			flash = msg.get("flash", {})
			if msg.has("steps"):
				flash_steps = msg["steps"]
			for r in msg.get("campaigns", []):
				discover[str(r["name"])] = r
			_refresh_tests()
			if _demo_discover != "" and not _demo_discover.begins_with("row:") and not _demo_standin and not status.get("busy", false):
				var want := _demo_discover.get_slice(":", 0)
				_demo_discover = ("row:" + _demo_discover.get_slice(":", 1)) if _demo_discover.contains(":") else ""
				_select(DISCOVER + want)
			elif shown.begins_with(DISCOVER):
				_show(shown)
		"discover_detail":
			var d: Dictionary = msg.get("detail", {})
			if msg.has("steps"):
				flash_steps = msg["steps"]
			if d.has("flash"):
				flash = d["flash"]
			if shown != DISCOVER + str(d.get("name", "")):
				return
			camp = d
			discover[str(d["name"])] = d
			_mark_campaign(str(d["name"]))
			if _demo_discover.begins_with("row:"):
				_camp_row = int(_demo_discover.substr(4))
				_demo_discover = ""
			_render_campaign(str(d["name"]))
			if _demo_flash and d["name"] == "flashed":
				_demo_flash = false
				_open_flash_dialog()
			if _camp_row >= 0 and _camp_row < _row_items.size():
				(_row_items[_camp_row] as TreeItem).select(0)
				table.scroll_to_item(_row_items[_camp_row])
		"discover_start":
			camp_running = name
			camp_log[name] = []
			camp_progress[name] = "…"
			camp_msg.erase(name)
			status["busy"] = true
			_mark_campaign(name)
			if on_screen:
				_camp_row = -1
				_render_campaign(name)
		"discover_log":
			var lines: Array = camp_log.get(name, [])
			lines.append(str(msg.get("text", "")))
			if lines.size() > MAX_LOG:
				lines = lines.slice(lines.size() - MAX_LOG)
			camp_log[name] = lines
			if on_screen and _camp_row < 0:
				_camp_detail_default(name)
		"discover_progress":
			var r: Dictionary = discover.get(name, {})
			r["done"] = int(msg.get("done", 0))
			r["total"] = int(msg.get("total", 0))
			discover[name] = r
			var step := str(msg.get("step", ""))
			camp_progress[name] = "%s%d of %d batches" % [(step + ": ") if step != "" else "", r["done"], r["total"]]
			_mark_campaign(name)
			if on_screen:
				_camp_note_default(name)
		"discover_ask":
			ask_dialog.dialog_text = str(msg.get("message", ""))
			ask_dialog.popup_centered(Vector2i(640, 340))
		"discover_done":
			camp_running = ""
			ask_dialog.hide()
			if msg.get("row") is Dictionary:
				discover[name] = msg["row"]
			camp_progress.erase(name)
			var m := str(msg.get("message", ""))
			if msg.get("ok", false):
				camp_msg[name] = ["Finished. " + m, "green"]
			elif msg.get("stopped", false):
				camp_msg[name] = ["Stopped. " + m + " Resume carries on from there.", "yellow"]
			else:
				camp_msg[name] = ["Could not finish: " + m, "error"]
			var lines: Array = camp_log.get(name, [])
			lines.append(camp_msg[name][0])
			camp_log[name] = lines
			_mark_campaign(name)
			if on_screen:
				_show(shown)
			_ask_discover_status()
			if msg.get("restore", false):
				_open_flash_dialog()
		"discover_refused":
			if name != "":
				camp_msg[name] = [str(msg.get("message", "")), "error"]
			if shown.begins_with(DISCOVER):
				_camp_note(str(msg.get("message", "")), "error")
			else:
				head.text = "[color=%s]%s[/color]" % [Palette.hex("error"), _esc(str(msg.get("message", "")))]
			if msg.get("restore", false):
				_open_flash_dialog()
		"discover_opened":
			_camp_note("Loaded in the main window: " + str(msg.get("path", "")).get_file(), "green")


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
