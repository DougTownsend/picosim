extends PanelContainer
## "Explain this step": a fold-out panel at the top of the right-hand column
## that says, after every Step, what the processor just did and what the
## next step will do: where the program goes next and why.
##
## The words come from the backend (picosim/explain.py) with every state, and
## are built only when the student steps: while Run is on the panel just says
## so and is not rebuilt.  Each explanation has a short, plain version and a
## list of details (exactly what changed, how the instruction works, its clock
## cycles).  "More detail" shows the details; a detail meant for a higher
## Level (clock cycles from Level 3, the pipeline from Level 4) is left out
## below it.  Open / closed and More detail are remembered in settings.cfg.

signal toggled_open(open: bool)

const SETTINGS := "user://settings.cfg"
const OPEN_HEIGHT := 190          # the panel's height when open (drag the bar for more)
const DETAIL_HEIGHT := 300        # … and with More detail on
const PipelineView := preload("res://scripts/pipeline_view.gd")

var is_open := true
var detail := false
var head_btn: Button
var hint: Label
var detail_box: CheckBox
var body: RichTextLabel
var _key = null                   # what the body was last built from
var _st: Dictionary = {}


func _ready() -> void:
	var cfg := ConfigFile.new()
	if cfg.load(SETTINGS) == OK:
		is_open = bool(cfg.get_value("ui", "explain_open", true))
		detail = bool(cfg.get_value("ui", "explain_detail", false))
	if OS.get_cmdline_user_args().has("--explain-detail"):   # screenshots
		detail = true
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 4)
	add_child(v)
	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 10)
	v.add_child(top)
	head_btn = Button.new()
	head_btn.flat = true
	head_btn.focus_mode = Control.FOCUS_NONE
	head_btn.tooltip_text = "Show or hide the explanation of each step"
	head_btn.add_theme_font_size_override("font_size", 15)
	head_btn.pressed.connect(func(): set_open(not is_open))
	top.add_child(head_btn)
	hint = Label.new()
	hint.text = "what the last step did, and what happens next"
	hint.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hint.clip_text = true
	hint.add_theme_font_size_override("font_size", 12)
	hint.mouse_filter = Control.MOUSE_FILTER_PASS
	top.add_child(hint)
	detail_box = CheckBox.new()
	detail_box.text = "More detail"
	detail_box.focus_mode = Control.FOCUS_NONE
	detail_box.button_pressed = detail
	detail_box.tooltip_text = "Also show exactly what changed, how the instruction works and, from Level 3, its clock cycles"
	detail_box.add_theme_font_size_override("font_size", 13)
	detail_box.toggled.connect(_on_detail)
	top.add_child(detail_box)
	body = RichTextLabel.new()
	body.bbcode_enabled = true
	body.scroll_active = true
	body.selection_enabled = true
	body.fit_content = false
	body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_theme_font_override("mono_font", Backend.mono_font())
	body.add_theme_font_size_override("normal_font_size", 14)
	body.add_theme_font_size_override("bold_font_size", 14)
	body.add_theme_font_size_override("mono_font_size", 13)
	v.add_child(body)
	Level.changed.connect(func(): _key = null; update_state(Backend.state))
	Palette.changed.connect(_apply_palette)
	_apply_palette()
	set_open(is_open, false)


func _apply_palette() -> void:
	add_theme_stylebox_override("panel", Palette.panel_box("panel", 6, 8))
	head_btn.add_theme_color_override("font_color", Palette.c("accent"))
	head_btn.add_theme_color_override("font_hover_color", Palette.c("accent"))
	hint.add_theme_color_override("font_color", Palette.c("dim"))
	_key = null
	update_state(Backend.state)


## Fold the panel open or shut (main.gd gives it room in the split).
func set_open(on: bool, save := true) -> void:
	is_open = on
	head_btn.text = ("▾  " if on else "▸  ") + "Explain this step"
	body.visible = on
	detail_box.visible = on
	custom_minimum_size.y = (DETAIL_HEIGHT if detail else OPEN_HEIGHT) if on else 0
	if save:
		_save("explain_open", on)
	toggled_open.emit(on)
	if on:
		_key = null
		update_state(Backend.state)


func _on_detail(on: bool) -> void:
	detail = on
	custom_minimum_size.y = DETAIL_HEIGHT if on else OPEN_HEIGHT
	_save("explain_detail", on)
	_key = null
	update_state(Backend.state)


func _save(key: String, value: Variant) -> void:
	var cfg := ConfigFile.new()
	cfg.load(SETTINGS)
	cfg.set_value("ui", key, value)
	cfg.save(SETTINGS)


func update_state(st: Dictionary) -> void:
	_st = st
	if not is_open or not st.get("loaded", false):
		return   # rebuilt when opened
	var e = st.get("explain")
	if st.get("mode", "sim") == "pico":
		e = {"kind": "pico"}
	if not (e is Dictionary):
		return
	var key := [e.get("kind"), e.get("id"), detail, Level.current]
	if key == _key:
		return   # the same explanation: states arrive often while a Step over runs
	_key = key
	body.text = _build(e)
	body.scroll_to_line(0)


## The explanation as BBCode.
func _build(e: Dictionary) -> String:
	var dim := Palette.hex("dim")
	match str(e.get("kind")):
		"running":
			return "[color=%s]The program is running. Explanations pause while Run is on: press [b]Pause[/b] (or a Step button) and this panel explains where it stopped and what comes next.[/color]" % dim
		"pico":
			return "[color=%s]Explanations work in [b]Simulator[/b] mode, where you can step through the program. Switch the mode menu back to Simulator to use them.[/color]" % dim
	var t := ""
	var now = e.get("now")
	if now is Dictionary:
		t += _section("WHAT JUST HAPPENED", now)
		if now.has("pipe_stages") and _st.get("pipe") is Dictionary:
			var stx: Dictionary = PipelineView.stage_text(_st["pipe"])
			t += "• [color=%s][b]Decode + Execute[/b][/color]: %s\n• [color=%s][b]Fetch[/b][/color]: %s\n" % [
				Palette.hex("stage_e"), stx["E"], Palette.hex("stage_f"), stx["F"]]
		if now.has("fault"):
			t += "[color=%s]%s[/color]\n" % [Palette.hex("error"), _esc(str(now["fault"]))]
	var nxt = e.get("next")
	if nxt is Dictionary:
		t += ("\n" if t != "" else "") + _section("WHAT HAPPENS NEXT", nxt)
	return t


## One section: its heading, the instruction, the plain words, then (with
## More detail) the details up to the current level.
func _section(title: String, s: Dictionary) -> String:
	var t := "[color=%s][b]%s[/b][/color]" % [Palette.hex("accent"), title]
	var head := str(s.get("head", ""))
	if head != "":
		t += "   [color=%s][code]%s[/code][/color]" % [Palette.hex("cyan"), _esc(head)]
	t += "\n" + _esc(str(s.get("simple", ""))) + "\n"
	var goes := str(s.get("goes", ""))
	if goes != "" and s.get("goes") != null:
		t += "[b]Then:[/b] " + _esc(goes) + "\n"
	var why = s.get("why")
	if why != null and str(why) != "":
		t += "[b]Why:[/b] " + _esc(str(why)) + "\n"
	if detail:
		var lines := ""
		for d in s.get("detail", []):
			if int(d[0]) <= Level.current:
				var line := str(d[1])
				if line.begins_with("  • "):
					lines += "      • " + _esc(line.substr(4)) + "\n"
				else:
					lines += "  " + _esc(line) + "\n"
		if lines != "":
			t += "[color=%s]%s[/color]" % [Palette.hex("muted"), lines]
	return t


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")
