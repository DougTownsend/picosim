extends CenterContainer
## Start page shown in place of the panels while no program is loaded: what
## picosim is, the bundled examples (one click opens one), "Open your own .s
## file…" and a link to the guide.  main.gd shows it and wires the signals.
##
## examples() reads examples/*.s next to the gui folder; each file starts with
## "@ Title:", "@ About:" and "@ Level:" lines.  The toolbar's Examples menu
## uses the same list.  Clicking an example opens the student's own copy of
## it (open_example()), so the originals are never changed.

signal open_file_requested
signal guide_requested

var _card: PanelContainer
var _box: VBoxContainer


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


## Where the students' copies of the examples go: Documents/picosim.
static func copies_dir() -> String:
	var docs := OS.get_system_dir(OS.SYSTEM_DIR_DOCUMENTS)
	return (docs if docs != "" else OS.get_user_data_dir()).path_join("picosim")


## Open an example to work on: the student's copy of it in copies_dir() (made
## from the original the first time and kept after that, so their changes
## stay) is loaded and opened in a text editor beside picosim.
static func open_example(path: String) -> void:
	var copy := copies_dir().path_join(path.get_file())
	if not FileAccess.file_exists(copy):
		DirAccess.make_dir_recursive_absolute(copies_dir())
		if DirAccess.copy_absolute(path, copy) != OK:
			copy = path   # the folder cannot be written: work on the original
	Backend.send("load", {"path": copy})
	open_in_editor(copy)


## The original example `path` is a copy of, or "" when it is not one.
static func original_of(path: String) -> String:
	if path.get_base_dir().simplify_path() != copies_dir().simplify_path():
		return ""
	for e in examples():
		if str(e["path"]).get_file() == path.get_file():
			return e["path"]
	return ""


## Put the original back over the student's copy and load it again.
static func restore_example(copy: String) -> void:
	var orig := original_of(copy)
	if orig != "" and DirAccess.copy_absolute(orig, copy) == OK:
		Backend.send("load", {"path": copy})


## Open `path` in a text editor: VS Code when it is installed (the guide
## suggests it), else the app the system opens .s files with, else the
## system's plain text editor.
static func open_in_editor(path: String) -> void:
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


func _label(text: String, size: int, color: String, wrap := false) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", Palette.c(color))
	if wrap:
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return l


func _build() -> void:
	var sb := Palette.panel_box("panel", 10, 28)
	_card.add_theme_stylebox_override("panel", sb)
	for c in _box.get_children():
		c.queue_free()

	_box.add_child(_label("Welcome to picosim", 30, "text"))
	_box.add_child(_label("picosim runs assembly programs for the Raspberry Pi Pico and shows you, one step at a time, what the processor does with them. Try anything: nothing here can break, and Reload always starts your program over.", 16, "muted", true))

	_box.add_child(HSeparator.new())
	_box.add_child(_label("Start with an example", 19, "accent"))
	var list := GridContainer.new()
	list.columns = 3
	list.add_theme_constant_override("h_separation", 14)
	list.add_theme_constant_override("v_separation", 6)
	_box.add_child(list)
	for e in examples():
		var b := Button.new()
		b.text = str(e["title"])
		b.alignment = HORIZONTAL_ALIGNMENT_LEFT
		b.custom_minimum_size.x = 230
		b.focus_mode = Control.FOCUS_NONE
		b.tooltip_text = "Open your own copy of %s, in picosim and in a text editor to change it" % str(e["path"]).get_file()
		b.add_theme_font_size_override("font_size", 16)
		b.pressed.connect(func(): open_example(e["path"]))
		list.add_child(b)
		var about := _label(str(e["about"]), 14, "muted", true)
		about.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		about.custom_minimum_size.x = 380
		list.add_child(about)
		var lv := int(e["level"])
		list.add_child(_label("Level %d" % lv, 13, "dim" if Level.at_least(lv) else "yellow"))

	_box.add_child(HSeparator.new())
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	_box.add_child(row)
	var open := Button.new()
	open.text = "Open your own .s file…"
	open.focus_mode = Control.FOCUS_NONE
	open.add_theme_font_size_override("font_size", 16)
	open.pressed.connect(func(): open_file_requested.emit())
	row.add_child(open)
	var guide := Button.new()
	guide.text = "Read the guide (F1)"
	guide.focus_mode = Control.FOCUS_NONE
	guide.add_theme_font_size_override("font_size", 16)
	guide.pressed.connect(func(): guide_requested.emit())
	row.add_child(guide)

	_box.add_child(_label("You are on Level %s. Raise the Level (top right) when your class gets to memory, the Pico's pins or the inside of the CPU: each level shows a little more." % Level.title(Level.current), 14, "dim", true))
