extends Control
## Top-level layout:
##   left  — registers (top) and memory (bottom)
##   right — toolbar, optional CPU diagram + cycle inspector, serial console

const RegistersPanel := preload("res://scripts/registers_panel.gd")
const MemoryPanel := preload("res://scripts/memory_panel.gd")
const ConsolePanel := preload("res://scripts/console_panel.gd")
const DiagramView := preload("res://scripts/diagram_view.gd")
const CyclePanel := preload("res://scripts/cycle_panel.gd")
const PicoView := preload("res://scripts/pico_view.gd")

## Clock-speed presets (cycles per second); 0 = as fast as possible.
const ZOOM_MIN := 0.5
const ZOOM_MAX := 3.0
const ZOOM_STEP := 0.1
const SETTINGS := "user://settings.cfg"
const SPEEDS := [1, 2, 4, 8, 16, 50, 200, 1000, 10000, 100000, 0]
## Optional user guide: if this file is deleted the Guide button just disappears.
const GUIDE := "res://scripts/guide.gd"

var regs_panel
var mem_panel
var console_panel
var diagram
var cycle_panel
var pico_view
var zoom := 1.0
var zoom_label: Button
var diagram_area: Control
var right_split: VSplitContainer
var pico_overlay: Control
var pico_home: Control

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
var status_label: Label
var file_dialog: FileDialog
var error_dialog: AcceptDialog
var _screenshot_path := ""
var _screenshot_frames := 0
var _demo_cycles := 0
var _demo_click_pin := 0
var _demo_component := ""


func _ready() -> void:
	_build_ui()
	_init_zoom()
	Backend.state_changed.connect(_on_state)
	Backend.program_loaded.connect(_on_program)
	Backend.load_failed.connect(_on_load_failed)
	Backend.error_received.connect(_on_error)
	Backend.serial_ports_listed.connect(_on_ports)
	Backend.flash_finished.connect(_on_flash_done)
	Backend.connected.connect(func(): _set_status("Connected to simulator", false))
	Backend.disconnected.connect(func(): _set_status("Simulator backend disconnected", true))
	_set_status("Connecting to simulator…", false)
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


func _process(_delta: float) -> void:
	# --screenshot PATH: grab the window once the UI has settled, then quit.
	if _demo_cycles > 0 and Backend.state.get("loaded", false):
		for i in _demo_cycles:
			Backend.send("step_cycle")
		_demo_cycles = 0
	if _demo_click_pin > 0 and Backend.state.get("loaded", false) and _screenshot_frames == 20:
		pico_view.demo_click(_demo_click_pin)
		_demo_click_pin = 0
	if _demo_component != "" and Backend.state.get("loaded", false) and _screenshot_frames == 10:
		diagram.select_component(_demo_component)
		_demo_component = ""
	if _screenshot_path != "" and Backend.state.get("loaded", false):
		_screenshot_frames += 1
		if _screenshot_frames == 40:
			get_viewport().get_texture().get_image().save_png(_screenshot_path)
			get_tree().quit()


# ── layout ──────────────────────────────────────────────────────────────────

func _build_ui() -> void:
	var bg := ColorRect.new()
	bg.color = Color("1b1e24")
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(bg)

	var root := VBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.offset_left = 6
	root.offset_top = 6
	root.offset_right = -6
	root.offset_bottom = -6
	add_child(root)

	root.add_child(_build_toolbar())

	var hsplit := HSplitContainer.new()
	hsplit.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(hsplit)

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

	diagram = DiagramView.new()
	diagram.size_flags_vertical = Control.SIZE_EXPAND_FILL
	diagram.size_flags_stretch_ratio = 2.2
	diagram.custom_minimum_size = Vector2(500, 300)
	diagram_area = diagram
	diagram.set_speed(SPEEDS[int(speed_slider.value)])
	right_split.add_child(diagram)

	var bottom := HSplitContainer.new()
	bottom.size_flags_vertical = Control.SIZE_EXPAND_FILL
	right_split.add_child(bottom)
	cycle_panel = CyclePanel.new()
	cycle_panel.custom_minimum_size.x = 600
	cycle_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	cycle_panel.size_flags_stretch_ratio = 1.6
	bottom.add_child(cycle_panel)
	cycle_panel.cycle_selected.connect(diagram.show_cycle)
	console_panel = ConsolePanel.new()
	console_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	console_panel.custom_minimum_size.x = 280
	bottom.add_child(console_panel)
	diagram.visible = false
	cycle_panel.visible = false

	# Full-window host for the Pico board; pico_view is moved in here on demand.
	pico_overlay = Control.new()
	pico_overlay.set_anchors_preset(Control.PRESET_FULL_RECT)
	pico_overlay.visible = false
	add_child(pico_overlay)

	file_dialog = FileDialog.new()
	file_dialog.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	file_dialog.access = FileDialog.ACCESS_FILESYSTEM
	file_dialog.filters = PackedStringArray(["*.s ; ARM assembly", "*.elf ; ELF binary"])
	file_dialog.use_native_dialog = true
	file_dialog.title = "Open assembly program"
	file_dialog.file_selected.connect(func(p): Backend.send("load", {"path": p}))
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
	btn_open = _button("Open…", "Open a .s file (Ctrl+O)", func(): file_dialog.popup_centered_ratio(0.6))
	btn_reload = _button("Reload", "Re-assemble the file and reset the CPU (Ctrl+R)", func(): Backend.send("reload"))
	btn_cycle = _button("Step Cycle", "Advance one clock cycle (F11)", _step_cycle)
	btn_insn = _button("Step Insn", "Finish the current instruction (F10)", _step_insn)
	btn_run = _button("Run", "Run / pause (F5)", _toggle_run)
	btn_run.custom_minimum_size.x = 70
	for b in [btn_open, btn_reload, _sep(), btn_cycle, btn_insn, btn_run]:
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
	bar.add_child(speed_slider)
	speed_label = Label.new()
	speed_label.custom_minimum_size.x = 90
	bar.add_child(speed_label)
	_on_speed(speed_slider.value)

	bar.add_child(_sep())
	mode_select = OptionButton.new()
	mode_select.add_item("Simulator", 0)
	mode_select.add_item("Pico (USB serial)", 1)
	mode_select.focus_mode = Control.FOCUS_NONE
	mode_select.item_selected.connect(_on_mode)
	bar.add_child(mode_select)
	port_select = OptionButton.new()
	port_select.custom_minimum_size.x = 180
	port_select.focus_mode = Control.FOCUS_NONE
	bar.add_child(port_select)
	btn_ports = _button("⟳", "Refresh serial ports", func(): Backend.send("serial_list"))
	btn_connect = _button("Connect", "Open the selected serial port", _on_connect)
	btn_flash = _button("Flash", "Build a .uf2 and upload it to the Pico", func(): Backend.send("flash"))
	for b in [btn_ports, btn_connect, btn_flash]:
		bar.add_child(b)

	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bar.add_child(spacer)
	status_label = Label.new()
	status_label.clip_text = true
	status_label.custom_minimum_size.x = 260
	status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	bar.add_child(status_label)
	var zoom_out := _button("−", "Zoom out (Ctrl/Cmd −)", func(): _set_zoom(zoom - ZOOM_STEP))
	zoom_label = _button("100%", "Reset zoom (Ctrl/Cmd 0)", func(): _set_zoom(_default_zoom()))
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
	if ResourceLoader.exists(GUIDE):
		var guide: Node = load(GUIDE).new()
		add_child(guide)
		bar.add_child(_button("Guide", "How to use picosim (F1)", func(): guide.open()))
	return bar


# ── zoom ────────────────────────────────────────────────────────────────────
# The whole UI is scaled with the window's content scale factor.  On a
# Retina/HiDPI screen Godot renders at physical resolution, so the default
# zoom follows the screen's scale factor.  The chosen zoom is remembered.

func _default_zoom() -> float:
	return clampf(DisplayServer.screen_get_scale(get_window().current_screen) * 0.8, 1.0, ZOOM_MAX)


func _init_zoom() -> void:
	var cfg := ConfigFile.new()
	var z := _default_zoom()
	if cfg.load(SETTINGS) == OK:
		z = float(cfg.get_value("ui", "zoom", z))
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--zoom="):
			z = float(arg.substr(7))
	# Fill most of the screen on first launch so a larger zoom still fits.
	var win := get_window()
	var usable := DisplayServer.screen_get_usable_rect(win.current_screen)
	var target := Vector2i(usable.size * 0.9)
	if target.x > win.size.x:
		win.size = target
		win.position = usable.position + (usable.size - target) / 2
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


func _on_diagram_toggled(on: bool) -> void:
	diagram_area.visible = on
	cycle_panel.visible = on
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
			if sim: _step_cycle()
		KEY_F10:
			if sim: _step_insn()
		KEY_F5:
			if sim: _toggle_run()
		KEY_O:
			if k.is_command_or_control_pressed(): file_dialog.popup_centered_ratio(0.6)
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
	_update_controls()
	if st.get("loaded", false):
		var s := "insn %d · cycle %d" % [int(st["steps"]), int(st["cycles"])]
		if st.get("halted", false):
			s += "   — halted"
		elif st.get("waiting_input", false):
			s += "   — waiting for input"
		_set_status(s, false)


func _on_program(prog: Dictionary) -> void:
	mem_panel.set_program(prog)
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


func _set_status(text: String, bad: bool) -> void:
	status_label.text = text
	status_label.tooltip_text = text
	status_label.add_theme_color_override("font_color", Color("ff7b72") if bad else Color("9da5b4"))


func _update_controls() -> void:
	var st := Backend.state
	var loaded: bool = st.get("loaded", false)
	var sim: bool = mode_select.selected == 0
	var halted: bool = st.get("halted", false)
	btn_reload.disabled = not loaded
	btn_cycle.disabled = not (loaded and sim) or halted
	btn_insn.disabled = not (loaded and sim) or halted
	btn_run.disabled = not (loaded and sim) or halted
	btn_run.text = "Pause" if st.get("running", false) else "Run"
	port_select.visible = not sim
	btn_ports.visible = not sim
	btn_connect.visible = not sim
	btn_flash.visible = not sim
	btn_connect.text = "Disconnect" if st.get("serial") != null else "Connect"
	btn_flash.disabled = not loaded or not str(st.get("path", "")).ends_with(".s")
