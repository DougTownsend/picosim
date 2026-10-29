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
const Tour := preload("res://scripts/tour.gd")
const ChallengeBar := preload("res://scripts/challenge_bar.gd")
const HelpButton := preload("res://scripts/help_button.gd")
const ExplainPanel := preload("res://scripts/explain_panel.gd")
const WalkPanel := preload("res://scripts/walk_panel.gd")
const MACHINES := ["multicycle", "m0plus"]

## Clock-speed presets (cycles per second); 0 = as fast as possible.
const ZOOM_MIN := 0.5
const ZOOM_MAX := 3.0
const ZOOM_STEP := 0.1
const SETTINGS := "user://settings.cfg"
const TOUR_ID := 100              # View menu item: Show the tour again
const HEAT_ID := 101              # View menu item: Heat overlay on the CPU diagram
const KEYS_ID := 102              # View menu item: the guide's Keyboard shortcuts
const LAYOUT_ID := 103            # View menu item: the guide's Layout and zoom
const ABOUT_EXAMPLES_ID := 1000   # Examples menu item: the guide's page on the examples
const RESTORE_EXAMPLE_ID := 1001  # Examples menu item: put the original back over the student's copy
const COPIES_FOLDER_ID := 1002    # Examples menu item: show the folder of the student's copies
const HeatOverlay := preload("res://scripts/heat_overlay.gd")
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
var explain_panel                # "Explain this step", above the right column
var explain_split: VSplitContainer
var pipe_view
var pipe_caption: RichTextLabel
var caption_panel: PanelContainer
var cycle_area: Control
var pico_view
var perf_window
var zoom := 1.0
var zoom_label: Button
var diagram_area: Control
var diagram_split: HSplitContainer   # the diagram, with the walkthrough beside it
var walk_panel                       # every step of the program, and where you are (walk_panel.gd)
var layout_root: VBoxContainer
var right_split: VSplitContainer
var pico_overlay: Control
var pico_home: Control
var _board_used := false         # the program has touched I/O, or the student opened the board
var _board_path := ""            # the file _board_used is about
var panels: Control              # everything under the toolbar except the welcome page
var welcome: Control
var level_bar: PanelContainer     # "New at Level N" strip, after raising the level
var spotlight: Control            # outlines new controls for a few seconds
var tour: Control                 # the first-run tour (tour.gd)
var btn_guide: Button
var _tour_arg := false            # --tour: show the tour even if seen (screenshots)
var _tour_step := 0               # --tour-step N: open it at coach mark N
var heat_overlay := false         # View ▸ Heat overlay: tint the diagram by usage
var _heat_wait := 0.0
var _heat_key := ""
var research := false             # --research (or --dev): the heat map at any level
var _shown_level := 1             # the level the window is laid out for
var guide: Node

var btn_open: Button
var btn_reload: Button
var btn_cycle: Button
var btn_insn: Button
var btn_over: Button
var btn_back: Button
var btn_run: Button
var speed_slider: HSlider
var speed_label: Label
var mode_select: OptionButton
var port_select: OptionButton
var btn_ports: Button
var btn_connect: Button
var btn_flash: Button
var hw_help: Button        # Pico mode's "?": Running on a real Pico
var btn_diagram: Button
var machine_select: OptionButton
var view_menu: MenuButton          # View ▾: the colour scheme
var level_select: OptionButton
var examples_menu: MenuButton
var btn_perf: Button
var sim_controls: HBoxContainer   # stepping and speed: shown in Simulator mode only
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
var _demo_steps := 0              # --demo-steps N: press Step N times
var _demo_back := 0               # --demo-back N: then Step back N times
var _demo_run := -1               # --demo-run CPS: press Run at that speed (0 = Max)
var _demo_click_pin := 0
var _demo_component := ""
var _demo_click_gate := ""        # --demo-click-gate ID: click that buffer with a real mouse event
var _demo_machine := ""          # --machine=m0plus: start on that machine
var _demo_goto := ""             # --demo-goto STEP[:CYCLE]: the walkthrough's Go here, once it has loaded
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
	Backend.heat_received.connect(_on_heat_overlay)
	Backend.load_failed.connect(_on_load_failed)
	Backend.error_received.connect(_on_error)
	Backend.serial_ports_listed.connect(_on_ports)
	Backend.flash_finished.connect(_on_flash_done)
	Backend.connected.connect(func():
		_set_status("Connected to simulator", false)
		_send_options())
	Backend.disconnected.connect(func(): _set_status("Simulator backend disconnected", true))
	_set_status("Connecting to simulator…", false)
	Palette.changed.connect(_on_palette)
	Level.changed.connect(_on_level_changed)
	_shown_level = Level.current
	_apply_level()
	if Level.at_least(3):
		_set_heat_overlay(true, false)   # on from Level 3, without opening the diagram at launch
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
		if args[i] == "--demo-steps" and i + 1 < args.size():
			_demo_steps = int(args[i + 1])
		if args[i] == "--demo-back" and i + 1 < args.size():
			_demo_back = int(args[i + 1])
		if args[i] == "--demo-run" and i + 1 < args.size():
			_demo_run = int(args[i + 1])
		if args[i] == "--demo-click-pin" and i + 1 < args.size():
			_demo_click_pin = int(args[i + 1])
		if args[i] == "--demo-click-gate" and i + 1 < args.size():
			_demo_click_gate = args[i + 1]
		if args[i] == "--demo-goto" and i + 1 < args.size():
			_demo_goto = args[i + 1]
		if args[i].begins_with("--machine="):
			_demo_machine = args[i].substr(10)
		if args[i] == "--settings":
			_demo_settings = true
		if args[i] == "--shot-frames" and i + 1 < args.size():
			_screenshot_wait = int(args[i + 1])
		if args[i] == "--heat-overlay":
			_set_heat_overlay.call_deferred(true)
		if args[i] in ["--research", "--dev"]:
			research = true
		if args[i] == "--tour":
			_tour_arg = true
		if args[i] == "--tour-step" and i + 1 < args.size():
			_tour_arg = true
			_tour_step = int(args[i + 1])
		if args[i] == "--demo-level-up" and i + 1 < args.size():
			_demo_level = int(args[i + 1])
	if research:
		btn_perf.visible = true


func _process(_delta: float) -> void:
	if heat_overlay and diagram_area.visible:   # no heat requests while the diagram is hidden
		_poll_heat(_delta)
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
	if (_demo_steps > 0 or _demo_back > 0) and Backend.state.get("loaded", false):
		for i in _demo_steps:
			_step_insn()
		for i in _demo_back:
			Backend.send("step_back")
		_demo_steps = 0
		_demo_back = 0
	if _demo_goto != "" and walk_panel._items.size() > 0:
		walk_panel._goto(int(_demo_goto.get_slice(":", 0)), int(_demo_goto.get_slice(":", 1)))
		_demo_goto = ""
	if _demo_run >= 0 and Backend.state.get("loaded", false):
		var cps := _demo_run
		_demo_run = -1
		# after the level's set_options, which may load the program again
		get_tree().create_timer(1.0).timeout.connect(func(): Backend.send("run", {"cps": cps}))
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
	if _screenshot_path != "" and (Backend.state.has("loaded") or error_dialog.visible):
		_screenshot_frames += 1
		if _screenshot_frames == _screenshot_wait:
			RenderingServer.force_draw()    # macOS may skip frames for a window in the background
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
	root.add_child(_build_level_bar())
	var challenge_bar := ChallengeBar.new()
	challenge_bar.help_requested.connect(func(): if guide: guide.open("challenges"))
	root.add_child(challenge_bar)

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
	pico_view.opened.connect(func(): _board_used = true)
	pico_view.corner.add_child(HelpButton.new("pico", "Raspberry Pi Pico board"))
	pico_view.corner.move_child(pico_view.corner.get_child(-1), 0)   # left of Enlarge

	# right column: "Explain this step" on top (it folds away), then the
	# diagram, then the cycle inspector + console underneath
	explain_split = VSplitContainer.new()
	explain_split.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hsplit.add_child(explain_split)
	explain_panel = ExplainPanel.new()
	explain_split.add_child(explain_panel)
	right_split = VSplitContainer.new()
	right_split.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	right_split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	explain_split.add_child(right_split)
	explain_split.collapsed = not explain_panel.is_open
	explain_panel.toggled_open.connect(func(on): explain_split.collapsed = not on)

	# the multi-cycle datapath and the 2-stage pipeline share one area
	# … with the walkthrough beside it (walk_panel.gd)
	diagram_split = HSplitContainer.new()
	diagram_split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	diagram_split.size_flags_stretch_ratio = 2.2
	right_split.add_child(diagram_split)
	diagram_area = MarginContainer.new()   # stacks its children, full size
	diagram_area.size_flags_vertical = Control.SIZE_EXPAND_FILL
	diagram_area.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	diagram_area.custom_minimum_size = Vector2(500, 300)
	diagram_split.add_child(diagram_area)
	walk_panel = WalkPanel.new()
	diagram_split.add_child(walk_panel)
	diagram = DiagramView.new()
	diagram.set_speed(SPEEDS[int(speed_slider.value)])
	diagram_area.add_child(diagram)
	pipe_view = PipelineView.new()
	pipe_view.set_speed(SPEEDS[int(speed_slider.value)])
	pipe_view.visible = false
	diagram_area.add_child(pipe_view)
	# each drawing's "?" goes under its component card, so an open card covers it
	for v in [[diagram, "diagram", "Datapath diagram"], [pipe_view, "pipeline", "Pipeline diagram"]]:
		var h := HelpButton.new(v[1], v[2])
		v[0].add_child(h)
		v[0].move_child(h, 0)
		h.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT, Control.PRESET_MODE_MINSIZE, 4)
		h.grow_horizontal = Control.GROW_DIRECTION_BEGIN

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
	var room := StyleBoxEmpty.new()
	room.content_margin_right = 26   # the "?" sits in the top-right corner
	pipe_caption.add_theme_stylebox_override("normal", room)
	caption_panel.add_child(pipe_caption)
	var cap_help := HelpButton.new("pipecycles", "Pipeline cycle by cycle")
	cap_help.size_flags_horizontal = Control.SIZE_SHRINK_END
	cap_help.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	caption_panel.add_child(cap_help)
	cycle_panel.cycle_selected.connect(diagram.show_cycle)
	console_panel = ConsolePanel.new()
	console_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	console_panel.custom_minimum_size.x = 280
	bottom.add_child(console_panel)
	diagram_area.visible = false
	diagram_split.visible = false
	cycle_area.visible = false

	# Full-window host for the Pico board; pico_view is moved in here on demand.
	pico_overlay = Control.new()
	pico_overlay.set_anchors_preset(Control.PRESET_FULL_RECT)
	pico_overlay.visible = false
	add_child(pico_overlay)

	# on top of everything: outlines for controls that are new at this level
	spotlight = Spotlight.new()
	add_child(spotlight)
	tour = Tour.new()
	tour.spotlight = spotlight
	tour.finished.connect(_tour_seen)
	add_child(tour)

	file_dialog = FileDialog.new()
	file_dialog.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	file_dialog.access = FileDialog.ACCESS_FILESYSTEM
	file_dialog.filters = PackedStringArray(["*.s ; ARM assembly", "*.elf ; ELF binary"])
	file_dialog.use_native_dialog = true
	file_dialog.title = "Open assembly program"
	file_dialog.file_selected.connect(func(p): Backend.send("load", {"path": p}))
	var ex_dir := ProjectSettings.globalize_path("res://").path_join("../examples").simplify_path()
	for dir in [Welcome.copies_dir(), ex_dir]:     # the student's copies first
		if DirAccess.dir_exists_absolute(dir):
			file_dialog.current_dir = dir
			break
	add_child(file_dialog)

	error_dialog = AcceptDialog.new()
	error_dialog.title = "Could not load program"
	error_dialog.ok_button_text = "OK — I'll fix it"
	var err_text := RichTextLabel.new()
	err_text.name = "Text"
	err_text.bbcode_enabled = true
	err_text.selection_enabled = true
	err_text.custom_minimum_size = Vector2(620, 140)
	err_text.fit_content = true
	err_text.add_theme_font_override("mono_font", Backend.mono_font())
	error_dialog.add_child(err_text)
	error_dialog.add_button("Troubleshooting guide", true, "guide")
	error_dialog.custom_action.connect(func(a): if a == "guide" and guide:
		error_dialog.hide()
		guide.open("trouble"))
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
	btn_cycle = _button("Step cycle", "Advance one clock cycle (C or F11)", _step_cycle)
	btn_insn = _button("Step instruction", "Run one instruction, then stop (S or F10)", _step_insn)
	btn_over = _button("Step over", "Run a whole function call (bl) and stop at the line after it (O)\nAny other instruction is just stepped", func(): Backend.send("step_over"))
	btn_back = _button("◀ Step back", "Undo the last step: go back one instruction (B)\nPartway through an instruction it goes back one cycle", func(): Backend.send("step_back"))
	btn_run = _button("Run", "Run the program by itself, or pause it (Space or F5)", _toggle_run)
	btn_run.custom_minimum_size.x = 70
	for b in [btn_open, examples_menu, btn_reload, btn_settings, _sep()]:
		bar.add_child(b)
	# in Pico mode the board runs the program, so these give way to its port controls
	sim_controls = HBoxContainer.new()
	sim_controls.add_theme_constant_override("separation", 6)
	bar.add_child(sim_controls)
	for b in [btn_back, btn_insn, btn_over, btn_cycle, btn_run]:
		sim_controls.add_child(b)

	var sl := Label.new()
	sl.text = "Speed"
	sim_controls.add_child(sl)
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
	sim_controls.add_child(speed_slider)
	speed_label = Label.new()
	speed_label.custom_minimum_size.x = 90
	sim_controls.add_child(speed_label)
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
	hw_help = HelpButton.new("hardware", "Running on a real Pico")
	for b in [btn_ports, btn_connect, btn_flash, hw_help]:
		bar.add_child(b)

	# the status label takes the toolbar's spare width and grows to fit its
	# text (see _set_status), so large insn/cycle counts are never cut off
	status_label = Label.new()
	status_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	status_label.custom_minimum_size.x = 170
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
	view_menu = MenuButton.new()
	view_menu.text = "View"
	view_menu.flat = false
	view_menu.focus_mode = Control.FOCUS_NONE
	view_menu.tooltip_text = "Colour scheme (System follows your computer's light or dark setting)"
	var vp := view_menu.get_popup()
	vp.add_separator("Colours")
	for i in Palette.ORDER.size():
		vp.add_radio_check_item(Palette.TITLES[Palette.ORDER[i]], i)
	vp.add_separator()
	vp.add_item("Show the tour again", TOUR_ID)
	vp.add_check_item("Heat overlay on the CPU diagram", HEAT_ID)
	vp.set_item_tooltip(vp.get_item_index(HEAT_ID), "Tint each part of the CPU diagram by how much of the run it was used. On by itself from Level 3; untick it to turn it off. The numbers are the heat map's: Performance ▸ Heat map.")
	vp.id_pressed.connect(_on_view_menu)
	bar.add_child(view_menu)
	_check_scheme()
	var perf: Node = load("res://scripts/perf_window.gd").new()
	add_child(perf)
	btn_perf = _button("Performance", "Cycles, CPI, the RP2040 pipeline and design comparisons (F2)", func(): perf.open())
	bar.add_child(btn_perf)
	perf_window = perf
	perf.heat_tab.guide_requested.connect(func(t): if guide: guide.open(t))
	perf.heat_tab.component_clicked.connect(_on_heat_component)
	level_select = OptionButton.new()
	level_select.fit_to_longest_item = false   # the button says "Level N"; the list has the names
	for n in range(1, Level.MAX + 1):
		level_select.add_item("Level " + Level.title(n))
		level_select.set_item_tooltip(n - 1, Level.ABOUT[n - 1])
	level_select.focus_mode = Control.FOCUS_NONE
	level_select.tooltip_text = "How much of picosim to show. Start at 1 and move up as your class covers more:\n" + \
		"\n".join(range(1, Level.MAX + 1).map(func(n): return Level.title(n) + " — " + Level.ABOUT[n - 1]))
	level_select.item_selected.connect(func(i):
		Level.set_level(i + 1)
		level_select.text = "Level %d" % (i + 1))
	bar.add_child(level_select)
	if ResourceLoader.exists(GUIDE):
		guide = load(GUIDE).new()
		add_child(guide)
		btn_guide = _button("Guide", "How to use picosim (F1)", func(): guide.open())
		var vp2 := view_menu.get_popup()
		vp2.add_separator("Help")
		vp2.add_item("Keyboard shortcuts", KEYS_ID)
		vp2.add_item("Layout and zoom", LAYOUT_ID)
		bar.add_child(btn_guide)
	return bar


## The six coach marks of the first-run tour (tour.gd).
func _start_tour() -> void:
	level_bar.visible = false
	if mem_panel.disasm_hidden and not walk_panel.is_open:
		walk_panel.set_open(true, false)        # "Your program" points at it
	tour.start([
		[btn_insn, "Step through your program",
			"Press [b]Step instruction[/b] (or the [b]S[/b] key) to run one line of your program, then look at what changed. [b]◀ Step back[/b] ([b]B[/b]) undoes a step, so try things freely. [b]Run[/b] ([b]Space[/b]) lets the program go by itself."],
		[explain_panel, "Explain this step",
			"After every step this panel says in plain words what the processor just did, and what the next step will do: where the program goes next and why. Tick [b]More detail[/b] to see exactly what changed. Click its title to fold it away."],
		[regs_panel, "Registers",
			"The CPU's 16 registers, the numbers it is working on. Whatever the last step changed turns [color={yellow}]yellow[/color], and the line under the flags says which line of your file changed it."],
		[walk_panel, "Your program",
			"Every instruction your program runs, in order, with what it does. The ▶ row is where you are; click any step to read it, and [b]Go here[/b] jumps there. Click a step's ● column to set a breakpoint, where Run stops. The [b]Memory[/b] tab under the Registers shows the bytes in memory."]
		if mem_panel.disasm_hidden else [mem_panel, "Your program",
			"Each row is one instruction: its address, its bits, and the line of your file it came from. The highlighted row runs next. Click a row's first column to set a breakpoint, where Run stops."],
		[console_panel, "Serial console",
			"What your program prints appears here. When it waits for a key ([code]getchar[/code]), click here and type."],
		[btn_guide, "The guide",
			"The guide explains each panel and walks you through the first programs. Open it here or with [b]F1[/b]. To see this tour again, use [b]View ▸ Show the tour again[/b]."],
	])


	if _tour_step > 1:
		tour._show(_tour_step - 1)
		_tour_step = 0


func _tour_seen() -> void:
	var cfg := ConfigFile.new()
	cfg.load(SETTINGS)
	cfg.set_value("ui", "tour_seen", true)
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
	2: ["Level 2 · Memory & I/O", "every instruction is shown now, the built-in OS's too ([b]Step over[/b] skips a whole bl call), the [b]Memory[/b] tab (the bytes in memory and the stack), the [b]Raspberry Pi Pico[/b] board under it (the chip's pins) and the [b]Simulator[/b] menu (run your program on a real Pico).", "memmap"],
	3: ["Level 3 · Inside the CPU", "the [b]CPU diagram[/b] on the right with the cycle inspector under it and the [b]Walkthrough[/b] beside it (every step of your program, ▶ on the one you are on, and the breakpoints), so the Disassembly makes way for the [b]Memory[/b] tab while the diagram is shown, [b]Step cycle[/b] (F11) to run one clock cycle at a time, the datapath registers in the Registers panel, and the [b]heat overlay[/b]: each part of the diagram is tinted by how much your program has used it (View menu to turn it off).", "cpuintro"],
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
		spotlight.stop_new()


## Raising the level gives a fresh view: what the new level adds is switched
## on and shown (the Memory tab, the CPU diagram, the pipeline), the strip
## says what is new, and the new controls are outlined for a few seconds.
## Levels the student has had on before are not announced again: going back
## up to them only switches their views back on.
func _level_up(old: int, now: int) -> void:
	if old < 3 and now >= 3:
		btn_diagram.button_pressed = true          # the diagram and the cycle inspector
		_set_heat_overlay(true)                     # with the heat overlay on it
	if old < 4 and now >= 4:
		machine_select.select(1)                    # the 2-stage pipeline
		_on_machine(1)
	if now == 2:
		mem_panel.tabs.current_tab = 1              # Level 2's new view: the Memory tab
	var seen := maxi(old, Level.highest)           # Level.highest still holds the old value here
	if now <= seen:
		level_bar.visible = false
		return
	var fresh := []
	if seen < 2 and now >= 2:
		fresh += [btn_over, mode_select, mem_panel, pico_view]
	if seen < 3 and now >= 3:
		fresh += [btn_cycle, btn_diagram, diagram_area, walk_panel, cycle_area, regs_panel.dp_row, mem_panel]
	if seen < 4 and now >= 4:
		fresh += [machine_select, btn_perf]
		if seen >= 3:
			fresh.append(diagram_area)
	var t := ""
	for n in range(seen + 1, now + 1):
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
	pop.add_separator()
	var copy := str(Backend.program.get("path", "")) if Backend.state.get("loaded", false) else ""
	if Welcome.original_of(copy) != "":
		pop.add_item("Start %s over from the original…" % copy.get_file(), RESTORE_EXAMPLE_ID)
		pop.set_item_tooltip(pop.get_item_index(RESTORE_EXAMPLE_ID), "Replace your copy of this example with the original, undoing your changes")
	if DirAccess.dir_exists_absolute(Welcome.copies_dir()):
		pop.add_item("Show my copies folder", COPIES_FOLDER_ID)
		pop.set_item_tooltip(pop.get_item_index(COPIES_FOLDER_ID), "Your copies of the examples are in %s" % Welcome.copies_dir())
	if guide:
		pop.add_item("About the examples…", ABOUT_EXAMPLES_ID)
		pop.set_item_tooltip(pop.get_item_index(ABOUT_EXAMPLES_ID), "What each example shows and something to try in it (opens the guide)")


func _on_example(id: int) -> void:
	if id == ABOUT_EXAMPLES_ID:
		guide.open("examples2" if Level.at_least(2) else "examples")
		return
	if id == COPIES_FOLDER_ID:
		OS.shell_open(Welcome.copies_dir())
		return
	if id == RESTORE_EXAMPLE_ID:
		_confirm_restore(str(Backend.program.get("path", "")))
		return
	var path = examples_menu.get_popup().get_item_metadata(examples_menu.get_popup().get_item_index(id))
	if path != null:
		Welcome.open_example(path)


func _confirm_restore(copy: String) -> void:
	var d := ConfirmationDialog.new()
	d.title = "Start over from the original"
	d.dialog_text = "Replace your copy of %s with the original example?\nYour changes to it will be lost." % copy.get_file()
	d.ok_button_text = "Start over"
	d.confirmed.connect(func(): Welcome.restore_example(copy))
	d.visibility_changed.connect(func(): if not d.visible: d.queue_free())
	add_child(d)
	d.popup_centered()


## Show only what the current level needs. Lowering the level also undoes what
## the hidden controls had switched on (the diagram, the pipeline, Pico mode).
func _apply_level() -> void:
	var lv: int = Level.current
	level_select.select(lv - 1)
	level_select.text = "Level %d" % lv
	if lv < 4 and machine_select.selected == 1:
		machine_select.select(0)
		_on_machine(0)
	if lv < 3 and btn_diagram.button_pressed:
		btn_diagram.button_pressed = false
	if lv < 3 and heat_overlay and not research:
		_set_heat_overlay(false)
	if lv < 2 and mode_select.selected == 1:
		mode_select.select(0)
		_on_mode(0)
	if lv < 2 and pico_overlay.visible:
		pico_view.set_fullscreen(false)
	btn_cycle.visible = lv >= 3
	btn_over.visible = lv >= 2
	_send_options()
	btn_diagram.visible = lv >= 3
	machine_select.visible = lv >= 4
	btn_perf.visible = lv >= 4 or research
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
	for panel in [mem_panel, cycle_panel, console_panel, diagram, explain_panel]:
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
	# Measure with the controls of higher levels and the panels behind the
	# welcome page shown, so raising the level or loading a program never
	# overflows the window.  The Simulator's stepping controls and the Pico's
	# port controls take turns in the toolbar: measure each and keep the wider.
	var extra: Array = [btn_cycle, btn_over, btn_diagram,
		machine_select, btn_perf, mode_select, pico_view, panels].filter(func(c): return not c.visible)
	for c in extra:
		c.visible = true
	var wv := welcome.visible
	welcome.visible = false
	var pico_ctl: Array = [port_select, btn_ports, btn_connect, btn_flash, hw_help]
	var pico_was: Array = pico_ctl.map(func(c): return c.visible)
	var sim_was := sim_controls.visible
	var need := Vector2.ZERO
	for pico in [false, true]:
		sim_controls.visible = not pico
		for c in pico_ctl:
			c.visible = pico
		need = need.max(layout_root.get_combined_minimum_size())
	need += Vector2(12, 12)  # + root margins
	sim_controls.visible = sim_was
	for i in pico_ctl.size():
		pico_ctl[i].visible = pico_was[i]
	for c in extra:
		c.visible = false
	welcome.visible = wv
	var fit := minf(win_size.x / need.x, win_size.y / need.y) * 0.98
	return clampf(floorf(fit / 0.05) * 0.05, ZOOM_MIN, ZOOM_MAX)


func _on_view_menu(id: int) -> void:
	if id == HEAT_ID:
		_set_heat_overlay(not heat_overlay)
		return
	if id == KEYS_ID or id == LAYOUT_ID:
		guide.open("keys" if id == KEYS_ID else "layout")
		return
	if id == TOUR_ID:
		if Backend.state.get("loaded", false):
			_start_tour()
		else:
			Backend.send("load", {"path": Welcome.examples()[0]["path"]})
			_tour_arg = true      # starts once the example is on screen
		return
	Palette.set_scheme(Palette.ORDER[id])
	_check_scheme()


## View ▸ Heat overlay: the CPU (or pipeline) diagram's boxes are tinted
## by the heat map's totals, refreshed while the program runs.  It comes on
## by itself on reaching Level 3 and goes off below it (see _level_up,
## _apply_level); `show_diagram` also opens the diagram it tints.
func _set_heat_overlay(on: bool, show_diagram := true) -> void:
	heat_overlay = on
	var vp := view_menu.get_popup()
	vp.set_item_checked(vp.get_item_index(HEAT_ID), on)
	_heat_key = ""
	if on and show_diagram and Level.at_least(3) and not btn_diagram.button_pressed:
		btn_diagram.button_pressed = true
	if not on:
		for v in [diagram, pipe_view]:
			v.heat = {}
			v.queue_redraw()


func _poll_heat(delta: float) -> void:
	_heat_wait -= delta
	if _heat_wait > 0.0 or not Backend.state.get("loaded", false):
		return
	_heat_wait = 0.5
	var model := "m0plus" if _pipeline() else "multicycle"
	var key := "%s|%s|%s" % [model, Backend.state.get("cycles", 0), Backend.program.get("path", "")]
	if key == _heat_key:
		return
	_heat_key = key
	Backend.send("heat", {"view": "totals", "model": model, "req": -7})


## A row label clicked in the heat map: show that part on the CPU diagram.
func _on_heat_component(id: String) -> void:
	if not Level.at_least(3):
		return
	if not btn_diagram.button_pressed:
		btn_diagram.button_pressed = true
	if _pipeline():
		if pipe_view.BOXES.has(id):
			pipe_view.select_component(id)
	elif diagram.BOXES.has(id):
		diagram.select_component(id)
	elif id.ends_with(":r") or id.ends_with(":w"):
		diagram.select_component("regfile")


func _on_heat_overlay(d: Dictionary) -> void:
	if int(d.get("req", 0)) != -7 or not heat_overlay:
		return
	var h := HeatOverlay.from_totals(d)
	var target = pipe_view if d.get("model") == "m0plus" else diagram
	for v in [diagram, pipe_view]:
		v.heat = h if v == target else {}
		v.queue_redraw()


## Tick the colour scheme in use in the View menu.
func _check_scheme() -> void:
	var vp := view_menu.get_popup()
	for i in Palette.ORDER.size():
		vp.set_item_checked(vp.get_item_index(i), Palette.ORDER[i] == Palette.preference)


func _init_zoom() -> void:
	var cfg := ConfigFile.new()
	var saved = null
	if cfg.load(SETTINGS) == OK and cfg.has_section_key("ui", "zoom"):
		saved = cfg.get_value("ui", "zoom")
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


## Zoom no further than the whole toolbar fits: past that its last buttons
## (Level, Guide) would be cut off.
func _set_zoom(z: float, save := true) -> void:
	zoom = clampf(snappedf(z, 0.05), ZOOM_MIN, minf(ZOOM_MAX, maxf(ZOOM_MIN, _default_zoom())))
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


## Level 1 hides the built-in OS: programs start at main and a call into the
## OS (bl putchar) is one step.  From level 2 every instruction is shown.
func _send_options() -> void:
	if Backend.online:
		var hide := Level.current == 1
		Backend.send("set_options", {"skip_boot": hide, "step_over_os": hide})


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
	diagram_split.visible = on
	cycle_area.visible = on
	btn_diagram.text = "Hide CPU Diagram" if on else "Show CPU Diagram"
	console_panel.set_compact(on)
	explain_panel.set_diagram_shown(on)
	# the walkthrough beside the diagram lists the program: the Disassembly
	# makes way for the Memory tab (buttons for the diagram exist from Level 3)
	mem_panel.set_disasm_hidden(on and Level.at_least(3))


func _on_pico_fullscreen(on: bool) -> void:
	pico_view.reparent(pico_overlay if on else pico_home, false)
	if on:
		pico_view.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	pico_overlay.visible = on


func _unhandled_key_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k == null or not k.pressed:
		return
	# holding a step key repeats the step; other keys ignore auto-repeat.
	# Letter keys (S, O, C, B, Space) work on every keyboard: on a Mac, F11 shows
	# the desktop and the F keys need Fn.  A text field that has focus gets
	# its letters first, so typing never steps the program.
	var plain := not (k.is_command_or_control_pressed() or k.alt_pressed)
	if k.echo and not k.keycode in [KEY_F11, KEY_F10, KEY_S, KEY_C, KEY_B]:
		return
	var sim: bool = mode_select.selected == 0
	var loaded: bool = Backend.state.get("loaded", false)
	if plain and loaded and k.keycode in [KEY_S, KEY_O, KEY_C, KEY_B, KEY_SPACE]:
		if not sim:
			return
		match k.keycode:
			KEY_S: _step_insn()
			KEY_B:
				if not btn_back.disabled: Backend.send("step_back")
			KEY_O:
				if Level.at_least(2): Backend.send("step_over")
			KEY_C:
				if Level.at_least(3): _step_cycle()
			KEY_SPACE: _toggle_run()
		get_viewport().set_input_as_handled()
		return
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
			if Level.at_least(4) or research: perf_window.open()
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
	if not _board_used and (not st.get("gpio_changed", []).is_empty() or st.get("gpio_source", "sim") == "board" \
			or st.get("mem_access", []).any(func(a): return int(a["addr"]) >= 0x40000000)):
		_board_used = true
	pico_view.set_compact(not _board_used)
	pico_view.update_state(st)
	console_panel.update_state(st)
	cycle_panel.update_state(st)
	explain_panel.update_state(st)
	diagram.update_state(st)
	var m := MACHINES.find(str(st.get("machine", "multicycle")))
	if m >= 0 and m != machine_select.selected:
		machine_select.select(m)
		_apply_machine()
	pipe_view.update_state(st)
	var p = st.get("pipe")
	if p is Dictionary:
		var cap := PipelineView.caption(p)
		if cap != pipe_caption.text:   # parsing BBCode again is the costly part
			var scroll := pipe_caption.get_v_scroll_bar().value
			pipe_caption.text = cap
			pipe_caption.get_v_scroll_bar().value = scroll
	_update_controls()
	var loaded: bool = st.get("loaded", false)
	if loaded == welcome.visible:
		welcome.visible = not loaded
		panels.visible = loaded
		if loaded:
			var cfg := ConfigFile.new()
			cfg.load(SETTINGS)
			var first: bool = not cfg.get_value("ui", "tour_seen", false) and _screenshot_path == ""
			if first or _tour_arg:
				_tour_arg = false
				_start_tour.call_deferred()
		elif tour.visible:
			tour.stop()
	if loaded:
		var s := "insn %d · cycle %d" % [int(st["steps"]), int(st["cycles"])]
		if not Level.at_least(3):
			s = "instruction %d" % (int(st["steps"]) - int(st.get("boot_steps", 0)))   # level 1 counts from main; cycles come in at level 3
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
	if str(prog["path"]) != _board_path:
		_board_path = str(prog["path"])
		_board_used = false
	_board_used = _board_used or prog.get("uses_io", false)
	pico_view.set_compact(not _board_used)
	btn_settings.visible = not prog.get("settings", []).is_empty()
	btn_settings.text = "⚙ Settings •" if settings_dialog.changed() else "⚙ Settings"
	btn_settings.tooltip_text = "This program's settings, such as which pins your LED and button are on" + \
		("\n• You have changed some from the program's defaults." if settings_dialog.changed() else "")
	if _demo_settings and btn_settings.visible:
		settings_dialog.open.call_deferred()
		_demo_settings = false
	get_window().title = "picosim — " + str(prog["path"]).get_file()


static func _first_upper(s: String) -> String:
	return s.left(1).to_upper() + s.substr(1)


## The assembler's complaints, one block per problem: the line number, that
## line of the file, what is wrong and what usually causes it.
func _on_load_failed(path: String, message: String, errors: Array) -> void:
	var y := Palette.hex("changed")
	var dim := Palette.hex("dim")
	var t := "[b]%s[/b] has %s. Fix %s in your editor, save, and press [b]Reload[/b].\n" % [
		path.get_file(), "a mistake" if errors.size() <= 1 else "%d mistakes" % errors.size(),
		"it" if errors.size() <= 1 else "them"]
	for e in errors:
		t += "\n"
		if int(e["line"]) > 0:
			t += "[color=%s][b]Line %d:[/b][/color]   [code]%s[/code]\n" % [y, int(e["line"]), str(e["source"]).replace("[", "[lb]")]
		t += _first_upper(str(e["message"]).replace("[", "[lb]")) + "\n"
		if str(e["hint"]) != "":
			t += "[color=%s]%s[/color]\n" % [dim, e["hint"]]
	if errors.is_empty():
		t += "\n[code]%s[/code]\n" % message.replace("[", "[lb]")
	error_dialog.get_node("Text").text = t
	error_dialog.popup_centered(Vector2i(700, 0))
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
	_style_level_bar()
	caption_panel.add_theme_stylebox_override("panel", Palette.panel_box("panel", 6, 10))
	if Backend.state.get("pipe") is Dictionary:
		pipe_caption.text = PipelineView.caption(Backend.state["pipe"])
	_set_status(status_label.text, _status_bad)


func _set_status(text: String, bad: bool) -> void:
	_status_bad = bad
	Backend.set_text(status_label, text)
	status_label.tooltip_text = text
	var font := status_label.get_theme_font("font")
	var fsize := status_label.get_theme_font_size("font_size")
	var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize).x
	status_label.custom_minimum_size.x = maxf(170.0, ceilf(w) + 4.0)
	Backend.set_color(status_label, "font_color", Palette.c("error") if bad else Palette.c("muted"))


func _update_controls() -> void:
	var st := Backend.state
	var loaded: bool = st.get("loaded", false)
	var sim: bool = mode_select.selected == 0
	var halted: bool = st.get("halted", false)
	var p = st.get("pipe")
	if p is Dictionary:
		halted = not bool(p.get("more", true))   # the pipeline can still drain
	btn_reload.disabled = not loaded and str(st.get("path", "")) == ""   # also retries a failed load
	btn_cycle.disabled = not (loaded and sim) or halted
	btn_insn.disabled = not (loaded and sim) or halted
	btn_over.disabled = btn_insn.disabled
	btn_back.disabled = not (loaded and sim) or not st.get("can_back", false)
	btn_run.disabled = not (loaded and sim) or halted
	btn_run.text = "Pause" if st.get("running", false) else "Run"
	var pico_ctl := not sim and Level.at_least(2)
	sim_controls.visible = not pico_ctl
	port_select.visible = pico_ctl
	btn_ports.visible = pico_ctl
	btn_connect.visible = pico_ctl
	btn_flash.visible = pico_ctl
	hw_help.visible = pico_ctl and guide != null
	btn_connect.text = "Disconnect" if st.get("serial") != null else "Connect"
	btn_flash.disabled = not loaded or not str(st.get("path", "")).ends_with(".s")
