extends Control
## The first-run tour: the first time a program is loaded, six coach marks
## point out Step, Explain this step, the Registers, the Disassembly, the Console and the Guide,
## one at a time, each with Next and Skip.  The spotlight outlines the control
## being explained.  Finishing or skipping it is remembered in settings.cfg
## ([ui] tour_seen); View ▸ Show the tour again starts it over.
## The window stays usable while it is shown: the student can press Step
## during step 1 and watch what it does.

signal finished

const WIDTH := 360.0
const GAP := 10.0

var spotlight: Control
var _steps: Array = []        # [Control, title, bbcode text]
var _i := 0
var _bubble: PanelContainer
var _title: Label
var _text: RichTextLabel
var _count: Label
var _next: Button


func _ready() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	visible = false
	_bubble = PanelContainer.new()
	_bubble.mouse_filter = Control.MOUSE_FILTER_STOP
	_bubble.custom_minimum_size.x = WIDTH
	add_child(_bubble)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	_bubble.add_child(v)
	_title = Label.new()
	_title.add_theme_font_size_override("font_size", 16)
	v.add_child(_title)
	_text = RichTextLabel.new()
	_text.bbcode_enabled = true
	_text.fit_content = true
	_text.scroll_active = false
	_text.custom_minimum_size.x = WIDTH - 24
	_text.add_theme_font_size_override("normal_font_size", 14)
	_text.add_theme_font_size_override("bold_font_size", 14)
	v.add_child(_text)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	v.add_child(row)
	_count = Label.new()
	_count.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_count)
	var skip := Button.new()
	skip.text = "Skip tour"
	skip.focus_mode = Control.FOCUS_NONE
	skip.pressed.connect(stop)
	row.add_child(skip)
	_next = Button.new()
	_next.focus_mode = Control.FOCUS_NONE
	_next.pressed.connect(func(): _show(_i + 1))
	row.add_child(_next)
	_apply_palette()
	Palette.changed.connect(_apply_palette)
	get_viewport().size_changed.connect(_place)


func _apply_palette() -> void:
	var sb := Palette.panel_box("panel_alt", 8, 14)
	sb.border_color = Palette.c("changed")
	sb.set_border_width_all(2)
	sb.shadow_color = Color(0, 0, 0, 0.35)
	sb.shadow_size = 8
	_bubble.add_theme_stylebox_override("panel", sb)
	_count.add_theme_color_override("font_color", Palette.c("dim"))
	if visible:
		_show(_i)


## Start the tour. `steps` is [[control, title, text], …]; controls that are
## hidden at the current level are left out.
func start(steps: Array) -> void:
	_steps = steps.filter(func(s): return s[0] is Control and s[0].is_visible_in_tree())
	if _steps.is_empty():
		return
	visible = true
	_show(0)


func stop() -> void:
	if not visible:
		return
	visible = false
	spotlight.release()
	finished.emit()


func _show(i: int) -> void:
	if i >= _steps.size():
		stop()
		return
	_i = i
	var s: Array = _steps[i]
	_title.text = s[1]
	_text.text = s[2].replace("{yellow}", Palette.hex("changed"))
	_count.text = "%d of %d" % [i + 1, _steps.size()]
	_next.text = "Done" if i == _steps.size() - 1 else "Next ▶"
	spotlight.hold([s[0]])
	_place.call_deferred()


## Put the bubble beside its control: below it if there is room, else above,
## else to its left; always inside the window.
func _place() -> void:
	if not visible or _steps.is_empty():
		return
	var target: Control = _steps[_i][0]
	var r := target.get_global_rect()
	r.position -= get_global_rect().position
	var win := get_viewport_rect().size - get_global_rect().position
	_bubble.reset_size()
	var b := _bubble.get_combined_minimum_size()
	var p := Vector2(r.position.x, r.end.y + GAP)
	if p.y + b.y > win.y:
		p.y = r.position.y - GAP - b.y
	if p.y < 0:
		# a tall panel: put the bubble beside it, near its top
		p = Vector2(r.end.x + GAP, r.position.y + 40)
		if p.x + b.x > win.x:
			p.x = r.position.x - GAP - b.x
	p.x = clampf(p.x, 8, maxf(8, win.x - b.x - 8))
	p.y = clampf(p.y, 8, maxf(8, win.y - b.y - 8))
	_bubble.position = p


func _unhandled_key_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if visible and k and k.pressed and not k.echo and k.keycode == KEY_ESCAPE:
		stop()
		get_viewport().set_input_as_handled()
