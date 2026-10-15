extends Control
## Draws attention to controls that just appeared: after the student raises
## the Level, main.gd calls show_new() with the new buttons and panels, and
## each gets a pulsing outline and a small NEW tag for a few seconds, then
## fades out.  It sits on top of the whole window and never takes a click.

const DURATION := 7.0     # seconds the outlines stay
const FADE := 1.5         # the last seconds fade out
const PULSE_HZ := 1.1

var _targets: Array = []  # Controls
var _t := 0.0
var _font: Font


func _ready() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_font = get_theme_default_font()
	set_process(false)


## Outline `controls` for a few seconds (the ones not on screen are skipped).
func show_new(controls: Array) -> void:
	_targets = controls.filter(func(c): return c is Control and is_instance_valid(c))
	_t = 0.0
	set_process(not _targets.is_empty())
	queue_redraw()


func _process(delta: float) -> void:
	_t += delta
	if _t >= DURATION:
		_targets.clear()
		set_process(false)
	queue_redraw()


func _draw() -> void:
	var fade := clampf((DURATION - _t) / FADE, 0.0, 1.0)
	var pulse := 0.6 + 0.4 * sin(_t * TAU * PULSE_HZ)
	var col := Palette.c("changed")
	var ink := Palette.c("bg")
	for c in _targets:
		var ctl := c as Control
		if not is_instance_valid(ctl) or not ctl.is_visible_in_tree():
			continue
		var r := ctl.get_global_rect()
		r.position -= get_global_rect().position
		r = r.grow(3)
		var box := StyleBoxFlat.new()
		box.draw_center = true
		box.bg_color = Color(col, 0.10 * pulse * fade)
		box.border_color = Color(col, (0.55 + 0.45 * pulse) * fade)
		box.set_border_width_all(3)
		box.set_corner_radius_all(6)
		draw_style_box(box, r)
		# a NEW tag on the top-right corner, kept inside the window
		var fs := 11
		var w := _font.get_string_size("NEW", HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x + 12
		var tag := Rect2(r.end.x - w - 4, r.position.y - 9, w, 18)
		tag.position.x = clampf(tag.position.x, 0.0, maxf(0.0, get_viewport_rect().size.x - get_global_rect().position.x - w))
		tag.position.y = maxf(0.0, tag.position.y)
		var pill := StyleBoxFlat.new()
		pill.bg_color = Color(col, fade)
		pill.set_corner_radius_all(9)
		draw_style_box(pill, tag)
		draw_string(_font, tag.position + Vector2(6, 13), "NEW", HORIZONTAL_ALIGNMENT_LEFT, -1, fs, Color(ink, fade))
