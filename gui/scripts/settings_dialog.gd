extends ConfirmationDialog
## "Program settings": a form for the settings a program declares (.equ lines
## marked "@ <-", see picosim/settings.py), so students can set their pins
## and wiring without editing code. The values are kept by picosim for this
## student, never written into the program file; Apply sends them to the
## backend, which loads the program again with them filled in.

var _rows: Array = []          # [{name, control, choices}]
var _box: VBoxContainer
var _error: Label
var _settings: Array = []
var _pending := {}             # values sent with the last Apply (kept if rejected)


func _ready() -> void:
	title = "Program settings"
	ok_button_text = "Apply and reload"
	cancel_button_text = "Close"
	add_button("Use the program's defaults", true, "defaults")
	custom_action.connect(_on_action)
	confirmed.connect(_apply)
	min_size = Vector2i(640, 200)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(620, 0)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(scroll)
	_box = VBoxContainer.new()
	_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_box.add_theme_constant_override("separation", 10)
	scroll.add_child(_box)
	Backend.program_loaded.connect(func(p): _pending = {}; set_settings(p.get("settings", [])))
	Backend.settings_error.connect(_on_error)


## True when the loaded program declares any settings.
func has_settings() -> bool:
	return not _settings.is_empty()


## True when any setting differs from the program's own default.
func changed() -> bool:
	for s in _settings:
		if str(s["value"]) != str(s["default"]):
			return true
	return false


func set_settings(list: Array) -> void:
	_settings = list
	if visible:
		_build("")


func open() -> void:
	_build("")
	popup_centered(Vector2i(680, mini(150 + 64 * _settings.size(), 640)))


func _label(text: String, size: int, color: String) -> Label:
	var l := Label.new()
	l.text = text
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", Palette.c(color))
	return l


func _build(error: String) -> void:
	for c in _box.get_children():
		c.queue_free()
	_rows.clear()
	var file := str(Backend.program.get("path", "")).get_file()
	title = "Program settings — " + file
	_box.add_child(_label("Set these to match how your circuit is wired. picosim remembers them for you; the program file itself is not changed.", 14, "muted"))
	for s in _settings:
		var row := VBoxContainer.new()
		row.add_theme_constant_override("separation", 2)
		_box.add_child(row)
		var top := HBoxContainer.new()
		top.add_theme_constant_override("separation", 12)
		row.add_child(top)
		var name_lbl := Label.new()
		name_lbl.text = str(s["name"])
		name_lbl.add_theme_font_override("font", Backend.mono_font())
		name_lbl.add_theme_font_size_override("font_size", 14)
		name_lbl.custom_minimum_size.x = 170
		top.add_child(name_lbl)
		var value := str(_pending.get(s["name"], s["value"])) if error != "" else str(s["value"])
		var control: Control
		var choices: Array = s.get("choices", [])
		if not choices.is_empty():
			var ob := OptionButton.new()
			for c in choices:
				ob.add_item(str(c["label"]))
				ob.set_item_metadata(ob.item_count - 1, str(c["value"]))
				if str(c["value"]).to_int() == value.to_int():
					ob.select(ob.item_count - 1)
			ob.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			ob.fit_to_longest_item = false
			control = ob
		else:
			var le := LineEdit.new()
			le.text = value
			le.custom_minimum_size.x = 120
			le.add_theme_font_override("font", Backend.mono_font())
			le.text_submitted.connect(func(_t): _apply(); hide())
			control = le
		top.add_child(control)
		var help := str(s["help"]) if choices.is_empty() else ""
		var dflt := str(s["default"])
		for c in choices:
			if str(c["value"]).to_int() == str(s["default"]).to_int():
				dflt = str(c["label"])
				break
		var note := (help + "   " if help != "" else "") + "(the program's default: %s)" % dflt
		row.add_child(_label(note, 13, "dim"))
		_rows.append({"name": s["name"], "control": control})
	_error = _label(error, 14, "error")
	_error.visible = error != ""
	_box.add_child(_error)


func _values() -> Dictionary:
	var out := {}
	for r in _rows:
		var c: Control = r["control"]
		if c is OptionButton:
			out[r["name"]] = str(c.get_item_metadata(c.selected))
		else:
			out[r["name"]] = (c as LineEdit).text.strip_edges()
	return out


func _apply() -> void:
	_pending = _values()
	Backend.send("set_settings", {"values": _pending})


func _on_action(action: StringName) -> void:
	if action == "defaults":
		Backend.send("set_settings", {"defaults": true})
		hide()


func _on_error(message: String) -> void:
	_build(message)
	if not visible:
		popup_centered(Vector2i(680, mini(200 + 90 * _settings.size(), 680)))
