extends CenterContainer
## Start page shown in place of the panels while no program is loaded: what
## picosim is, the bundled examples, "Open your own .s file…" and a link to
## the guide.  main.gd shows it and wires the signals.
##
## examples() reads examples/*.s next to the gui folder; each file starts with
## "@ Title:", "@ About:" and "@ Level:" lines.  The toolbar's Examples menu
## uses the same list.  Each example has two buttons: its title runs the
## example as it is (nothing to set up, no editor; the original is never
## changed), and Edit my copy saves the student's own copy in copies_dir()
## and opens it in a text editor.  main.gd does both (example_requested),
## raising the level first if the example needs it.
##
## Everything here works from the keyboard: Tab and Shift+Tab move through
## the buttons (the one with focus has a ring), Enter or Space presses it.

signal open_file_requested
signal guide_requested
## An example's button: run it as it is, or (edit) edit the student's copy.
signal example_requested(example: Dictionary, edit: bool)

const SETTINGS := "user://settings.cfg"

var _card: PanelContainer
var _box: VBoxContainer
var _first: Button                 # gets the keyboard focus when the page is shown


## [{path, title, about, level}] for every examples/*.s, in file-name order.
static func examples() -> Array:
	var dir := ProjectSettings.globalize_path("res://").path_join("../examples").simplify_path()
	var out := []
	var names := Array(DirAccess.get_files_at(dir)).filter(func(f): return f.ends_with(".s"))
	names.sort()
	for f in names:
		var path := dir.path_join(f)
		var e := {"path": path, "title": f.get_basename(), "about": "", "level": 1}
		var fh := FileAccess.open(path, FileAccess.READ)
		while fh and not fh.eof_reached():
			var line := fh.get_line().strip_edges()
			if not line.begins_with("@"):
				break
			for key in ["Title", "About", "Level"]:
				var tag := "@ %s:" % key
				if line.begins_with(tag):
					var val := line.substr(tag.length()).strip_edges()
					e[key.to_lower()] = int(val) if key == "Level" else val
		out.append(e)
	return out


## The example with this file name, or {}.
static func example_named(file: String) -> Dictionary:
	for e in examples():
		if str(e["path"]).get_file() == file:
			return e
	return {}


## Where the students' copies of the examples go: Documents/picosim, or a
## folder the student picked when that could not be written (saved in
## settings.cfg), or PICOSIM_COPIES_DIR (a classroom's shared setup, tests).
static func copies_dir() -> String:
	var env := OS.get_environment("PICOSIM_COPIES_DIR")
	if env != "":
		return env
	var cfg := ConfigFile.new()
	if cfg.load(SETTINGS) == OK and str(cfg.get_value("ui", "copies_dir", "")) != "":
		return str(cfg.get_value("ui", "copies_dir"))
	var docs := OS.get_system_dir(OS.SYSTEM_DIR_DOCUMENTS)
	return (docs if docs != "" else OS.get_user_data_dir()).path_join("picosim")


static func set_copies_dir(dir: String) -> void:
	var cfg := ConfigFile.new()
	cfg.load(SETTINGS)
	cfg.set_value("ui", "copies_dir", dir)
	cfg.save(SETTINGS)


## copies_dir() as the student reads it: ~/Documents/picosim.
static func shown_dir(dir := "") -> String:
	if dir == "":
		dir = copies_dir()
	var home := OS.get_environment("HOME")
	if home != "" and dir.begins_with(home):
		return "~" + dir.substr(home.length())
	return dir


## The student's copy of example `path` (made from the original the first
## time and kept after that, so their changes stay):
## {ok, copy, new, error}.  Nothing is opened.
static func make_copy(path: String, dir := "") -> Dictionary:
	if dir == "":
		dir = copies_dir()
	var copy := dir.path_join(path.get_file())
	if FileAccess.file_exists(copy):
		return {"ok": true, "copy": copy, "new": false, "error": ""}
	var err := DirAccess.make_dir_recursive_absolute(dir)
	if err == OK:
		err = DirAccess.copy_absolute(path, copy)
	if err != OK:
		return {"ok": false, "copy": copy, "new": false, "error": error_string(err)}
	return {"ok": true, "copy": copy, "new": true, "error": ""}


## The original example `path` is a copy of, or "" when it is not one.
static func original_of(path: String) -> String:
	if path.get_base_dir().simplify_path() != copies_dir().simplify_path():
		return ""
	for e in examples():
		if str(e["path"]).get_file() == path.get_file():
			return e["path"]
	return ""


## The example `path` is (an original or the student's copy), or {}.
static func example_of(path: String) -> Dictionary:
	for e in examples():
		if str(e["path"]).simplify_path() == path.simplify_path():
			return e
	if original_of(path) != "":
		return example_named(path.get_file())
	return {}


## Put the original back over the student's copy and load it again.
static func restore_example(copy: String) -> void:
	var orig := original_of(copy)
	if orig != "" and DirAccess.copy_absolute(orig, copy) == OK:
		Backend.send("load", {"path": copy})


## Open `path` in a text editor: VS Code when it is installed (the guide
## suggests it), else the app the system opens .s files with, else the
## system's plain text editor.  PICOSIM_EDITOR names another editor to run,
## or "none" to open nothing (a classroom without one; tests).
static func open_in_editor(path: String) -> void:
	var editor := OS.get_environment("PICOSIM_EDITOR")
	if editor == "none":
		return
	if editor != "":
		OS.create_process(editor, [path])
		return
	match OS.get_name():
		"macOS":
			for app in ["/Applications/Visual Studio Code.app", OS.get_environment("HOME").path_join("Applications/Visual Studio Code.app")]:
				if DirAccess.dir_exists_absolute(app):
					OS.create_process("open", ["-a", app, path])
					return
			if OS.execute("open", [path]) != 0:   # no app for .s files
				OS.create_process("open", ["-t", path])
		"Windows":
			if OS.shell_open(path) != OK:
				OS.create_process("notepad.exe", [path])
		_:
			OS.shell_open(path)


func _ready() -> void:
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_card = PanelContainer.new()
	_card.custom_minimum_size.x = 760
	add_child(_card)
	_box = VBoxContainer.new()
	_box.add_theme_constant_override("separation", 10)
	_card.add_child(_box)
	_build()
	Palette.changed.connect(_build)
	Level.changed.connect(_build)
	visibility_changed.connect(_focus_first)
	_focus_first()


func _label(text: String, size: int, color: String, wrap := false) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", Palette.c(color))
	if wrap:
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return l


## A button the keyboard can reach: Tab moves to it, a ring shows it has
## focus, and Enter or Space presses it.
func _button(text: String, tip: String, size := 16) -> Button:
	var b := Button.new()
	b.text = text
	b.tooltip_text = tip
	b.focus_mode = Control.FOCUS_ALL
	b.add_theme_font_size_override("font_size", size)
	var ring := StyleBoxFlat.new()
	ring.draw_center = false
	ring.set_border_width_all(2)
	ring.border_color = Palette.c("focus")
	ring.set_corner_radius_all(5)
	ring.set_expand_margin_all(2)
	b.add_theme_stylebox_override("focus", ring)
	return b


## The first example gets the focus, so Tab, Enter and Space work at once.
func _focus_first() -> void:
	if is_visible_in_tree() and _first != null:
		(func(): if is_instance_valid(_first) and is_visible_in_tree() and get_viewport().gui_get_focus_owner() == null:
			_first.grab_focus()).call_deferred()


func _build() -> void:
	var sb := Palette.panel_box("panel", 10, 28)
	_card.add_theme_stylebox_override("panel", sb)
	var had_focus := get_viewport() != null and get_viewport().gui_get_focus_owner() != null \
		and is_ancestor_of(get_viewport().gui_get_focus_owner())
	for c in _box.get_children():
		_box.remove_child(c)
		c.queue_free()
	_first = null

	_box.add_child(_label("Welcome to picosim", 30, "text"))
	_box.add_child(_label("picosim runs assembly programs for the Raspberry Pi Pico and shows you, one step at a time, what the processor does with them. Try anything: nothing here can break, and Reload always starts your program over.", 16, "muted", true))

	_box.add_child(HSeparator.new())
	_box.add_child(_label("Start with an example", 19, "accent"))
	_box.add_child(_label("Click an example to run it. To change it, use Edit my copy: picosim saves your own copy and opens it in a text editor.", 14, "muted", true))
	var list := GridContainer.new()
	list.columns = 4
	list.add_theme_constant_override("h_separation", 14)
	list.add_theme_constant_override("v_separation", 6)
	_box.add_child(list)
	var dir := copies_dir()
	for e in examples():
		var file := str(e["path"]).get_file()
		var b := _button(str(e["title"]), "Run %s here as it is: nothing to set up, and the original stays unchanged" % file)
		b.alignment = HORIZONTAL_ALIGNMENT_LEFT
		b.custom_minimum_size.x = 230
		b.pressed.connect(func(): example_requested.emit(e, false))
		list.add_child(b)
		if _first == null:
			_first = b
		var about := _label(str(e["about"]), 14, "muted", true)
		about.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		about.custom_minimum_size.x = 360
		list.add_child(about)
		var has_copy := FileAccess.file_exists(dir.path_join(file))
		var ed := _button("Open my copy" if has_copy else "Edit my copy",
			("Open your copy, %s, in picosim and in a text editor" if has_copy else
			"Save your own copy as %s and open it in a text editor to change it") % shown_dir(dir.path_join(file)), 13)
		ed.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		ed.pressed.connect(func(): example_requested.emit(e, true))
		list.add_child(ed)
		var lv := int(e["level"])
		var tag := _label("Level %d" % lv, 13, "dim" if Level.at_least(lv) else "yellow")
		tag.tooltip_text = Level.ABOUT[lv - 1]
		tag.mouse_filter = Control.MOUSE_FILTER_PASS
		list.add_child(tag)
	_box.add_child(_label("Your copies are saved in %s." % shown_dir(dir), 13, "dim", true))

	_box.add_child(HSeparator.new())
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	_box.add_child(row)
	var open := _button("Open your own .s file…", "Open an assembly (.s) file (Ctrl/Cmd+O)")
	open.pressed.connect(func(): open_file_requested.emit())
	row.add_child(open)
	var guide := _button("Read the guide (F1)", "How to use picosim, starting with your first five minutes")
	guide.pressed.connect(func(): guide_requested.emit())
	row.add_child(guide)

	_box.add_child(_label("You are on Level %s. Raise the Level (top right) when your class gets to memory, the Pico's pins or the inside of the CPU: each level shows a little more. Keys: Tab moves between buttons, Enter or Space presses one." % Level.title(Level.current), 14, "dim", true))
	if had_focus:
		_focus_first()
