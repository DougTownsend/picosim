extends PanelContainer
## A strip under the toolbar, like the "New at Level N" one: a line or two
## of text and a few buttons.  main.gd keeps two: one for a save that does
## not build (it stays until the file builds) and one for short notes, such
## as where to look in an example that was just opened, or why the pipeline
## waits for the end of an instruction.
##
## show_notice(text, actions, key): `actions` is [[label, tooltip, Callable]];
## each button runs its Callable.  `closable` adds a ✕ that hides the strip.

var _text: RichTextLabel
var _buttons: HBoxContainer
var _close: Button
var border_key := "changed"       # the Palette colour of the strip's border
var key := ""                     # what the note is about (show_notice's key)


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
	_text.meta_clicked.connect(func(m): get_tree().call_group("guide", "open", str(m)))
	h.add_child(_text)
	_buttons = HBoxContainer.new()
	_buttons.add_theme_constant_override("separation", 6)
	_buttons.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(_buttons)
	_close = Button.new()
	_close.text = "✕"
	_close.tooltip_text = "Hide this"
	_close.focus_mode = Control.FOCUS_NONE
	_close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_close.pressed.connect(hide_notice)
	h.add_child(_close)
	_apply_palette()
	Palette.changed.connect(_apply_palette)


func show_notice(text: String, actions: Array = [], what := "", closable := true) -> void:
	key = what
	_text.text = text
	for b in _buttons.get_children():
		b.queue_free()
	for a in actions:
		var b := Button.new()
		b.text = str(a[0])
		b.tooltip_text = str(a[1])
		b.focus_mode = Control.FOCUS_NONE
		var cb: Callable = a[2]
		b.pressed.connect(func(): cb.call())
		_buttons.add_child(b)
	_close.visible = closable
	visible = true


func hide_notice() -> void:
	visible = false
	key = ""


func _apply_palette() -> void:
	var sb := Palette.panel_box("box_active", 6, 8)
	sb.border_color = Palette.c(border_key)
	sb.set_border_width_all(2)
	add_theme_stylebox_override("panel", sb)
