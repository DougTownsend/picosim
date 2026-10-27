extends Button
## A small ⓘ-style "?" that sits beside a panel's title and opens the guide at
## the page about that panel, so nobody has to hunt through the guide's list.
##
## It reaches the guide through the "guide" group (guide.gd joins it), so if
## guide.gd is deleted these buttons hide themselves and nothing else changes.
## `topic` may be changed later (the diagram area shows the CPU or the
## pipeline page depending on the machine).

var topic := ""


func _init(t: String, about: String) -> void:
	topic = t
	text = "?"
	flat = true
	focus_mode = Control.FOCUS_NONE
	tooltip_text = "Open the guide: " + about
	custom_minimum_size = Vector2(22, 22)
	size_flags_vertical = Control.SIZE_SHRINK_CENTER
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	add_theme_font_size_override("font_size", 13)
	pressed.connect(func(): get_tree().call_group("guide", "open", topic))
	mouse_entered.connect(queue_redraw)
	mouse_exited.connect(queue_redraw)


func _ready() -> void:
	_apply_palette()
	Palette.changed.connect(_apply_palette)
	(func(): if get_tree().get_first_node_in_group("guide") == null: hide()).call_deferred()


func _apply_palette() -> void:
	for item in ["font_color", "font_focus_color"]:
		add_theme_color_override(item, Palette.c("dim"))
	for item in ["font_hover_color", "font_pressed_color", "font_hover_pressed_color"]:
		add_theme_color_override(item, Palette.c("accent"))
	queue_redraw()


## A thin ring around the "?", brighter under the mouse.
func _draw() -> void:
	var r := minf(size.x, size.y) / 2.0 - 2.0
	var col := Palette.c("accent") if is_hovered() else Palette.c("edge")
	draw_arc(size / 2.0, r, 0.0, TAU, 32, col, 1.0, true)
