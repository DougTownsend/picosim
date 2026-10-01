extends Node
## Colour schemes for the whole UI (autoload "Palette").
##
##   blue  — the original blue-grey look, on Godot's default control theme
##   dark  — neutral greys, near-black backgrounds
##   light — soft mid-grey panels, dark text, deep accents for contrast
##
## Panels read colours with c("key") (or hex("key") for BBCode) and listen
## for `changed` to restyle.  Dark and light also replace Godot's built-in
## control theme (buttons, lists, tabs, …), applied to the root window.
## The choice is saved in user://settings.cfg; `--theme=NAME` overrides it.
## The default, "system", follows the OS appearance (light → light,
## dark → dark) and switches live when the OS does.

signal changed

const SETTINGS := "user://settings.cfg"
const ORDER := ["system", "blue", "dark", "light"]
const TITLES := {"system": "System", "blue": "Blue", "dark": "Dark", "light": "Light"}

const SCHEMES := {
	"blue": {
		"bg": "1b1e24", "panel": "22262e", "panel_alt": "2c313a", "surface": "15171c", "card": "1e2229",
		"box": "262b34", "box_active": "2d3a52", "edge": "444c59", "wire": "3a414d", "bus": "4b5363",
		"text": "d7dae0", "dim": "7f8794", "muted": "9da5b4",
		"accent": "61afef", "purple": "c678dd", "yellow": "e5c07b", "cyan": "56b6c2", "red": "e06c75",
		"green": "98c379", "changed": "f2cc60", "sel": "ffb86c", "error": "ff7b72", "serial": "9ece6a",
		"label": "7fd1b9", "bp": "ff6b6b", "focus": "4a78c2", "sp_bg": "3a4f7a", "pc_bg": "5a4a20",
		"pc_row": "f2cc6038", "sig_bg": "2f3845", "glow": "bfe6ff", "glow_ctrl": "f0c8ff",
		"glow_head": "ffffff", "shadow": "00000080", "gnd": "6b7280",
	},
	"dark": {
		"bg": "141414", "panel": "1d1d1d", "panel_alt": "2a2a2a", "surface": "0e0e0e", "card": "202020",
		"box": "232323", "box_active": "1f3048", "edge": "474747", "wire": "383838", "bus": "4a4a4a",
		"text": "e6e6e6", "dim": "8c8c8c", "muted": "ababab",
		"accent": "61afef", "purple": "c678dd", "yellow": "e5c07b", "cyan": "56b6c2", "red": "e06c75",
		"green": "98c379", "changed": "f2cc60", "sel": "ffb86c", "error": "ff7b72", "serial": "9ece6a",
		"label": "7fd1b9", "bp": "ff6b6b", "focus": "4a78c2", "sp_bg": "2f4468", "pc_bg": "54441a",
		"pc_row": "f2cc6033", "sig_bg": "333333", "glow": "bfe6ff", "glow_ctrl": "f0c8ff",
		"glow_head": "ffffff", "shadow": "00000099", "gnd": "737373",
		# control theme
		"btn": "2b2b2b", "btn_hover": "353535", "btn_pressed": "1f3048", "input": "121212",
		"hover": "2a2a2a", "select": "1f3048", "scroll": "4a4a4a",
	},
	"light": {
		"bg": "c4c8cd", "panel": "d3d6da", "panel_alt": "c6cacf", "surface": "dadde0", "card": "d6d9dc",
		"box": "cdd1d5", "box_active": "bccde3", "edge": "8f969f", "wire": "a3a9b1", "bus": "818994",
		"text": "1b1e22", "dim": "4d535b", "muted": "3c4249", "gnd": "4d535b",
		"accent": "0b5cb5", "purple": "6f42c1", "yellow": "7a5100", "cyan": "156a70", "red": "b81f29",
		"green": "17702f", "changed": "8f5400", "sel": "e36209", "error": "cf222e", "serial": "17702f",
		"label": "0d6660", "bp": "d1242f", "focus": "0b5cb5", "sp_bg": "aec5e6", "pc_bg": "dcc787",
		"pc_row": "f2b8244a", "sig_bg": "c5cad1", "glow": "0a3d91", "glow_ctrl": "4c2a99",
		"glow_head": "0a3069", "shadow": "0000002e",
		# control theme
		"btn": "cbcfd3", "btn_hover": "c0c4c9", "btn_pressed": "b9cce4", "input": "dcdfe2",
		"hover": "c9cdd1", "select": "b9cce4", "scroll": "9aa1aa",
	},
}

var preference := "system"    # what the user picked: "system" or a scheme
var current := "blue"          # the scheme in use
var ui_theme: Theme = null    # null in "blue": Godot's default control theme


func _ready() -> void:
	var cfg := ConfigFile.new()
	if cfg.load(SETTINGS) == OK:
		preference = str(cfg.get_value("ui", "theme", preference))
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--theme="):
			preference = arg.substr(8)
	if preference.begins_with("light"):
		preference = "light"   # the trial light_v1…v4 schemes were merged into "light"
	if preference != "system" and not SCHEMES.has(preference):
		preference = "system"
	current = _resolve()
	_apply()
	if DisplayServer.has_method("set_system_theme_change_callback"):
		DisplayServer.set_system_theme_change_callback(_on_system_theme_changed)


## The scheme for the current preference and OS appearance.
func _resolve() -> String:
	if preference != "system":
		return preference
	if DisplayServer.is_dark_mode_supported() and not DisplayServer.is_dark_mode():
		return "light"
	return "dark"


func _on_system_theme_changed() -> void:
	_switch_to(_resolve())


func c(key: String) -> Color:
	var scheme: Dictionary = SCHEMES[current]
	return Color(scheme[key] if scheme.has(key) else SCHEMES["blue"][key])


## "#rrggbb" for BBCode [color] / [bgcolor] tags.
func hex(key: String) -> String:
	return "#" + c(key).to_html(c(key).a < 1.0)


func is_light() -> bool:
	return current == "light"


## Pick a scheme by name, or "system" to follow the OS; the choice is saved.
func set_scheme(name: String) -> void:
	if name != "system" and not SCHEMES.has(name):
		return
	preference = name
	var cfg := ConfigFile.new()
	cfg.load(SETTINGS)
	cfg.set_value("ui", "theme", name)
	cfg.save(SETTINGS)
	_switch_to(_resolve())


func _switch_to(name: String) -> void:
	if name == current:
		return
	current = name
	_apply()
	changed.emit()


func _apply() -> void:
	ui_theme = null if current == "blue" else _build_theme()
	get_tree().root.theme = ui_theme


## A rounded panel stylebox in a palette colour.
func panel_box(key: String, radius := 6, margin := 8) -> StyleBoxFlat:
	var sb := _flat(c(key), radius)
	sb.set_content_margin_all(margin)
	return sb


# ── control theme for dark / light ──────────────────────────────────────────

func _flat(bg: Color, radius := 4, border := Color.TRANSPARENT, bw := 0) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	sb.set_corner_radius_all(radius)
	if bw > 0:
		sb.border_color = border
		sb.set_border_width_all(bw)
	return sb


func _pad(sb: StyleBox, h := 8, v := 4) -> StyleBox:
	sb.content_margin_left = h
	sb.content_margin_right = h
	sb.content_margin_top = v
	sb.content_margin_bottom = v
	return sb


## An icon drawn from SVG at 3× and shown at its nominal size, so it stays
## sharp when the UI is zoomed.
func _svg(svg: String) -> ImageTexture:
	var img := Image.new()
	img.load_svg_from_string(svg, 3.0)
	var tex := ImageTexture.create_from_image(img)
	tex.set_size_override(img.get_size() / 3)
	return tex


func _check_icon(on: bool, fill: Color, stroke: Color, mark: Color) -> ImageTexture:
	var box := "<rect x='1.5' y='1.5' width='13' height='13' rx='3' fill='#%s' stroke='#%s' stroke-width='1.2'/>" % [
		fill.to_html(false), stroke.to_html(false)]
	var tick := "<path d='M4.5 8.2l2.4 2.4 4.8-5' fill='none' stroke='#%s' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/>" % mark.to_html(false) if on else ""
	return _svg("<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16'>%s%s</svg>" % [box, tick])


func _dot_icon(size: int, fill: Color, stroke: Color) -> ImageTexture:
	var r := size / 2.0
	return _svg("<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d'><circle cx='%s' cy='%s' r='%s' fill='#%s' stroke='#%s' stroke-width='1'/></svg>" % [
		size, size, r, r, r - 1.0, fill.to_html(false), stroke.to_html(false)])


func _build_theme() -> Theme:
	var t := Theme.new()
	var text := c("text")
	var dim := c("dim")
	var accent := c("accent")
	var edge := c("edge")
	var empty := StyleBoxEmpty.new()
	var disabled_text := dim.lerp(c("panel"), 0.35)

	# buttons (Button, OptionButton, the zoom/step buttons, …)
	for type in ["Button", "OptionButton", "MenuButton"]:
		t.set_stylebox("normal", type, _pad(_flat(c("btn"), 4, edge, 1)))
		t.set_stylebox("hover", type, _pad(_flat(c("btn_hover"), 4, edge, 1)))
		t.set_stylebox("pressed", type, _pad(_flat(c("btn_pressed"), 4, accent, 1)))
		t.set_stylebox("hover_pressed", type, _pad(_flat(c("btn_pressed"), 4, accent, 1)))
		t.set_stylebox("disabled", type, _pad(_flat(c("btn").lerp(c("panel"), 0.5), 4, edge.lerp(c("panel"), 0.5), 1)))
		t.set_stylebox("focus", type, empty)
		for k in ["font_color", "font_hover_color", "font_focus_color", "icon_normal_color", "icon_hover_color"]:
			t.set_color(k, type, text)
		for k in ["font_pressed_color", "font_hover_pressed_color", "icon_pressed_color", "icon_hover_pressed_color"]:
			t.set_color(k, type, accent if is_light() else text)
		t.set_color("font_disabled_color", type, disabled_text)
		t.set_color("icon_disabled_color", type, disabled_text)
	t.set_constant("modulate_arrow", "OptionButton", 1)

	# check boxes
	var off := _check_icon(false, c("input"), edge, text)
	var on := _check_icon(true, accent, accent, Color.WHITE)
	t.set_icon("unchecked", "CheckBox", off)
	t.set_icon("checked", "CheckBox", on)
	t.set_icon("unchecked_disabled", "CheckBox", off)
	t.set_icon("checked_disabled", "CheckBox", on)
	for s in ["normal", "pressed", "hover", "hover_pressed", "disabled", "focus"]:
		t.set_stylebox(s, "CheckBox", _pad(StyleBoxEmpty.new(), 4, 2))
	for k in ["font_color", "font_hover_color", "font_pressed_color", "font_hover_pressed_color", "font_focus_color"]:
		t.set_color(k, "CheckBox", text)
	t.set_color("font_disabled_color", "CheckBox", disabled_text)

	# text
	t.set_color("font_color", "Label", text)
	t.set_color("default_color", "RichTextLabel", text)
	t.set_color("selection_color", "RichTextLabel", c("select"))
	t.set_stylebox("normal", "RichTextLabel", empty)
	t.set_stylebox("focus", "RichTextLabel", empty)
	t.set_stylebox("normal", "LineEdit", _pad(_flat(c("input"), 4, edge, 1), 6, 4))
	t.set_stylebox("focus", "LineEdit", _pad(_flat(c("input"), 4, accent, 1), 6, 4))
	t.set_stylebox("read_only", "LineEdit", _pad(_flat(c("panel_alt"), 4, edge, 1), 6, 4))
	t.set_color("font_color", "LineEdit", text)
	t.set_color("font_selected_color", "LineEdit", text)
	t.set_color("font_placeholder_color", "LineEdit", dim)
	t.set_color("font_uneditable_color", "LineEdit", dim)
	t.set_color("caret_color", "LineEdit", text)
	t.set_color("selection_color", "LineEdit", c("select"))
	t.set_color("clear_button_color", "LineEdit", dim)

	# lists
	var sel := _flat(c("select"), 3)
	var hov := _flat(c("hover"), 3)
	for type in ["ItemList", "Tree"]:
		t.set_stylebox("panel", type, _pad(_flat(c("surface"), 4, edge, 1), 4, 4))
		t.set_stylebox("focus", type, empty)
		t.set_stylebox("selected", type, sel)
		t.set_stylebox("selected_focus", type, sel)
		t.set_stylebox("hovered", type, hov)
		t.set_stylebox("cursor", type, empty)
		t.set_stylebox("cursor_unfocused", type, empty)
		t.set_color("font_color", type, text)
		t.set_color("font_selected_color", type, text)
		t.set_color("font_hovered_color", type, text)
		t.set_color("guide_color", type, Color(edge, 0.3))
	t.set_stylebox("hovered_selected", "ItemList", sel)
	t.set_stylebox("hovered_selected_focus", "ItemList", sel)
	for s in ["title_button_normal", "title_button_hover", "title_button_pressed"]:
		t.set_stylebox(s, "Tree", _pad(_flat(c("panel_alt"), 0), 4, 2))
	t.set_color("title_button_color", "Tree", dim)
	t.set_color("relationship_line_color", "Tree", edge)

	# tabs
	t.set_stylebox("panel", "TabContainer", _pad(_flat(c("panel"), 4, edge, 1), 6, 6))
	t.set_stylebox("tab_selected", "TabContainer", _pad(_flat(c("panel"), 4, edge, 1), 12, 5))
	t.set_stylebox("tab_unselected", "TabContainer", _pad(_flat(c("panel_alt"), 4), 12, 5))
	t.set_stylebox("tab_hovered", "TabContainer", _pad(_flat(c("hover"), 4), 12, 5))
	t.set_stylebox("tab_focus", "TabContainer", empty)
	t.set_stylebox("tabbar_background", "TabContainer", empty)
	t.set_color("font_selected_color", "TabContainer", text)
	t.set_color("font_hovered_color", "TabContainer", text)
	t.set_color("font_unselected_color", "TabContainer", dim)

	# popups, tooltips, dialogs
	var pop := _pad(_flat(c("card"), 4, edge, 1), 4, 4)
	pop.shadow_color = c("shadow")
	pop.shadow_size = 6
	t.set_stylebox("panel", "PopupMenu", pop)
	t.set_stylebox("hover", "PopupMenu", _flat(c("select"), 3))
	t.set_color("font_color", "PopupMenu", text)
	t.set_color("font_hover_color", "PopupMenu", text)
	t.set_color("font_disabled_color", "PopupMenu", disabled_text)
	t.set_color("font_separator_color", "PopupMenu", dim)
	t.set_stylebox("panel", "TooltipPanel", _pad(pop.duplicate(), 8, 5))
	t.set_color("font_color", "TooltipLabel", text)
	t.set_stylebox("panel", "AcceptDialog", _pad(_flat(c("panel"), 0), 10, 10))
	var border := _flat(c("panel_alt"), 6, edge, 1)
	border.expand_margin_top = 28
	border.expand_margin_left = 4
	border.expand_margin_right = 4
	border.expand_margin_bottom = 4
	border.shadow_color = c("shadow")
	border.shadow_size = 10
	t.set_stylebox("embedded_border", "Window", border)
	t.set_stylebox("embedded_unfocused_border", "Window", border)
	t.set_color("title_color", "Window", text)

	# slider, scroll bars, separators
	t.set_stylebox("slider", "HSlider", _pad(_flat(c("panel_alt"), 3, edge, 1), 0, 2))
	t.set_stylebox("grabber_area", "HSlider", _pad(_flat(accent, 3), 0, 2))
	t.set_stylebox("grabber_area_highlight", "HSlider", _pad(_flat(accent, 3), 0, 2))
	t.set_icon("grabber", "HSlider", _dot_icon(16, c("input"), accent))
	t.set_icon("grabber_highlight", "HSlider", _dot_icon(16, c("btn_hover"), accent))
	for type in ["VScrollBar", "HScrollBar"]:
		t.set_stylebox("scroll", type, _pad(_flat(Color(0, 0, 0, 0), 4), 4, 4))
		t.set_stylebox("scroll_focus", type, _pad(_flat(Color(0, 0, 0, 0), 4), 4, 4))
		t.set_stylebox("grabber", type, _pad(_flat(c("scroll"), 4), 4, 4))
		t.set_stylebox("grabber_highlight", type, _pad(_flat(c("scroll").lerp(text, 0.25), 4), 4, 4))
		t.set_stylebox("grabber_pressed", type, _pad(_flat(accent, 4), 4, 4))
	var line := StyleBoxLine.new()
	line.color = edge
	line.vertical = true
	t.set_stylebox("separator", "VSeparator", line)
	var hline := StyleBoxLine.new()
	hline.color = edge
	t.set_stylebox("separator", "HSeparator", hline)
	_match_default_metrics(t)
	return t


## Give every stylebox the same margins as Godot's default theme (used by
## "blue"), so switching schemes changes colours only, never control sizes
## or the layout.
func _match_default_metrics(t: Theme) -> void:
	var d := ThemeDB.get_default_theme()
	for type in t.get_stylebox_type_list():
		for n in t.get_stylebox_list(type):
			var ref: StyleBox = d.get_stylebox(n, type)
			if ref == null:
				continue
			# styleboxes are shared between items, so each gets its own copy
			var sb: StyleBox = t.get_stylebox(n, type).duplicate()
			for side in [SIDE_LEFT, SIDE_TOP, SIDE_RIGHT, SIDE_BOTTOM]:
				sb.set_content_margin(side, ref.get_margin(side))
			if sb is StyleBoxFlat and ref is StyleBoxFlat:
				for side in [SIDE_LEFT, SIDE_TOP, SIDE_RIGHT, SIDE_BOTTOM]:
					sb.set_expand_margin(side, ref.get_expand_margin(side))
			t.set_stylebox(n, type, sb)
