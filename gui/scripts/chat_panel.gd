extends Node
## Ask picosim: the help chatbot's window.  Opened by the toolbar's Ask
## button or the ? key; the answers come from the backend (picosim/chat/),
## which knows the guide (main.gd sends it the knowledge pack) and the
## program that is running.
##
## Self-contained like the guide: main.gd only creates it if this file
## exists, so deleting chat_panel.gd removes the Ask button and nothing else.
##
## Each answer is a bubble with a small tag saying where it came from
## (built-in, from the guide, or AI), its guide links, "Show me" buttons that
## point at a control (main.gd's run_ask_action), 👍 / 👎 and Report.  Chips
## under the conversation suggest questions that fit what is on screen (a
## fault, a failed challenge, getchar waiting).  The bot gives hints, never a
## finished solution; the window says so the first time it opens.
##
## Reply text uses a small markup: **bold**, `code`, *italic*, lines that
## start with "• ", and [guide:id|label] for a link into the guide.

const MAX_CHARS := 1000
const TEXT_SCALE_MAX := 2.5
const TAGS := {"built-in": "built-in answer", "guide": "from the guide", "ai": "AI answer — check the guide if unsure"}

var host: Node                    # main.gd: ask_context, run_ask_action, guide
var win: Window
var bg: PanelContainer
var scroll: ScrollContainer
var log_box: VBoxContainer
var chips: HFlowContainer
var input: LineEdit
var send_btn: Button
var counter: Label
var status_chip: Label
var notice: Label
var _next_id := 1
var _bubbles := {}                # question id → {panel, body, tag, extra, raw}
var _ai := "off"                  # chat_status: on, off or unavailable
var _ai_reason := ""
var _ctx_key := ""                # what the context chips were last asked for
var _scaled: Array = []           # [control, theme item, size at text scale 1]
var text_scale := 1.0
var _open_size := Vector2i.ZERO
var _demo_ask := ""               # --chat-ask "…" (screenshots)
var _knowledge := false           # the backend has the guide (chat_status came)


func _ready() -> void:
	win = Window.new()
	win.title = "Ask picosim"
	win.visible = false
	win.transient = true
	win.min_size = Vector2i(420, 360)
	win.close_requested.connect(win.hide)
	win.window_input.connect(_on_window_input)
	win.size_changed.connect(_on_win_resized)
	add_child(win)

	bg = PanelContainer.new()
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	win.add_child(bg)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	bg.add_child(v)

	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 8)
	v.add_child(head)
	var tagline := Label.new()
	tagline.text = "Hints, not answers · it can make mistakes"
	tagline.name = "Tagline"
	_scale(tagline, "font_size", 13)
	tagline.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(tagline)
	status_chip = Label.new()
	status_chip.visible = false
	status_chip.mouse_filter = Control.MOUSE_FILTER_PASS
	_scale(status_chip, "font_size", 13)
	head.add_child(status_chip)
	var clear := Button.new()
	clear.text = "Clear"
	clear.tooltip_text = "Start a new conversation"
	clear.focus_mode = Control.FOCUS_NONE
	clear.pressed.connect(_clear)
	head.add_child(clear)

	notice = Label.new()
	notice.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	notice.visible = false
	_scale(notice, "font_size", 13)
	v.add_child(notice)

	scroll = ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	v.add_child(scroll)
	log_box = VBoxContainer.new()
	log_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	log_box.add_theme_constant_override("separation", 10)
	scroll.add_child(log_box)

	chips = HFlowContainer.new()
	chips.add_theme_constant_override("h_separation", 6)
	chips.add_theme_constant_override("v_separation", 6)
	v.add_child(chips)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	v.add_child(row)
	input = LineEdit.new()
	input.placeholder_text = "Ask about your program, a button, or an instruction…"
	input.max_length = MAX_CHARS
	input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	input.text_changed.connect(_on_typing)
	input.text_submitted.connect(func(t): _ask(t))
	_scale(input, "font_size", 15)
	row.add_child(input)
	counter = Label.new()
	_scale(counter, "font_size", 12)
	row.add_child(counter)
	send_btn = Button.new()
	send_btn.text = "Send"
	send_btn.disabled = true
	send_btn.focus_mode = Control.FOCUS_NONE
	send_btn.pressed.connect(func(): _ask(input.text))
	row.add_child(send_btn)

	Backend.chat_event.connect(_on_chat)
	Backend.state_changed.connect(_on_state)
	Palette.changed.connect(_apply_palette)
	_apply_palette()
	var args := OS.get_cmdline_user_args()
	var i := args.find("--chat-ask")
	if i >= 0 and i + 1 < args.size():
		_demo_ask = args[i + 1]
	if args.has("--chat") or _demo_ask != "":
		open.call_deferred()


## Show the window (it keeps the conversation while picosim runs).
func open() -> void:
	var area := get_viewport().get_visible_rect().size
	_open_size = Vector2i.ZERO
	win.popup_centered(Vector2i(mini(int(area.x * 0.46), 720), int(area.y * 0.8)))
	_open_size = win.size
	_set_text_scale(1.0)
	if log_box.get_child_count() == 0:
		_welcome()
	_ctx_key = ""
	_on_state(Backend.state)
	input.grab_focus()


func _welcome() -> void:
	var b := _bot_bubble(-1)
	_fill(b, {"text": "Ask me what you need help with.", "source": "built-in"}, false)
	b["tag"].visible = false      # a greeting, not an answer


# ── asking ──────────────────────────────────────────────────────────────────

func _on_typing(t: String) -> void:
	send_btn.disabled = t.strip_edges() == ""
	counter.text = "%d/%d" % [t.length(), MAX_CHARS] if t.length() > MAX_CHARS * 0.8 else ""


func _ask(text: String, extra := {}) -> void:
	text = text.strip_edges()
	if text == "" and not extra.has("doc"):
		return
	input.text = ""
	_on_typing("")
	_user_bubble(text)
	var qid := _next_id
	_next_id += 1
	var b := _bot_bubble(qid)
	b["question"] = text
	if not Backend.online:
		_fill(b, {"text": "The simulator part of picosim stopped, so I can't look at your program or the guide right " +
			"now. Close picosim and start it again.", "source": "built-in",
			"links": [{"id": "trouble", "label": "Troubleshooting"}]}, false)
		return
	_fill(b, {"text": "…", "source": "built-in"}, false)
	var msg := {"id": qid, "text": text, "context": host.ask_context() if host else {}}
	msg.merge(extra)
	Backend.send("chat", msg)


func _on_chat(msg: Dictionary) -> void:
	var qid := int(msg.get("id", 0))
	match str(msg.get("type", "")):
		"chat_reply", "chat_replace":
			if _bubbles.has(qid):
				_fill(_bubbles[qid], msg, not msg.get("pending", false))
				if msg.get("pending", false):
					_bubbles[qid]["body"].text = "[color=%s]Asking the AI helper…[/color]" % Palette.hex("dim")
					_bubbles[qid]["raw"] = ""
			_set_chips(msg.get("suggestions", []))
		"chat_delta":
			if _bubbles.has(qid):
				var b: Dictionary = _bubbles[qid]
				b["raw"] = str(b["raw"]) + str(msg.get("text", ""))
				b["body"].text = to_bbcode(str(b["raw"]))
				_scroll_down()
		"chat_done":
			if _bubbles.has(qid):
				_add_feedback(_bubbles[qid], qid)
		"chat_status":
			_ai = str(msg.get("ai", "off"))
			_ai_reason = str(msg.get("reason", ""))
			_knowledge = true
			_show_status()
			_try_demo()
		"chat_suggestions":
			_set_chips(msg.get("chips", []))
		"chat_reported":
			if _bubbles.has(qid):
				var rep: Button = _bubbles[qid].get("report")
				if rep:
					rep.text = "Saved for your teacher"
					rep.disabled = true


func _try_demo() -> void:
	if _demo_ask != "" and _knowledge and Backend.state.get("loaded", false):
		var q := _demo_ask
		_demo_ask = ""
		get_tree().create_timer(2.5).timeout.connect(func(): _ask(q))   # after --demo-run / --demo-steps


## New context chips when what is on screen changes (a fault, a failed
## challenge, getchar waiting, a fresh program).
func _on_state(st: Dictionary) -> void:
	_try_demo()
	if not win.visible:
		return
	var checks = st.get("checks")
	var key := "%s|%s|%s|%s|%s|%s" % [st.get("loaded", false), st.get("path", ""), st.get("fault") != null,
		st.get("halted", false), st.get("waiting_input", false), checks != null]
	if key != _ctx_key and Backend.online:
		_ctx_key = key
		Backend.send("chat_suggest", {"context": host.ask_context() if host else {}})


func _set_chips(list: Array) -> void:
	for c in chips.get_children():
		c.queue_free()
	for s in list:
		var d: Dictionary = s if s is Dictionary else {"label": str(s), "ask": str(s)}
		var b := Button.new()
		b.text = str(d.get("label", ""))
		b.focus_mode = Control.FOCUS_NONE
		_scale(b, "font_size", 13)
		if d.has("doc"):
			b.pressed.connect(func(): _ask(str(d["label"]), {"doc": d["doc"]}))
		else:
			b.pressed.connect(func(): _ask(str(d.get("ask", d.get("label", "")))))
		chips.add_child(b)
	_style_chips()


func _clear() -> void:
	for c in log_box.get_children():
		c.queue_free()
	_bubbles.clear()
	if Backend.online:
		Backend.send("chat_reset")
	_welcome.call_deferred()
	_ctx_key = ""
	_on_state(Backend.state)


# ── bubbles ─────────────────────────────────────────────────────────────────

func _user_bubble(text: String) -> void:
	var m := MarginContainer.new()
	m.add_theme_constant_override("margin_left", 60)
	var p := PanelContainer.new()
	p.size_flags_horizontal = Control.SIZE_SHRINK_END
	p.set_meta("kind", "user")
	var l := RichTextLabel.new()
	l.bbcode_enabled = true
	l.fit_content = true
	l.scroll_active = false
	l.selection_enabled = true
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.custom_minimum_size.x = 120
	l.text = text.replace("[", "[lb]")
	_scale(l, "normal_font_size", 16)
	p.add_child(l)
	m.add_child(p)
	log_box.add_child(m)
	_style_bubble(p)
	_scroll_down()


func _bot_bubble(qid: int) -> Dictionary:
	var m := MarginContainer.new()
	m.add_theme_constant_override("margin_right", 30)
	var p := PanelContainer.new()
	p.set_meta("kind", "bot")
	m.add_child(p)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 6)
	p.add_child(v)
	var tag := Label.new()
	_scale(tag, "font_size", 11)
	v.add_child(tag)
	var body := RichTextLabel.new()
	body.bbcode_enabled = true
	body.fit_content = true
	body.scroll_active = false
	body.selection_enabled = true
	body.meta_underlined = false
	body.add_theme_font_override("mono_font", Backend.mono_font())
	body.meta_clicked.connect(_on_meta)
	_scale(body, "normal_font_size", 16)
	_scale(body, "bold_font_size", 16)
	_scale(body, "italics_font_size", 16)
	_scale(body, "mono_font_size", 15)
	v.add_child(body)
	var extra := HFlowContainer.new()
	extra.add_theme_constant_override("h_separation", 6)
	extra.add_theme_constant_override("v_separation", 4)
	v.add_child(extra)
	log_box.add_child(m)
	_style_bubble(p)
	var b := {"panel": p, "body": body, "tag": tag, "extra": extra, "raw": "", "box": v}
	if qid >= 0:
		_bubbles[qid] = b
	_scroll_down()
	return b


## Put a reply into a bubble: its text, tag, guide links and buttons.
func _fill(b: Dictionary, msg: Dictionary, final: bool) -> void:
	var src := str(msg.get("source", "built-in"))
	b["raw"] = str(msg.get("text", ""))
	b["body"].text = to_bbcode(b["raw"])
	b["tag"].text = TAGS.get(src, src)
	b["tag"].add_theme_color_override("font_color", Palette.c("yellow") if src == "ai" else Palette.c("dim"))
	for c in b["extra"].get_children():
		c.queue_free()
	var links: Array = msg.get("links", [])
	if not links.is_empty():
		var t := "[color=%s]Read more:[/color] " % Palette.hex("dim")
		var parts := PackedStringArray()
		for l in links:
			parts.append("[url=guide:%s][color=%s][u]%s[/u][/color][/url]" % [l["id"], Palette.hex("accent"), str(l["label"]).replace("[", "[lb]")])
		b["body"].text += "\n" + t + "  ·  ".join(parts)
	for a in msg.get("actions", []):
		_small(b["extra"], "▸ " + str(a.get("label", "Show me")), func(): if host: host.run_ask_action(str(a["kind"]), a.get("arg")))
	if msg.get("more", false):
		_small(b["extra"], "More detail", func(): _ask("Tell me more"))
	if msg.get("ai_offer", false) and _ai == "on":
		_small(b["extra"], "Ask the AI helper instead", func(): _ask(str(b.get("question", "")), {"force_ai": true}))
	if final and msg.has("id"):
		_add_feedback(b, int(msg["id"]))
	_scroll_down()


func _add_feedback(b: Dictionary, qid: int) -> void:
	if b.has("feedback"):
		return
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 4)
	b["box"].add_child(row)
	b["feedback"] = row
	var up := _small(row, "👍", func(): pass)
	var down := _small(row, "👎", func(): pass)
	up.tooltip_text = "This helped"
	down.tooltip_text = "This was wrong or didn't help"
	for pair in [[up, true], [down, false]]:
		var btn: Button = pair[0]
		var good: bool = pair[1]
		btn.pressed.connect(func():
			Backend.send("chat_feedback", {"id": qid, "good": good})
			up.disabled = true
			down.disabled = true
			btn.text = ("👍" if good else "👎") + " thanks")
	var rep := _small(row, "Report", func(): Backend.send("chat_report", {"id": qid, "context": host.ask_context() if host else {}}))
	rep.tooltip_text = "Save this question and answer (no personal details) for your teacher to look at"
	b["report"] = rep


func _small(parent: Control, text: String, cb: Callable) -> Button:
	var btn := Button.new()
	btn.text = text
	btn.flat = true
	btn.focus_mode = Control.FOCUS_NONE
	btn.pressed.connect(cb)
	_scale(btn, "font_size", 12)
	btn.add_theme_color_override("font_color", Palette.c("accent"))
	parent.add_child(btn)
	return btn


func _on_meta(m: Variant) -> void:
	var s := str(m)
	if s.begins_with("guide:") and host and host.guide:
		host.guide.open(s.substr(6))


func _scroll_down() -> void:
	(func():
		await get_tree().process_frame
		scroll.scroll_vertical = int(scroll.get_v_scroll_bar().max_value)).call_deferred()


## The reply markup as BBCode in the current colours.
static func to_bbcode(t: String) -> String:
	var s := t.replace("[", "[lb]")
	var code := RegEx.create_from_string("`([^`\\n]+)`")
	s = code.sub(s, "[color=%s][code]$1[/code][/color]" % Palette.hex("cyan"), true)
	var bold := RegEx.create_from_string("\\*\\*(.+?)\\*\\*")
	s = bold.sub(s, "[b]$1[/b]", true)
	var ital := RegEx.create_from_string("(?<![\\w*])\\*([^*\\n]+?)\\*(?![\\w*])")
	s = ital.sub(s, "[i]$1[/i]", true)
	var link := RegEx.create_from_string("\\[lb\\]guide:([a-z0-9_]+)(?:\\|([^\\]]+))?\\]")
	var out := ""
	var at := 0
	for m in link.search_all(s):
		out += s.substr(at, m.get_start() - at)
		var label := m.get_string(2) if m.get_string(2) != "" else m.get_string(1)
		out += "[url=guide:%s][color=%s][u]%s[/u][/color][/url]" % [m.get_string(1), Palette.hex("accent"), label]
		at = m.get_end()
	return out + s.substr(at)


# ── status, colours and text size ───────────────────────────────────────────

func _show_status() -> void:
	status_chip.visible = _ai != "off"
	status_chip.text = "AI help on" if _ai == "on" else "AI unavailable"
	status_chip.tooltip_text = _ai_reason
	status_chip.add_theme_color_override("font_color", Palette.c("green") if _ai == "on" else Palette.c("dim"))
	notice.visible = _ai == "on"
	notice.text = "AI help is on: questions the built-in answers can't handle are sent, with your program, to " + \
		"Claude, an AI made by Anthropic. Don't type personal details."
	notice.add_theme_color_override("font_color", Palette.c("yellow"))


func _apply_palette() -> void:
	win.theme = Palette.ui_theme
	bg.add_theme_stylebox_override("panel", Palette.panel_box("bg", 0, 12))
	var tl := bg.find_child("Tagline", true, false) as Label
	if tl:
		tl.add_theme_color_override("font_color", Palette.c("dim"))
	for m in log_box.get_children():
		_style_bubble(m.get_child(0))
	_style_chips()
	_show_status()
	for b in _bubbles.values():
		b["body"].text = to_bbcode(str(b["raw"]))


func _style_bubble(p: PanelContainer) -> void:
	var user := str(p.get_meta("kind", "bot")) == "user"
	var sb := Palette.panel_box("box_active" if user else "panel", 10, 12)
	if not user:
		sb.border_color = Palette.c("edge")
		sb.set_border_width_all(1)
	p.add_theme_stylebox_override("panel", sb)


func _style_chips() -> void:
	for c in chips.get_children():
		var sb := Palette.panel_box("panel_alt", 12, 10)
		sb.border_color = Palette.c("accent")
		sb.set_border_width_all(1)
		c.add_theme_stylebox_override("normal", sb)
		c.add_theme_color_override("font_color", Palette.c("text"))


func _scale(c: Control, item: String, size: int) -> void:
	_scaled.append([c, item, size])
	c.add_theme_font_size_override(item, roundi(size * text_scale))


func _on_win_resized() -> void:
	if _open_size.x <= 0 or _open_size.y <= 0:
		return
	var growth := float(win.size.x * win.size.y) / float(_open_size.x * _open_size.y)
	var s := clampf(sqrt(growth), 1.0, TEXT_SCALE_MAX)
	_set_text_scale(maxf(1.0, floorf(s / 0.05 + 0.001) * 0.05))


func _set_text_scale(s: float) -> void:
	if is_equal_approx(s, text_scale):
		return
	text_scale = s
	_scaled = _scaled.filter(func(e): return is_instance_valid(e[0]))
	for e in _scaled:
		e[0].add_theme_font_size_override(e[1], roundi(e[2] * s))


func _on_window_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k and k.pressed and not k.echo and k.keycode == KEY_ESCAPE:
		win.hide()
		win.set_input_as_handled()
