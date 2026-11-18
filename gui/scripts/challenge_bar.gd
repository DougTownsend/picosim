extends PanelContainer
## The challenge strip under the toolbar.  A program whose header declares
## "@ Challenge:" and "@ Expect:" lines (see picosim/challenge.py) shows its
## task here while it runs; when it finishes, the strip turns green if every
## check passed, or says which check is not met yet and what happened
## instead (and the challenge's hint, if it has one).  Hidden for programs
## without a challenge, and by its ✕ until the next program is loaded.
## While the newest save does not build, the result is not shown as the
## student's: it belongs to the last build that worked.

signal help_requested

var _text: RichTextLabel
var _dismissed := false


func _ready() -> void:
	visible = false
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 12)
	add_child(h)
	_text = RichTextLabel.new()
	_text.bbcode_enabled = true
	_text.fit_content = true
	_text.scroll_active = false
	_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(_text)
	var help := Button.new()
	help.text = "About challenges"
	help.tooltip_text = "How challenges are checked, and how to write your own"
	help.focus_mode = Control.FOCUS_NONE
	help.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	help.pressed.connect(func(): help_requested.emit())
	h.add_child(help)
	var close := Button.new()
	close.text = "✕"
	close.tooltip_text = "Hide the challenge until the next program is loaded"
	close.focus_mode = Control.FOCUS_NONE
	close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	close.pressed.connect(func(): _dismissed = true; visible = false)
	h.add_child(close)
	Backend.program_loaded.connect(func(_p): _dismissed = false; _refresh())
	Backend.state_changed.connect(func(_s): _refresh())
	Palette.changed.connect(func(): set_meta("key", ""); _refresh())


func _refresh() -> void:
	var ch = Backend.program.get("challenge")
	var st := Backend.state
	var want: bool = ch is Dictionary and st.get("loaded", false) and not _dismissed \
		and st.get("mode", "sim") == "sim"
	visible = want
	if not want:
		return
	var checks = st.get("checks")
	var b = st.get("build")
	var stale = b.get("stale") if b is Dictionary else null
	var now := ""
	if stale is Dictionary and stale.get("same", true) and not stale.get("accepted", false):
		now = "stale"
	elif checks is Array:
		now = "pass" if checks.all(func(c): return c["ok"]) else "fail"
	var key := now + str(checks) + str(ch) + str(stale)
	if key == get_meta("key", ""):
		return
	set_meta("key", key)
	var task := str(ch.get("task", ""))
	if task == "":
		task = "Make these come true: " + ", ".join(PackedStringArray(ch.get("checks", [])))
	var t := ""
	var dim := Palette.hex("dim")
	match now:
		"stale":
			t = "[b]Challenge:[/b]   %s   [color=%s](not checked: your newest save, from %s, does not build yet)[/color]" % [task, dim, str(stale["saved"])]
		"pass":
			t = "[b][color=%s]✓ Challenge passed![/color][/b]   %s" % [Palette.hex("green"), task]
		"fail":
			t = "[b]Challenge: not yet[/b]   %s" % task
			for c in checks:
				if c["ok"]:
					t += "\n[color=%s]✓ %s[/color]" % [Palette.hex("green"), c["text"]]
				else:
					t += "\n[color=%s]✗ wanted %s, but %s[/color]" % [Palette.hex("changed"), c["text"], c["got"]]
			if str(ch.get("hint", "")) != "":
				t += "\n[color=%s]Hint: %s[/color]" % [dim, str(ch["hint"])]
			t += "\nChange your file, save it (picosim reloads it) and run it again."
		_:
			t = "[b]Challenge:[/b]   %s   [color=%s](checked when the program finishes)[/color]" % [task, dim]
	if stale is Dictionary and stale.get("accepted", false) and now in ["pass", "fail"]:
		t += "\n[color=%s]Checked on the last build that worked (saved %s), not on your newest save.[/color]" % [dim, str(stale.get("loaded_saved", ""))]
	_text.text = t
	var sb := Palette.panel_box("box_active", 6, 8)
	sb.border_color = Palette.c("green") if now == "pass" else (Palette.c("dim") if now == "stale" else Palette.c("accent"))
	sb.set_border_width_all(2)
	add_theme_stylebox_override("panel", sb)
