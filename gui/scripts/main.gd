extends Control
## Top-level layout:
##   left  — registers (top) and memory (bottom)
##   right — toolbar, optional CPU diagram + cycle inspector, serial console
## While no program is loaded a welcome page (welcome.gd) replaces the panels.
## The Level menu (level.gd) decides which controls and panels are shown, so
## beginners start with only the basics; see _apply_level.

const RegistersPanel := preload("res://scripts/registers_panel.gd")
const MemoryPanel := preload("res://scripts/memory_panel.gd")
const ConsolePanel := preload("res://scripts/console_panel.gd")
const DiagramView := preload("res://scripts/diagram_view.gd")
const CyclePanel := preload("res://scripts/cycle_panel.gd")
const PicoView := preload("res://scripts/pico_view.gd")
const PipelineView := preload("res://scripts/pipeline_view.gd")
const Welcome := preload("res://scripts/welcome.gd")
const SettingsDialog := preload("res://scripts/settings_dialog.gd")
const Spotlight := preload("res://scripts/spotlight.gd")
const MACHINES := ["multicycle", "pico2"]

## Clock-speed presets (cycles per second); 0 = as fast as possible.
const ZOOM_MIN := 0.5
const ZOOM_MAX := 3.0
const ZOOM_STEP := 0.1
const SETTINGS := "user://settings.cfg"
const SPEEDS := [1, 2, 4, 8, 16, 50, 200, 1000, 10000, 100000, 0]
## Optional user guide: if this file is deleted the Guide button just disappears.
const GUIDE := "res://scripts/guide.gd"
## Text in a panel grows when the user drags a splitter to enlarge it.
const TEXT_SCALE_MAX := 3.0
## Theme font-size items that set a control's text size.
const FONT_ITEMS := ["font_size", "normal_font_size", "bold_font_size", "mono_font_size",
	"italics_font_size", "bold_italics_font_size", "title_button_font_size"]

var regs_panel
var mem_panel
var console_panel
var diagram
var cycle_panel
var pipe_view
var pipe_caption: RichTextLabel
var caption_panel: PanelContainer
var cycle_area: Control
var pico_view
var perf_window
var zoom := 1.0
var zoom_label: Button
var diagram_area: Control
var layout_root: VBoxContainer
var right_split: VSplitContainer
var pico_overlay: Control
var pico_home: Control
var panels: Control              # everything under the toolbar except the welcome page
var welcome: Control
var tip_bar: PanelContainer
var level_bar: PanelContainer     # "New at Level N" strip, after raising the level
var spotlight: Control            # outlines new controls for a few seconds
var _shown_level := 1             # the level the window is laid out for
var guide: Node

var btn_open: Button
var btn_reload: Button
var btn_cycle: Button
var btn_insn: Button
var btn_run: Button
var speed_slider: HSlider
var speed_label: Label
var mode_select: OptionButton
var port_select: OptionButton
var btn_ports: Button
var btn_connect: Button
var btn_flash: Button
var btn_diagram: Button
var machine_select: OptionButton
var theme_select: OptionButton
var level_select: OptionButton
var examples_menu: MenuButton
var btn_perf: Button
var btn_settings: Button
var settings_dialog: ConfirmationDialog
var mode_sep: VSeparator
var status_label: Label
var _status_bad := false
var bg: ColorRect
var file_dialog: FileDialog
var error_dialog: AcceptDialog
var _screenshot_path := ""
var _screenshot_frames := 0
var _screenshot_wait := 40       # frames to let the UI settle before --screenshot
var _demo_cycles := 0
var _demo_click_pin := 0
var _demo_component := ""
var _demo_click_gate := ""        # --demo-click-gate ID: click that buffer with a real mouse event
var _demo_machine := ""          # --machine=pico2: start on that machine
var _demo_settings := false      # --settings: open the program's settings form
var _demo_level := 0             # --demo-level-up N: raise the level to N (not saved)
var _text_panels: Array = []     # [{panel, fonts, growth, size, scale}] — see _setup_text_scaling
var _drag_frame := -100          # process frame of the last splitter drag


func _ready() -> void:
	_build_ui()
	_init_zoom()
	_setup_text_scaling()
	Backend.state_changed.connect(_on_state)
	Backend.program_loaded.connect(_on_program)
	Backend.load_failed.connect(_on_load_failed)
	Backend.error_received.connect(_on_error)
	Backend.serial_ports_listed.connect(_on_ports)
	Backend.flash_finished.connect(_on_flash_done)
	Backend.connected.connect(func(): _set_status("Connected to simulator", false))
	Backend.disconnected.connect(func(): _set_status("Simulator backend disconnected", true))
	_set_status("Connecting to simulator…", false)
	Palette.changed.connect(_on_palette)
	Level.changed.connect(_on_level_changed)
	_shown_level = Level.current
	_apply_level()
	_update_controls()
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i] == "--screenshot" and i + 1 < args.size():
			_screenshot_path = args[i + 1]
		if args[i] == "--show-diagram":
			btn_diagram.button_pressed = true
		if args[i] == "--select-component" and i + 1 < args.size():
			_demo_component = args[i + 1]
		if args[i] == "--pico-full":
			pico_view.set_fullscreen(true)
		if args[i] == "--demo-cycles" and i + 1 < args.size():
			_demo_cycles = int(args[i + 1])
		if args[i] == "--demo-click-pin" and i + 1 < args.size():
			_demo_click_pin = int(args[i + 1])
		if args[i] == "--demo-click-gate" and i + 1 < args.size():
			_demo_click_gate = args[i + 1]
		if args[i].begins_with("--machine="):
			_demo_machine = args[i].substr(10)
		if args[i] == "--settings":
			_demo_settings = true
		if args[i] == "--shot-frames" and i + 1 < args.size():
			_screenshot_wait = int(args[i + 1])
		if args[i] == "--demo-level-up" and i + 1 < args.size():
			_demo_level = int(args[i + 1])


func _process(_delta: float) -> void:
	# --screenshot PATH: grab the window once the UI has settled, then quit.
	if _demo_machine != "" and Backend.state.get("loaded", false):
		machine_select.select(maxi(0, MACHINES.find(_demo_machine)))
		_on_machine(machine_select.selected)
		_demo_machine = ""
	if _demo_level > 0 and Backend.state.get("loaded", false):
		Level.current = _demo_level   # as if picked in the menu, but not saved
		Level.changed.emit()
		_demo_level = 0
	if _demo_cycles > 0 and Backend.state.get("loaded", false):
		for i in _demo_cycles:
			Backend.send("step_cycle")
		_demo_cycles = 0
	if _demo_click_pin > 0 and Backend.state.get("loaded", false) and _screenshot_frames == 20:
		pico_view.demo_click(_demo_click_pin)
		_demo_click_pin = 0
	if _demo_click_gate != "" and Backend.state.get("loaded", false) and _screenshot_frames == 12:
		var c: Vector2 = diagram._gate_geom(_demo_click_gate)["c"]
		var at: Vector2 = diagram.get_screen_transform() * diagram._p(c.x, c.y)
		for pressed in [true, false]:
			var ev := InputEventMouseButton.new()
			ev.button_index = MOUSE_BUTTON_LEFT
			ev.pressed = pressed
			ev.position = at
			ev.global_position = at
			Input.parse_input_event(ev)
		_demo_click_gate = ""
	if _demo_component != "" and Backend.state.get("loaded", false) and _screenshot_frames == 10:
		diagram.select_component(_demo_component)
		_demo_component = ""
	# (with no program the welcome page is shown, once the backend has answered)
	if _screenshot_path != "" and Backend.state.has("loaded"):
		_screenshot_frames += 1
		if _screenshot_frames == _screenshot_wait:
			get_viewport().get_texture().get_image().save_png(_screenshot_path)
			get_tree().quit()


# ── layout ──────────────────────────────────────────────────────────────────

func _build_ui() -> void:
	bg = ColorRect.new()
	bg.color = Palette.c("bg")
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(bg)

	var root := VBoxContainer.new()
	layout_root = root
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.offset_left = 6
	root.offset_top = 6
	root.offset_right = -6
	root.offset_bottom = -6
	add_child(root)

	root.add_child(_build_toolbar())
	root.add_child(_build_tip_bar())
	root.add_child(_build_level_bar())

	var hsplit := HSplitContainer.new()
	hsplit.size_flags_vertical = Control.SIZE_EXPAND_FILL
	hsplit.visible = false   # until a program is loaded
	root.add_child(hsplit)
	panels = hsplit
	welcome = Welcome.new()
	welcome.open_file_requested.connect(_open_dialog)
	welcome.guide_requested.connect(func(): if guide: guide.open("start"))
	root.add_child(welcome)

	# left column
	var left := VSplitContainer.new()
	left.custom_minimum_size.x = 500
	left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left.size_flags_stretch_ratio = 0.62
	hsplit.add_child(left)
	regs_panel = RegistersPanel.new()
	left.add_child(regs_panel)
	var lower := VSplitContainer.new()
	lower.size_flags_vertical = Control.SIZE_EXPAND_FILL
	left.add_child(lower)
	mem_panel = MemoryPanel.new()
	mem_panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	mem_panel.custom_minimum_size.y = 160
	lower.add_child(mem_panel)
	pico_view = PicoView.new()
	pico_view.custom_minimum_size.y = 250
	pico_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	pico_view.size_flags_stretch_ratio = 0.9
	lower.add_child(pico_view)
	pico_home = lower
	pico_view.fullscreen_toggled.connect(_on_pico_fullscreen)

	# right column: diagram on top; cycle inspector + console underneath
	right_split = VSplitContainer.new()
	right_split.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hsplit.add_child(right_split)

	# the multi-cycle datapath and the 2-stage pipeline share one area
	diagram_area = MarginContainer.new()   # stacks its children, full size
	diagram_area.size_flags_vertical = Control.SIZE_EXPAND_FILL
	diagram_area.size_flags_stretch_ratio = 2.2
	diagram_area.custom_minimum_size = Vector2(500, 300)
	right_split.add_child(diagram_area)
	diagram = DiagramView.new()
	diagram.set_speed(SPEEDS[int(speed_slider.value)])
	diagram_area.add_child(diagram)
	pipe_view = PipelineView.new()
	pipe_view.set_speed(SPEEDS[int(speed_slider.value)])
	pipe_view.visible = false
	diagram_area.add_child(pipe_view)

	var bottom := HSplitContainer.new()
	bottom.size_flags_vertical = Control.SIZE_EXPAND_FILL
	right_split.add_child(bottom)
	# the cycle inspector, or in the pipeline the explanation of the cycle shown
	cycle_area = MarginContainer.new()
	cycle_area.custom_minimum_size.x = 600
	cycle_area.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	cycle_area.size_flags_stretch_ratio = 1.6
	bottom.add_child(cycle_area)
	cycle_panel = CyclePanel.new()
	cycle_area.add_child(cycle_panel)
	caption_panel = PanelContainer.new()
	caption_panel.visible = false
	cycle_area.add_child(caption_panel)
	pipe_caption = RichTextLabel.new()
	pipe_caption.bbcode_enabled = true
	pipe_caption.scroll_active = true
	pipe_caption.selection_enabled = true
	pipe_caption.add_theme_font_override("mono_font", Backend.mono_font())
	caption_panel.add_child(pipe_caption)
	cycle_panel.cycle_selected.connect(diagram.show_cycle)
	console_panel = ConsolePanel.new()
	console_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	console_panel.custom_minimum_size.x = 280
	bottom.add_child(console_panel)
	diagram_area.visible = false
	cycle_area.visible = false

	# Full-window host for the Pico board; pico_view is moved in here on demand.
	pico_overlay = Control.new()
	pico_overlay.set_anchors_preset(Control.PRESET_FULL_RECT)
	pico_overlay.visible = false
	add_child(pico_overlay)

	# on top of everything: outlines for controls that are new at this level
	spotlight = Spotlight.new()
	add_child(spotlight)

	file_dialog = FileDialog.new()
	file_dialog.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	file_dialog.access = FileDialog.ACCESS_FILESYSTEM
	file_dialog.filters = PackedStringArray(["*.s ; ARM assembly", "*.elf ; ELF binary"])
	file_dialog.use_native_dialog = true
	file_dialog.title = "Open assembly program"
	file_dialog.file_selected.connect(func(p): Backend.send("load", {"path": p}))
	var ex_dir := ProjectSettings.globalize_path("res://").path_join("../examples").simplify_path()
	if DirAccess.dir_exists_absolute(ex_dir):
		file_dialog.current_dir = ex_dir
	add_child(file_dialog)

	error_dialog = AcceptDialog.new()
	error_dialog.title = "Could not load program"
	add_child(error_dialog)


func _button(text: String, tip: String, cb: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.tooltip_text = tip
	b.pressed.connect(cb)
	b.focus_mode = Control.FOCUS_NONE
	return b


func _sep() -> VSeparator:
	return VSeparator.new()


func _build_toolbar() -> Control:
	var bar := HBoxContainer.new()
	bar.add_theme_constant_override("separation", 6)
	btn_open = _button("Open…", "Open an assembly (.s) file (Ctrl+O)", _open_dialog)
	examples_menu = MenuButton.new()
	examples_menu.text = "Examples"
	examples_menu.tooltip_text = "Open one of the example programs"
	examples_menu.focus_mode = Control.FOCUS_NONE
	examples_menu.flat = false
	examples_menu.about_to_popup.connect(_fill_examples)
	examples_menu.get_popup().id_pressed.connect(_on_example)
	btn_reload = _button("Reload", "Start the program over: read the file again and reset the CPU (Ctrl+R)", func(): Backend.send("reload"))
	settings_dialog = SettingsDialog.new()
	add_child(settings_dialog)
	btn_settings = _button("⚙ Settings", "This program's settings, such as which pins your LED and button are on", func(): settings_dialog.open())
	btn_settings.visible = false
	btn_cycle = _button("Step cycle", "Advance one clock cycle (F11)", _step_cycle)
	btn_insn = _button("Step instruction", "Run one instruction, then stop (F10)", _step_insn)
	btn_run = _button("Run", "Run the program by itself, or pause it (F5)", _toggle_run)
	btn_run.custom_minimum_size.x = 70
	for b in [btn_open, examples_menu, btn_reload, btn_settings, _sep(), btn_insn, btn_cycle, btn_run]:
		bar.add_child(b)

	var sl := Label.new()
	sl.text = "Speed"
	bar.add_child(sl)
	speed_slider = HSlider.new()
	speed_slider.min_value = 0
	speed_slider.max_value = SPEEDS.size() - 1
	speed_slider.step = 1
	speed_slider.value = 3
	speed_slider.custom_minimum_size.x = 140
	speed_slider.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	speed_slider.focus_mode = Control.FOCUS_NONE
	speed_slider.value_changed.connect(_on_speed)
	speed_slider.tooltip_text = "How fast Run goes, in clock cycles per second (Max = as fast as possible)"
	sl.tooltip_text = speed_slider.tooltip_text
	bar.add_child(speed_slider)
	speed_label = Label.new()
	speed_label.custom_minimum_size.x = 90
	bar.add_child(speed_label)
	_on_speed(speed_slider.value)

	mode_sep = _sep()
	bar.add_child(mode_sep)
	mode_select = OptionButton.new()
	mode_select.add_item("Simulator", 0)
	mode_select.add_item("Pico (USB serial)", 1)
	mode_select.focus_mode = Control.FOCUS_NONE
	mode_select.item_selected.connect(_on_mode)
	mode_select.tooltip_text = "Simulator — run your program here on screen\nPico (USB serial) — talk to a real Raspberry Pi Pico plugged in by USB"
	bar.add_child(mode_select)
	port_select = OptionButton.new()
	port_select.custom_minimum_size.x = 180
	port_select.focus_mode = Control.FOCUS_NONE
	port_select.tooltip_text = "The USB serial port your Pico is on (marked (Pico))"
	bar.add_child(port_select)
	btn_ports = _button("⟳", "Refresh serial ports", func(): Backend.send("serial_list"))
	btn_connect = _button("Connect", "Open the selected serial port", _on_connect)
	btn_flash = _button("Flash", "Build a .uf2 and upload it to the Pico", func(): Backend.send("flash"))
	for b in [btn_ports, btn_connect, btn_flash]:
		bar.add_child(b)

	# the status label takes the toolbar's spare width and grows to fit its
	# text (see _set_status), so large insn/cycle counts are never cut off
	status_label = Label.new()
	status_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	status_label.custom_minimum_size.x = 260
	status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	bar.add_child(status_label)
	var zoom_out := _button("−", "Zoom out (Ctrl/Cmd −)", func(): _set_zoom(zoom - ZOOM_STEP))
	zoom_label = _button("100%", "Fit to window (Ctrl/Cmd 0)", func(): _set_zoom(_default_zoom()))
	zoom_label.custom_minimum_size.x = 52
	var zoom_in := _button("+", "Zoom in (Ctrl/Cmd +)", func(): _set_zoom(zoom + ZOOM_STEP))
	for b in [zoom_out, zoom_label, zoom_in]:
		bar.add_child(b)
	btn_diagram = Button.new()
	btn_diagram.text = "Show CPU Diagram"
	btn_diagram.toggle_mode = true
	btn_diagram.focus_mode = Control.FOCUS_NONE
	btn_diagram.tooltip_text = "Show the datapath and step through each cycle"
	btn_diagram.toggled.connect(_on_diagram_toggled)
	bar.add_child(btn_diagram)
	machine_select = OptionButton.new()
	machine_select.add_item("Multi-cycle")
	machine_select.add_item("2-stage pipeline")
	machine_select.focus_mode = Control.FOCUS_NONE
	machine_select.tooltip_text = "What the diagram shows and Step cycle steps:\nMulti-cycle — the teaching datapath, one FSM state per cycle\n2-stage pipeline — the real Pico's Cortex-M0+, two instructions in flight"
	machine_select.item_selected.connect(_on_machine)
	bar.add_child(machine_select)
	theme_select = OptionButton.new()
	for name in Palette.ORDER:
		theme_select.add_item(Palette.TITLES[name])
	theme_select.select(Palette.ORDER.find(Palette.preference))
	theme_select.focus_mode = Control.FOCUS_NONE
	theme_select.tooltip_text = "Colour scheme (System follows your OS light/dark setting)"
	theme_select.item_selected.connect(func(i): Palette.set_scheme(Palette.ORDER[i]))
	bar.add_child(theme_select)
	var perf: Node = load("res://scripts/perf_window.gd").new()
	add_child(perf)
	btn_perf = _button("Performance", "Cycles, CPI, the RP2040 pipeline and design comparisons (F2)", func(): perf.open())
	bar.add_child(btn_perf)
	perf_window = perf
	level_select = OptionButton.new()
	for n in range(1, Level.MAX + 1):
		level_select.add_item("Level " + Level.title(n))
		level_select.set_item_tooltip(n - 1, Level.ABOUT[n - 1])
	level_select.focus_mode = Control.FOCUS_NONE
	level_select.tooltip_text = "How much of picosim to show. Start at 1 and move up as your class covers more:\n" + \
		"\n".join(range(1, Level.MAX + 1).map(func(n): return Level.title(n) + " — " + Level.ABOUT[n - 1]))
	level_select.item_selected.connect(func(i): Level.set_level(i + 1))
	bar.add_child(level_select)
	if ResourceLoader.exists(GUIDE):
		guide = load(GUIDE).new()
		add_child(guide)
		bar.add_child(_button("Guide", "How to use picosim (F1)", func(): guide.open()))
	return bar


## A slim "how to start" strip shown above the panels after the first program
## a student ever loads; closing it is remembered ([ui] tips_seen).
func _build_tip_bar() -> Control:
	tip_bar = PanelContainer.new()
	tip_bar.visible = false
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 18)
	tip_bar.add_child(h)
	var l := RichTextLabel.new()
	l.bbcode_enabled = true
	l.fit_content = true
	l.autowrap_mode = TextServer.AUTOWRAP_OFF
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	l.scroll_active = false
	l.name = "Text"
	h.add_child(l)
	var ok := _button("Got it ✕", "Hide this tip", _dismiss_tips)
	h.add_child(ok)
	_style_tip_bar()
	return tip_bar


func _style_tip_bar() -> void:
	tip_bar.add_theme_stylebox_override("panel", Palette.panel_box("box_active", 6, 8))
	var a := Palette.hex("changed")
	tip_bar.get_child(0).get_node("Text").text = (
		"[b]How to start:[/b]   ① [b]Step instruction[/b] (F10) runs one line of your program" +
		"     ② values that just changed turn [color=%s]yellow[/color]" +
		"     ③ [b]Run[/b] (F5) goes by itself — [b]Reload[/b] starts over") % a


func _dismiss_tips() -> void:
	tip_bar.visible = false
	var cfg := ConfigFile.new()
	cfg.load(SETTINGS)
	cfg.set_value("ui", "tips_seen", true)
	cfg.save(SETTINGS)


## "New at Level N" strip under the toolbar: after the student raises the
## level it says what appeared, with a button into the guide's lesson.
func _build_level_bar() -> Control:
	level_bar = PanelContainer.new()
	level_bar.visible = false
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 12)
	level_bar.add_child(h)
	var l := RichTextLabel.new()
	l.name = "Text"
	l.bbcode_enabled = true
	l.fit_content = true
	l.scroll_active = false
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(l)
	var learn := _button("Learn about it", "Open the guide's lesson on what is new", func():
		if guide: guide.open(str(level_bar.get_meta("topic", "start"))))
	learn.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(learn)
	var ok := _button("Got it ✕", "Hide this", func(): level_bar.visible = false)
	ok.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(ok)
	_style_level_bar()
	return level_bar


func _style_level_bar() -> void:
	var sb := Palette.panel_box("box_active", 6, 8)
	sb.border_color = Palette.c("changed")
	sb.set_border_width_all(2)
	level_bar.add_theme_stylebox_override("panel", sb)


## What each level adds, for the strip: [title, what is new, guide topic].
const LEVEL_NEWS := {
	2: ["Level 2 · Memory & I/O", "the [b]Memory[/b] tab (the bytes in memory and the stack), the [b]Raspberry Pi Pico[/b] board under it (the chip's pins) and the [b]Simulator[/b] menu (run your program on a real Pico).", "memmap"],
	3: ["Level 3 · Inside the CPU", "the [b]CPU diagram[/b] on the right with the cycle inspector under it, [b]Step cycle[/b] (F11) to run one clock cycle at a time, and the datapath registers in the Registers panel.", "cpuintro"],
	4: ["Level 4 · Pipeline & performance", "the diagram now shows the Pico's own [b]2-stage pipeline[/b] (the menu next to the diagram button switches back to Multi-cycle), and the [b]Performance[/b] window (F2) measures your program.", "pipeintro"],
}


func _on_level_changed() -> void:
	var old := _shown_level
	_shown_level = Level.current
	_apply_level()
	if Level.current > old:
		_level_up(old, Level.current)
	else:
		level_bar.visible = false


## Raising the level gives a fresh view: what the new level adds is switched
## on and shown (the Memory tab, the CPU diagram, the pipeline), the strip
## says what is new, and the new controls are outlined for a few seconds.
func _level_up(old: int, now: int) -> void:
	var fresh := []
	if old < 2 and now >= 2:
		fresh += [mode_select, mem_panel, pico_view]
	if old < 3 and now >= 3:
		btn_diagram.button_pressed = true          # the diagram and the cycle inspector
		fresh += [btn_cycle, btn_diagram, diagram_area, cycle_area, regs_panel.dp_row]
	if old < 4 and now >= 4:
		machine_select.select(1)                    # the 2-stage pipeline
		_on_machine(1)
		fresh += [machine_select, btn_perf]
		if old >= 3:
			fresh.append(diagram_area)
	if now == 2:
		mem_panel.tabs.current_tab = 1              # Level 2's new view: the Memory tab
	var t := ""
	for n in range(old + 1, now + 1):
		var e: Array = LEVEL_NEWS[n]
		t += ("\n" if t != "" else "") + "[b]%s[/b]   [color=%s]New:[/color] %s" % [e[0], Palette.hex("changed"), e[1]]
	level_bar.get_child(0).get_node("Text").text = t
	level_bar.set_meta("topic", LEVEL_NEWS[now][2])
	level_bar.visible = true
	spotlight.show_new(fresh)


func _open_dialog() -> void:
	file_dialog.popup_centered_ratio(0.6)


func _fill_examples() -> void:
	var pop := examples_menu.get_popup()
	pop.clear()
	var list: Array = Welcome.examples()
	for i in list.size():
		var e: Dictionary = list[i]
		var tag := "" if Level.at_least(int(e["level"])) else "   (Level %d)" % int(e["level"])
		pop.add_item(str(e["title"]) + tag, i)
		pop.set_item_tooltip(i, str(e["about"]))
		pop.set_item_metadata(i, e["path"])
	if list.is_empty():
		pop.add_item("no examples folder found")
		pop.set_item_disabled(0, true)


func _on_example(id: int) -> void:
	var path = examples_menu.get_popup().get_item_metadata(examples_menu.get_popup().get_item_index(id))
	if path != null:
		Backend.send("load", {"path": path})


## Show only what the current level needs. Lowering the level also undoes what
## the hidden controls had switched on (the diagram, the pipeline, Pico mode).
func _apply_level() -> void:
	var lv: int = Level.current
	level_select.select(lv - 1)
	if lv < 4 and machine_select.selected == 1:
		machine_select.select(0)
		_on_machine(0)
	if lv < 3 and btn_diagram.button_pressed:
		btn_diagram.button_pressed = false
	if lv < 2 and mode_select.selected == 1:
		mode_select.select(0)
		_on_mode(0)
	if lv < 2 and pico_overlay.visible:
		pico_view.set_fullscreen(false)
	btn_cycle.visible = lv >= 3
	btn_diagram.visible = lv >= 3
	machine_select.visible = lv >= 4
	btn_perf.visible = lv >= 4
	mode_select.visible = lv >= 2
	mode_sep.visible = lv >= 2
	pico_view.visible = lv >= 2
	_update_controls()
	if Backend.state.get("loaded", false):
		_on_state(Backend.state)   # the status text's wording depends on the level


# ── text scaling ────────────────────────────────────────────────────────────
# Dragging a splitter to make a panel bigger makes its text bigger with the
# panel's linear size (the square root of its area, so doubling only the
# height gives ~1.4× text and lines do not get cramped); dragging it smaller
# shrinks it back, never below normal.
# Only splitter drags count: resizing the window, zooming or toggling the
# diagram leave text sizes alone.  A panel may cap its scale with
# max_text_scale() and react to it in _on_text_scale(s).

func _setup_text_scaling() -> void:
	for sp in find_children("*", "SplitContainer", true, false):
		sp.dragged.connect(func(_offset): _drag_frame = Engine.get_process_frames())
	for panel in [mem_panel, cycle_panel, console_panel, diagram]:
		var fonts := []
		for c in [panel] + panel.find_children("*", "Control", true, false):
			for item in FONT_ITEMS:
				if c.has_theme_font_size(item) or c.has_theme_font_size_override(item):
					fonts.append([c, item, c.get_theme_font_size(item)])
		var e := {"panel": panel, "fonts": fonts, "growth": 1.0, "size": panel.size, "scale": 1.0}
		_text_panels.append(e)
		panel.resized.connect(_on_text_panel_resized.bind(e))


func _on_text_panel_resized(e: Dictionary) -> void:
	var panel: Control = e["panel"]
	var old: Vector2 = e["size"]
	e["size"] = panel.size
	var manual := Engine.get_process_frames() - _drag_frame <= 2
	if manual and old.x > 0 and old.y > 0 and panel.size.x > 0 and panel.size.y > 0:
		e["growth"] *= (panel.size.x / old.x) * (panel.size.y / old.y)
	var s := clampf(sqrt(e["growth"]), 1.0, TEXT_SCALE_MAX)
	if panel.has_method("max_text_scale"):
		s = clampf(minf(s, panel.max_text_scale()), 1.0, TEXT_SCALE_MAX)
	s = maxf(1.0, floorf(s / 0.05 + 0.001) * 0.05)   # whole 5% steps, never past the cap
	if is_equal_approx(s, e["scale"]):
		return
	e["scale"] = s
	for f in e["fonts"]:
		f[0].add_theme_font_size_override(f[1], roundi(f[2] * s))
	if panel.has_method("_on_text_scale"):
		panel._on_text_scale(s)


# ── zoom ────────────────────────────────────────────────────────────────────
# The whole UI is scaled with the window's content scale factor.  Until the
# user picks a zoom, it is the largest that fits the whole layout in the
# window (so it follows the screen's resolution and Retina/HiDPI scaling).
# A zoom the user picks is remembered; the zoom % button fits it again.

## The largest zoom at which the whole layout fits a window of `win_size`.
func _default_zoom(win_size := Vector2i.ZERO) -> float:
	if win_size == Vector2i.ZERO:
		win_size = get_window().size
	# Measure with the Pico-mode toolbar controls shown too, so switching
	# modes later does not overflow the toolbar.
	# Likewise the controls of higher levels and the panels behind the welcome
	# page, so raising the level or loading a program never overflows either.
	var extra: Array = [port_select, btn_ports, btn_connect, btn_flash, btn_cycle, btn_diagram,
		machine_select, btn_perf, mode_select, pico_view, panels].filter(func(c): return not c.visible)
	for c in extra:
		c.visible = true
	var wv := welcome.visible
	welcome.visible = false
	var need := layout_root.get_combined_minimum_size() + Vector2(12, 12)  # + root margins
	for c in extra:
		c.visible = false
	welcome.visible = wv
	var fit := minf(win_size.x / need.x, win_size.y / need.y) * 0.98
	return clampf(floorf(fit / 0.05) * 0.05, ZOOM_MIN, ZOOM_MAX)


func _init_zoom() -> void:
	var cfg := ConfigFile.new()
	var saved = null
	if cfg.load(SETTINGS) == OK:
		saved = cfg.get_value("ui", "zoom", null)
	# The window opens filling most of the screen (Godot does not remember
	# window sizes between runs, so this happens on every launch).
	var win := get_window()
	var usable := DisplayServer.screen_get_usable_rect(win.current_screen)
	var target := Vector2i(usable.size * 0.9)
	win.size = target
	win.position = usable.position + (usable.size - target) / 2
	var z: float = float(saved) if saved != null else _default_zoom(target)
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--zoom="):
			z = float(arg.substr(7))
	_set_zoom(z, false)


func _set_zoom(z: float, save := true) -> void:
	zoom = clampf(snappedf(z, 0.05), ZOOM_MIN, ZOOM_MAX)
	get_window().content_scale_factor = zoom
	zoom_label.text = "%d%%" % roundi(zoom * 100)
	if save:
		var cfg := ConfigFile.new()
		cfg.load(SETTINGS)
		cfg.set_value("ui", "zoom", zoom)
		cfg.save(SETTINGS)


func _input(event: InputEvent) -> void:
	# Ctrl/Cmd + mouse wheel zooms from anywhere in the window.
	var mb := event as InputEventMouseButton
	if mb and mb.pressed and mb.is_command_or_control_pressed():
		if mb.button_index == MOUSE_BUTTON_WHEEL_UP:
			_set_zoom(zoom + ZOOM_STEP)
			get_viewport().set_input_as_handled()
		elif mb.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_set_zoom(zoom - ZOOM_STEP)
			get_viewport().set_input_as_handled()


# ── actions ─────────────────────────────────────────────────────────────────

func _step_cycle() -> void:
	Backend.send("step_cycle")


func _step_insn() -> void:
	Backend.send("step_insn")


func _toggle_run() -> void:
	if Backend.state.get("running", false):
		Backend.send("pause")
	else:
		Backend.send("run", {"cps": SPEEDS[int(speed_slider.value)]})


func _on_speed(v: float) -> void:
	var cps: int = SPEEDS[int(v)]
	speed_label.text = "Max" if cps == 0 else ("%d cyc/s" % cps)
	if diagram:
		diagram.set_speed(cps)
	if pipe_view:
		pipe_view.set_speed(cps)
	if Backend.state.get("running", false):
		Backend.send("set_speed", {"cps": cps})


func _on_mode(idx: int) -> void:
	Backend.send("set_mode", {"mode": "pico" if idx == 1 else "sim"})
	if idx == 1:
		Backend.send("serial_list")


func _on_connect() -> void:
	if Backend.state.get("serial") != null:
		Backend.send("serial_close")
		return
	var port := ""
	if port_select.selected >= 0:
		port = port_select.get_item_metadata(port_select.selected)
	Backend.send("serial_open", {"port": port})


func _on_machine(idx: int) -> void:
	Backend.send("set_machine", {"machine": MACHINES[idx]})
	_apply_machine()
	if idx == 1 and not btn_diagram.button_pressed:
		btn_diagram.button_pressed = true


func _pipeline() -> bool:
	return machine_select.selected == 1


func _apply_machine() -> void:
	var pipe := _pipeline()
	diagram.visible = not pipe
	pipe_view.visible = pipe
	cycle_panel.visible = not pipe
	caption_panel.visible = pipe
	btn_cycle.tooltip_text = "Advance one pipeline cycle (F11)" if pipe else "Advance one clock cycle (F11)"
	btn_insn.tooltip_text = "Go to the next instruction's first Execute cycle (F10)" if pipe else "Run one instruction, then stop (F10)"
	caption_panel.add_theme_stylebox_override("panel", Palette.panel_box("panel", 6, 10))


func _on_diagram_toggled(on: bool) -> void:
	diagram_area.visible = on
	cycle_area.visible = on
	btn_diagram.text = "Hide CPU Diagram" if on else "Show CPU Diagram"
	console_panel.set_compact(on)


func _on_pico_fullscreen(on: bool) -> void:
	pico_view.reparent(pico_overlay if on else pico_home, false)
	if on:
		pico_view.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	pico_overlay.visible = on


func _unhandled_key_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k == null or not k.pressed:
		return
	# holding F10/F11 repeats the step; other keys ignore auto-repeat
	if k.echo and k.keycode != KEY_F11 and k.keycode != KEY_F10:
		return
	var sim: bool = mode_select.selected == 0
	match k.keycode:
		KEY_ESCAPE:
			if not pico_overlay.visible:
				return
			pico_view.set_fullscreen(false)
		KEY_F11:
			if sim and Level.at_least(3): _step_cycle()
		KEY_F10:
			if sim: _step_insn()
		KEY_F5:
			if sim: _toggle_run()
		KEY_F2:
			if Level.at_least(4): perf_window.open()
		KEY_O:
			if k.is_command_or_control_pressed(): _open_dialog()
		KEY_R:
			if k.is_command_or_control_pressed(): Backend.send("reload")
		KEY_EQUAL, KEY_PLUS, KEY_KP_ADD:
			if k.is_command_or_control_pressed(): _set_zoom(zoom + ZOOM_STEP)
		KEY_MINUS, KEY_KP_SUBTRACT:
			if k.is_command_or_control_pressed(): _set_zoom(zoom - ZOOM_STEP)
		KEY_0, KEY_KP_0:
			if k.is_command_or_control_pressed(): _set_zoom(_default_zoom())
		_:
			return
	get_viewport().set_input_as_handled()


# ── backend events ──────────────────────────────────────────────────────────

func _on_state(st: Dictionary) -> void:
	regs_panel.update_state(st)
	mem_panel.update_state(st)
	pico_view.update_state(st)
	console_panel.update_state(st)
	cycle_panel.update_state(st)
	diagram.update_state(st)
	var m := MACHINES.find(str(st.get("machine", "multicycle")))
	if m >= 0 and m != machine_select.selected:
		machine_select.select(m)
		_apply_machine()
	pipe_view.update_state(st)
	var p = st.get("pipe")
	if p is Dictionary:
		var scroll := pipe_caption.get_v_scroll_bar().value
		pipe_caption.text = PipelineView.caption(p)
		pipe_caption.get_v_scroll_bar().value = scroll
	_update_controls()
	var loaded: bool = st.get("loaded", false)
	if loaded == welcome.visible:
		welcome.visible = not loaded
		panels.visible = loaded
		if loaded:
			var cfg := ConfigFile.new()
			cfg.load(SETTINGS)
			tip_bar.visible = not cfg.get_value("ui", "tips_seen", false)
	if loaded:
		var s := "insn %d · cycle %d" % [int(st["steps"]), int(st["cycles"])]
		if not Level.at_least(3):
			s = "instruction %d" % int(st["steps"])   # clock cycles come in at level 3
		if p is Dictionary:
			s = "insn %d · pipeline cycle %d" % [int(p["insns"]), int(p["cycle"])]
		var fault: bool = st.get("fault") != null
		if fault:
			s += "   — stopped by a CPU fault (see the console)"
		elif st.get("halted", false):
			s += "   — halted"
		elif st.get("waiting_input", false):
			s += "   — waiting for input"
		_set_status(s, fault)


func _on_program(prog: Dictionary) -> void:
	mem_panel.set_program(prog)
	btn_settings.visible = not prog.get("settings", []).is_empty()
	btn_settings.text = "⚙ Settings •" if settings_dialog.changed() else "⚙ Settings"
	btn_settings.tooltip_text = "This program's settings, such as which pins your LED and button are on" + \
		("\n• You have changed some from the program's defaults." if settings_dialog.changed() else "")
	if _demo_settings and btn_settings.visible:
		settings_dialog.open.call_deferred()
		_demo_settings = false
	get_window().title = "picosim — " + str(prog["path"]).get_file()


func _on_load_failed(path: String, message: String) -> void:
	error_dialog.dialog_text = path.get_file() + "\n\n" + message
	error_dialog.popup_centered()
	_set_status("Load failed: " + path.get_file(), true)


func _on_error(message: String) -> void:
	_set_status(message, true)
	console_panel.append_system(message + "\n")


func _on_ports(ports: Array) -> void:
	port_select.clear()
	for p in ports:
		var label: String = p["device"].get_file()
		if p["pico"]:
			label += "  (Pico)"
		port_select.add_item(label)
		port_select.set_item_metadata(port_select.item_count - 1, p["device"])
	if ports.is_empty():
		port_select.add_item("no serial ports")
		port_select.set_item_metadata(0, "")


func _on_flash_done(ok: bool) -> void:
	_set_status("Flash succeeded" if ok else "Flash failed — see console", not ok)
	if ok:
		mode_select.select(1)
		Backend.send("serial_list")


func _on_palette() -> void:
	bg.color = Palette.c("bg")
	_style_tip_bar()
	_style_level_bar()
	caption_panel.add_theme_stylebox_override("panel", Palette.panel_box("panel", 6, 10))
	if Backend.state.get("pipe") is Dictionary:
		pipe_caption.text = PipelineView.caption(Backend.state["pipe"])
	_set_status(status_label.text, _status_bad)


func _set_status(text: String, bad: bool) -> void:
	_status_bad = bad
	status_label.text = text
	status_label.tooltip_text = text
	var font := status_label.get_theme_font("font")
	var fsize := status_label.get_theme_font_size("font_size")
	var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize).x
	status_label.custom_minimum_size.x = maxf(260.0, ceilf(w) + 4.0)
	status_label.add_theme_color_override("font_color", Palette.c("error") if bad else Palette.c("muted"))


func _update_controls() -> void:
	var st := Backend.state
	var loaded: bool = st.get("loaded", false)
	var sim: bool = mode_select.selected == 0
	var halted: bool = st.get("halted", false)
	var p = st.get("pipe")
	if p is Dictionary:
		halted = not bool(p.get("more", true))   # the pipeline can still drain
	btn_reload.disabled = not loaded
	btn_cycle.disabled = not (loaded and sim) or halted
	btn_insn.disabled = not (loaded and sim) or halted
	btn_run.disabled = not (loaded and sim) or halted
	btn_run.text = "Pause" if st.get("running", false) else "Run"
	var pico_ctl := not sim and Level.at_least(2)
	port_select.visible = pico_ctl
	btn_ports.visible = pico_ctl
	btn_connect.visible = pico_ctl
	btn_flash.visible = pico_ctl
	btn_connect.text = "Disconnect" if st.get("serial") != null else "Connect"
	btn_flash.disabled = not loaded or not str(st.get("path", "")).ends_with(".s")
