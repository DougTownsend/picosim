extends PanelContainer
## Serial text I/O.  In Simulator mode output comes from putchar and input
## feeds getchar; in Pico mode both go over the board's USB serial port.
## Click the output area and type to send keystrokes immediately, or use
## the line box below (Enter sends the line followed by a carriage return).

const SYS_COLOR := Color("7f8794")
const PICO_COLOR := Color("9ece6a")
const SIM_COLOR := Color("d7dae0")

var title: Label
var status: Label
var out: RichTextLabel
var line_edit: LineEdit
var _last_cr := false
var font: Font


func _ready() -> void:
	font = Backend.mono_font()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color("22262e")
	sb.set_corner_radius_all(6)
	sb.content_margin_left = 8
	sb.content_margin_right = 8
	sb.content_margin_top = 6
	sb.content_margin_bottom = 8
	add_theme_stylebox_override("panel", sb)

	var v := VBoxContainer.new()
	add_child(v)
	var head := HBoxContainer.new()
	v.add_child(head)
	title = Label.new()
	title.clip_text = true
	title.custom_minimum_size.x = 260
	title.text = "USB Serial — Simulator"
	title.add_theme_font_size_override("font_size", 15)
	head.add_child(title)
	status = Label.new()
	status.clip_text = true
	status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	status.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	status.add_theme_color_override("font_color", Color("f2cc60"))
	head.add_child(status)
	var clear := Button.new()
	clear.text = "Clear"
	clear.focus_mode = Control.FOCUS_NONE
	clear.pressed.connect(func(): out.clear())
	head.add_child(clear)

	out = RichTextLabel.new()
	out.size_flags_vertical = Control.SIZE_EXPAND_FILL
	out.custom_minimum_size.y = 90
	out.scroll_following = true
	out.selection_enabled = true
	out.focus_mode = Control.FOCUS_ALL
	out.add_theme_font_override("normal_font", font)
	out.add_theme_font_size_override("normal_font_size", 14)
	var osb := StyleBoxFlat.new()
	osb.bg_color = Color("15171c")
	osb.set_corner_radius_all(4)
	osb.content_margin_left = 6
	osb.content_margin_top = 4
	out.add_theme_stylebox_override("normal", osb)
	var fsb := osb.duplicate()
	fsb.border_color = Color("4a78c2")
	fsb.set_border_width_all(1)
	out.add_theme_stylebox_override("focus", fsb)
	out.tooltip_text = "Click here and type: each key is sent immediately"
	out.gui_input.connect(_on_out_input)
	v.add_child(out)

	var row := HBoxContainer.new()
	v.add_child(row)
	line_edit = LineEdit.new()
	line_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	line_edit.placeholder_text = "Type a line and press Enter (sent with \\r)"
	line_edit.add_theme_font_override("font", font)
	line_edit.text_submitted.connect(_on_line)
	row.add_child(line_edit)

	Backend.console_output.connect(_on_output)


func set_compact(on: bool) -> void:
	out.custom_minimum_size.y = 60 if on else 90


func append_system(text: String) -> void:
	_append(text, SYS_COLOR)


func _on_output(text: String, source: String) -> void:
	var col := SIM_COLOR
	if source == "system":
		col = SYS_COLOR
	elif source == "pico":
		col = PICO_COLOR
	_append(text, col)


func _append(text: String, col: Color) -> void:
	# Normalise line endings: \r\n and lone \r both become a newline.
	var clean := ""
	for ch in text:
		if ch == "\r":
			clean += "\n"
			_last_cr = true
			continue
		if ch == "\n" and _last_cr:
			_last_cr = false
			continue
		_last_cr = false
		clean += ch
	out.push_color(col)
	out.add_text(clean)
	out.pop()


func _send(text: String) -> void:
	Backend.send("console_input", {"text": text})


func _on_line(text: String) -> void:
	_send(text + "\r")
	line_edit.clear()


func _on_out_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k == null or not k.pressed:
		return
	if k.is_command_or_control_pressed() and k.keycode == KEY_C:
		return     # let copy through
	if k.keycode in [KEY_F5, KEY_F10, KEY_F11]:
		return
	var text := ""
	match k.keycode:
		KEY_ENTER, KEY_KP_ENTER: text = "\r"
		KEY_BACKSPACE: text = "\b"
		KEY_TAB: text = "\t"
		KEY_ESCAPE: text = "\u001b"
		_:
			if k.unicode > 0 and k.unicode < 256:
				text = char(k.unicode)
	if text != "":
		_send(text)
		out.accept_event()


func update_state(st: Dictionary) -> void:
	var mode: String = st.get("mode", "sim")
	if mode == "pico":
		var port = st.get("serial")
		title.text = "USB Serial — Pico " + (str(port).get_file() if port != null else "(not connected)")
		status.text = ""
	else:
		title.text = "USB Serial — Simulator"
		if st.get("waiting_input", false):
			status.text = "⌨ program is waiting for input (getchar)"
		elif st.get("halted", false):
			status.text = "program halted"
		else:
			status.text = ""
