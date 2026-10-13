extends Control
## The RP2040's Cortex-M0+ 2-stage pipeline, drawn like the CPU diagram.
##
## Two stage regions (FETCH | DECODE + EXECUTE) are separated by the IF/EX
## pipeline register.  Every instruction in flight is a labelled token in
## its stage's colour-coded lane; each pipeline cycle the token in Fetch
## slides across IF/EX into Execute and the next instruction enters Fetch,
## so the instructions form a line, each one stage behind the one before.
##
## The M0+ has no branch predictor: it always fetches the next sequential
## instruction (predict not-taken).  When a taken branch resolves in
## Execute, BRANCH RESOLVE sends the target to the PC MUX and FLUSHes the
## wrong-path instruction in Fetch (✕), and the branch's last cycle refills
## the pipeline from the target.  A multi-cycle instruction (LDR, PUSH…)
## keeps Execute busy, so the instruction behind it waits in IF/EX.
##
## Cycle counts are RP2040 datasheet Table 81 (laid out by the server from
## picosim/timing.py).  The strip along the bottom is the last few cycles,
## one row per instruction, the same staircase as the Performance window.

const W := 1000.0
const H := 640.0

const COLOR_KEYS := {
	"C_BG": "surface", "C_BOX": "box", "C_BOX_ACTIVE": "box_active", "C_EDGE": "edge",
	"C_EDGE_ACTIVE": "accent", "C_WIRE": "wire", "C_WIRE_ACTIVE": "accent", "C_TEXT": "text",
	"C_DIM": "dim", "C_MUTED": "muted", "C_CHANGED": "changed", "C_CTRL": "purple",
	"C_GLOW": "glow", "C_GLOW_HEAD": "glow_head", "C_SEL": "sel", "C_FLUSH": "red",
	"C_STAGE_F": "stage_f", "C_STAGE_E": "stage_e", "C_ON_STAGE": "on_stage", "C_PANEL_ALT": "panel_alt",
}
var C_BG: Color
var C_BOX: Color
var C_BOX_ACTIVE: Color
var C_EDGE: Color
var C_EDGE_ACTIVE: Color
var C_WIRE: Color
var C_WIRE_ACTIVE: Color
var C_TEXT: Color
var C_DIM: Color
var C_MUTED: Color
var C_CHANGED: Color
var C_CTRL: Color
var C_GLOW: Color
var C_GLOW_HEAD: Color
var C_SEL: Color
var C_FLUSH: Color
var C_STAGE_F: Color
var C_STAGE_E: Color
var C_ON_STAGE: Color
var C_PANEL_ALT: Color

const ComponentInfo := preload("res://scripts/component_info.gd")

## Same timing rules as the CPU diagram's pulse (diagram_view.gd).
const ANIM_FILL := 0.85
const ANIM_STEP := 1.6
const ANIM_MIN := 0.35
const ANIM_RUN_MIN := 0.06
const TAIL := 60.0

## Token colours, by instruction: follow one instruction from stage to stage.
const TOKEN_KEYS := ["accent", "purple", "yellow", "cyan", "green", "sel"]

## Stage regions and the IF/EX register.
const REGION_F := Rect2(20, 50, 310, 360)
const REGION_E := Rect2(390, 50, 590, 360)
## Token slots (centres) and sizes.
const SLOT_F := Vector2(175, 338)
const SLOT_E := Vector2(690, 338)
const SPAWN := Vector2(175, 240)     # new instructions arrive from the fetch unit
const RETIRE := Vector2(1060, 338)   # finished instructions leave to the right
const TOKEN_W_F := 280.0
const TOKEN_W_E := 420.0
const TOKEN_H := 46.0
## Instruction strip.
const STRIP_Y := 428.0

## id: [x, y, w, h, label, shape, tooltip]
const BOXES := {
	"p_pcmux": [40, 70, 110, 30, "PC MUX", "mux",
		"Chooses the next fetch address: PC + 2 (sequential: the M0+ always predicts not-taken) or a branch target from BRANCH RESOLVE."],
	"p_pc": [40, 130, 110, 40, "PC", "reg",
		"Program Counter: the address of the instruction being fetched."],
	"p_inc": [190, 130, 50, 40, "+2", "incr",
		"PC + 2: the next sequential halfword. Fetch assumes the instruction it just fetched will not branch."],
	"p_imem": [40, 210, 270, 60, "INSTRUCTION FETCH", "box",
		"Reads the instruction at PC over the AHB-Lite bus (flash through XIP, or SRAM) into the IF/EX register."],
	"p_ifex": [345, 60, 30, 330, "IF/EX", "pipereg",
		"Pipeline register between the stages: holds the fetched instruction until Execute is free. A FLUSH empties it."],
	"p_decode": [405, 70, 130, 40, "DECODE", "box",
		"Decodes the instruction from IF/EX: which registers to read, which operation, whether it can branch."],
	"p_regfile": [405, 130, 180, 150, "REGISTER FILE", "regfile",
		"R0–R12, SP, LR. Read for operands, written back at the end of Execute."],
	"p_alu": [620, 125, 150, 50, "ALU", "alu",
		"Arithmetic and logic, plus the address calculation for loads and stores."],
	"p_flags": [808, 140, 104, 40, "N Z C V", "flags",
		"Condition flags, set by CMP, TST and instructions ending in S. A conditional branch reads them."],
	"p_resolve": [770, 70, 200, 40, "BRANCH RESOLVE", "box",
		"Decides in Execute whether a branch is taken. If it is: sends the target to the PC MUX and FLUSHes the wrong-path instruction in Fetch."],
	"p_dmem": [620, 240, 170, 45, "DATA ACCESS", "box",
		"Loads and stores: AHB-Lite (SRAM, peripherals: 2 cycles) or the single-cycle IOPORT to the SIO (1 cycle)."],
}

## [id, points, label]
## As on the CPU diagram, the muxes and the ALU take data in on their long
## side and put their result out of their short side; the incrementer is a
## sideways adder (long input side on the left).  FLUSH is a control signal
## (dashed), like the select inputs in CTRLS.
const WIRES := [
	["inc_pcmux", [[240, 150], [262, 150], [262, 48], [125, 48], [125, 70]], "PC+2"],
	["res_pcmux", [[940, 70], [940, 30], [70, 30], [70, 70]], "branch target"],
	["flush", [[800, 70], [800, 40], [360, 40], [360, 60]], "FLUSH"],
	["pcmux_pc", [[95, 100], [95, 130]], ""],
	["pc_inc", [[150, 150], [190, 150]], ""],
	["pc_imem", [[95, 170], [95, 210]], ""],
	["imem_ifex", [[310, 240], [345, 240]], ""],
	["ifex_dec", [[375, 90], [405, 90]], ""],
	["dec_rf", [[470, 110], [470, 130]], ""],
	["dec_res", [[535, 90], [770, 90]], ""],
	["rf_alu_a", [[555, 130], [555, 112], [735, 112], [735, 125]], ""],
	["rf_alu_b", [[585, 150], [605, 150], [605, 118], [655, 118], [655, 125]], ""],
	["alu_rf", [[695, 175], [695, 205], [585, 205]], ""],
	["alu_dmem", [[695, 175], [695, 240]], ""],
	["alu_flags", [[695, 175], [695, 205], [855, 205], [855, 180]], ""],
	["flags_res", [[855, 140], [855, 110]], ""],
	["dmem_rf", [[620, 270], [585, 270]], ""],
]

## Control inputs: a short dashed stub into a slanted side.
## id → [[outside point, point on the side], label position, idle label]
const CTRLS := {
	"p_pcmux": [[[174, 85], [144, 85]], [150, 80], "select"],
	"p_alu": [[[796, 150], [762, 150]], [768, 145], "ALUK"],
}

var st: Dictionary = {}
var pipe: Dictionary = {}
var font: Font
var mono: Font
var _scale := 1.0
var _origin := Vector2.ZERO
var cps := 4
var selected_id := ""
var card: PanelContainer
var card_text: RichTextLabel

# animation: tokens move from where they were in the previous cycle
var _shown_t := -1000000         # the pipeline cycle drawn
var _movers: Array = []          # [{tok, from, to, w0, w1, a0, a1, cross}]
var _prog := 1.0                 # 0 → 1 over the animation
var still := false               # snapshot mode (user-guide pictures): no animation
var _dur := 0.0


func _ready() -> void:
	font = get_theme_default_font()
	mono = Backend.mono_font()
	clip_contents = true
	mouse_filter = Control.MOUSE_FILTER_STOP
	tooltip_text = " "
	resized.connect(queue_redraw)
	set_process(false)
	_build_card()
	_load_colors()
	Palette.changed.connect(_load_colors)


func _load_colors() -> void:
	for k in COLOR_KEYS:
		set(k, Palette.c(COLOR_KEYS[k]))
	var sb := Palette.panel_box("card", 6, 10)
	sb.border_color = C_SEL.darkened(0.3)
	sb.set_border_width_all(1)
	sb.shadow_color = Palette.c("shadow")
	sb.shadow_size = 8
	card.add_theme_stylebox_override("panel", sb)
	if selected_id != "":
		select_component(selected_id)
	queue_redraw()


func set_speed(v: int) -> void:
	cps = v


# ── info card ───────────────────────────────────────────────────────────────

func _build_card() -> void:
	card = PanelContainer.new()
	card.visible = false
	add_child(card)
	var v := VBoxContainer.new()
	card.add_child(v)
	var head := HBoxContainer.new()
	v.add_child(head)
	var hint := Label.new()
	hint.text = "Component details"
	hint.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hint.add_theme_font_size_override("font_size", 12)
	head.add_child(hint)
	var close := Button.new()
	close.text = "✕"
	close.flat = true
	close.focus_mode = Control.FOCUS_NONE
	close.tooltip_text = "Close (Esc)"
	close.pressed.connect(func(): select_component(""))
	head.add_child(close)
	card_text = RichTextLabel.new()
	card_text.bbcode_enabled = true
	card_text.scroll_active = true
	card_text.selection_enabled = true
	card_text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	card_text.add_theme_font_override("mono_font", mono)
	card_text.add_theme_font_size_override("normal_font_size", 14)
	card_text.add_theme_font_size_override("bold_font_size", 14)
	v.add_child(card_text)


func select_component(id: String) -> void:
	if id != "" and not ComponentInfo.PIPE_INFO.has(id):
		id = ""
	selected_id = id
	card.visible = id != ""
	if card.visible:
		var names := {}
		for k in BOXES:
			names[k] = BOXES[k][4]
		card_text.text = ComponentInfo.entry_bbcode(ComponentInfo.PIPE_INFO[id], names)
		var w := minf(clampf(size.x * 0.42, 280.0, 460.0), size.x - 16.0)
		var cx := _rect(id).get_center().x
		card.size = Vector2(w, minf(size.y - 16.0, 360.0))
		card.position = Vector2(8.0 if cx > size.x / 2.0 else size.x - w - 8.0, 8.0)
	queue_redraw()


func _gui_input(event: InputEvent) -> void:
	var mb := event as InputEventMouseButton
	if mb == null or not mb.pressed or mb.button_index != MOUSE_BUTTON_LEFT or still:
		return
	var id := ""
	for k in BOXES:
		if _rect(k).has_point(mb.position):
			id = k
	select_component("" if id == selected_id else id)
	accept_event()


func _unhandled_key_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k and k.pressed and k.keycode == KEY_ESCAPE and selected_id != "" and is_visible_in_tree():
		select_component("")
		get_viewport().set_input_as_handled()


func _get_tooltip(at: Vector2) -> String:
	for id in BOXES:
		if _rect(id).has_point(at):
			return "%s\n%s\n(click for details)" % [BOXES[id][4], BOXES[id][6]]
	for slot in ["E", "F"]:
		var tok = pipe.get(slot)
		if tok != null and _token_rect(SLOT_E if slot == "E" else SLOT_F,
				TOKEN_W_E if slot == "E" else TOKEN_W_F).has_point(at):
			return "%04X  %s\n%s" % [int(tok["addr"]), str(tok["text"]), _token_note(tok, slot)]
	return ""


# ── state and animation ─────────────────────────────────────────────────────

func update_state(s: Dictionary) -> void:
	st = s
	var p = s.get("pipe")
	var old := pipe
	pipe = p if p is Dictionary else {}
	if pipe.is_empty():
		_shown_t = -1000000
		queue_redraw()
		return
	var t := int(pipe["t"])
	if t != _shown_t:
		_plan_moves(old, t - _shown_t == 1)
		_shown_t = t
	queue_redraw()


func _slot_pos(slot: String) -> Vector2:
	return SLOT_E if slot == "E" else SLOT_F


func _slot_w(slot: String) -> float:
	return TOKEN_W_E if slot == "E" else TOKEN_W_F


## Where each token was last cycle and where it is now.
func _plan_moves(old: Dictionary, one_step: bool) -> void:
	_movers = []
	var was := {}       # id → slot last cycle
	for slot in ["E", "F"]:
		var o = old.get(slot)
		if o != null:
			was[int(o["id"])] = slot
	var now := {}
	for slot in ["E", "F"]:
		var tok = pipe.get(slot)
		if tok == null:
			continue
		var id := int(tok["id"])
		now[id] = slot
		var from := SPAWN if slot == "F" else SLOT_F
		var w0 := _slot_w(slot) * 0.6
		var a0 := 0.0
		if was.has(id):
			from = _slot_pos(was[id])
			w0 = _slot_w(was[id])
			a0 = 1.0
		elif slot == "E":
			a0 = 0.0
		_movers.append({"tok": tok, "slot": slot, "from": from, "to": _slot_pos(slot),
			"w0": w0, "w1": _slot_w(slot), "a0": a0, "a1": 1.0, "cross": false})
	for slot in ["E", "F"]:
		var o = old.get(slot)
		if o == null or now.has(int(o["id"])):
			continue
		var to := RETIRE if slot == "E" else _slot_pos(slot) + Vector2(0, 46)
		_movers.append({"tok": o, "slot": slot, "from": _slot_pos(slot), "to": to,
			"w0": _slot_w(slot), "w1": _slot_w(slot), "a0": 1.0, "a1": 0.0,
			"cross": slot == "F" and bool(o["flushed"])})
	_start_anim(one_step)


func _start_anim(one_step: bool) -> void:
	_prog = 1.0
	set_process(false)
	if still or not one_step or not is_visible_in_tree():
		return
	var running: bool = st.get("running", false)
	var rate: int = int(st.get("cps", cps)) if running else cps
	if running:
		_dur = ANIM_FILL / rate if rate > 0 else 0.0
		if _dur < ANIM_RUN_MIN:
			return
	else:
		_dur = max(ANIM_STEP / sqrt(rate), ANIM_MIN) if rate > 0 else ANIM_MIN
	_prog = 0.0
	set_process(true)


func _process(delta: float) -> void:
	_prog = minf(1.0, _prog + delta / maxf(_dur, 0.001))
	if _prog >= 1.0:
		set_process(false)
	queue_redraw()


func _animating() -> bool:
	return _prog < 1.0


# ── drawing helpers ─────────────────────────────────────────────────────────

func _p(x: float, y: float) -> Vector2:
	return _origin + Vector2(x, y) * _scale


func _pv(v: Vector2) -> Vector2:
	return _origin + v * _scale


func _fs(base: float) -> int:
	return max(8, int(round(base * _scale)))


func _text(pos: Vector2, s: String, sz: float, col: Color, f: Font = null, center_w: float = -1.0) -> void:
	var fnt := f if f != null else font
	var fs := _fs(sz)
	if center_w > 0:
		var tw := fnt.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x
		pos.x += (center_w * _scale - tw) / 2.0
	draw_string(fnt, pos, s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, col)


## Text cut to fit `max_w` virtual units.
func _fit(pos: Vector2, s: String, sz: float, col: Color, f: Font, max_w: float) -> void:
	var fs := _fs(sz)
	var room := max_w * _scale
	if f.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x > room:
		while s.length() > 1 and f.get_string_size(s + "…", HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x > room:
			s = s.left(s.length() - 1)
		s += "…"
	draw_string(f, pos, s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, col)


func _arrow(pts: PackedVector2Array, col: Color, width: float, dashed: bool) -> void:
	if dashed:
		for i in pts.size() - 1:
			draw_dashed_line(pts[i], pts[i + 1], col, width, 5.0 * _scale)
	else:
		draw_polyline(pts, col, width, true)
	var tip := pts[pts.size() - 1]
	var dir := (tip - pts[pts.size() - 2]).normalized()
	var n := Vector2(-dir.y, dir.x)
	var sz := 7.0 * _scale
	draw_colored_polygon(PackedVector2Array([tip, tip - dir * sz + n * sz * 0.55,
		tip - dir * sz - n * sz * 0.55]), col)


func _screen(q: Array) -> PackedVector2Array:
	var pts := PackedVector2Array()
	for v in q:
		pts.append(_p(v[0], v[1]))
	return pts


func _poly_len(q: Array) -> float:
	var l := 0.0
	for i in q.size() - 1:
		l += Vector2(q[i][0], q[i][1]).distance_to(Vector2(q[i + 1][0], q[i + 1][1]))
	return l


func _along(q: Array, d: float) -> Vector2:
	for i in q.size() - 1:
		var p0 := Vector2(q[i][0], q[i][1])
		var p1 := Vector2(q[i + 1][0], q[i + 1][1])
		var l := p0.distance_to(p1)
		if d <= l or i == q.size() - 2:
			var f := clampf(d / l, 0.0, 1.0) if l > 0 else 1.0
			return _pv(p0.lerp(p1, f))
		d -= l
	return _p(q[0][0], q[0][1])


func _slice(q: Array, d0: float, d1: float) -> PackedVector2Array:
	var out := PackedVector2Array([_along(q, d0)])
	var acc := 0.0
	for i in range(1, q.size() - 1):
		acc += Vector2(q[i - 1][0], q[i - 1][1]).distance_to(Vector2(q[i][0], q[i][1]))
		if acc > d0 and acc < d1:
			out.append(_p(q[i][0], q[i][1]))
	out.append(_along(q, d1))
	return out


func _rect(id: String) -> Rect2:
	var b: Array = BOXES[id]
	return Rect2(_p(b[0], b[1]), Vector2(b[2], b[3]) * _scale)


func _box_poly(r: Rect2, shape: String) -> PackedVector2Array:
	var inset := r.size.x * 0.12
	match shape:
		"incr":
			var d := r.size.y * 0.2
			return PackedVector2Array([r.position, r.position + Vector2(r.size.x, d),
				r.end - Vector2(0, d), Vector2(r.position.x, r.end.y)])
		"mux":
			return PackedVector2Array([r.position, r.position + Vector2(r.size.x, 0),
				r.end - Vector2(inset, 0), Vector2(r.position.x + inset, r.end.y)])
		"alu":
			var notch := r.size.x * 0.5
			return PackedVector2Array([r.position, Vector2(r.position.x + notch - 10 * _scale, r.position.y),
				Vector2(r.position.x + notch, r.position.y + r.size.y * 0.3),
				Vector2(r.position.x + notch + 10 * _scale, r.position.y), Vector2(r.end.x, r.position.y),
				r.end - Vector2(inset, 0), Vector2(r.position.x + inset, r.end.y)])
	return PackedVector2Array()


func _token_color(tok: Dictionary) -> Color:
	return Palette.c(TOKEN_KEYS[posmod(int(tok["id"]), TOKEN_KEYS.size())])


func _token_rect(c: Vector2, w: float) -> Rect2:
	return Rect2(_p(c.x - w / 2.0, c.y - TOKEN_H / 2.0), Vector2(w, TOKEN_H) * _scale)


# ── what is active this cycle ───────────────────────────────────────────────

func _mnemonic(tok) -> String:
	if tok == null:
		return ""
	return str(tok["text"]).get_slice(" ", 0).to_lower()


func _group(tok) -> String:
	return "" if tok == null else str(tok["group"])


## Wires carrying data in the cycle shown.
func _active_wires() -> Dictionary:
	var on := {}
	var e = pipe.get("E")
	var f = pipe.get("F")
	if f != null and str(f["status"]) == "fetch":
		for w in ["pc_imem", "imem_ifex", "pc_inc", "inc_pcmux", "pcmux_pc"]:
			on[w] = true
	if e == null:
		return on
	if bool(e["redirect"]):
		on["res_pcmux"] = true
		on["flush"] = true
		on.erase("inc_pcmux")
	if bool(e["refilling"]):
		return on
	var g := _group(e)
	var m := _mnemonic(e)
	if int(e["k"]) == 1:
		on["ifex_dec"] = true
		on["dec_rf"] = true
	if g in ["branch", "call/return"]:
		on["dec_res"] = true
		if m.length() > 1 and m not in ["b", "bl", "bx", "blx", "pop"]:
			on["flags_res"] = true    # B<cond> reads the flags
	if g in ["ALU", "multiply", "load", "store", "push/pop"] or m in ["bx", "blx"]:
		on["rf_alu_a"] = true
		if g in ["ALU", "multiply", "store"]:
			on["rf_alu_b"] = true
	if g in ["ALU", "multiply"]:
		if m not in ["cmp", "cmn", "tst"]:
			on["alu_rf"] = true
		if m.ends_with("s") or m in ["cmp", "cmn", "tst"]:
			on["alu_flags"] = true
	if g in ["load", "store", "push/pop"]:
		on["alu_dmem"] = true
		if g == "load" or m == "pop":
			on["dmem_rf"] = true
	return on


func _active_boxes(wires: Dictionary) -> Dictionary:
	var on := {}
	var ends := {
		"inc_pcmux": ["p_inc", "p_pcmux"], "res_pcmux": ["p_resolve", "p_pcmux"],
		"flush": ["p_resolve", "p_ifex"], "pcmux_pc": ["p_pcmux", "p_pc"], "pc_inc": ["p_pc", "p_inc"],
		"pc_imem": ["p_pc", "p_imem"], "imem_ifex": ["p_imem", "p_ifex"], "ifex_dec": ["p_ifex", "p_decode"],
		"dec_rf": ["p_decode", "p_regfile"], "dec_res": ["p_decode", "p_resolve"],
		"rf_alu_a": ["p_regfile", "p_alu"], "rf_alu_b": ["p_regfile", "p_alu"], "alu_flags": ["p_alu", "p_flags"],
		"flags_res": ["p_flags", "p_resolve"], "alu_dmem": ["p_alu", "p_dmem"],
		"alu_rf": ["p_alu", "p_regfile"], "dmem_rf": ["p_dmem", "p_regfile"],
	}
	for w in wires:
		for b in ends[w]:
			on[b] = true
	if pipe.get("E") != null:
		on["p_ifex"] = true
	return on


func _token_note(tok: Dictionary, slot: String) -> String:
	if slot == "F":
		if bool(tok["flushed"]):
			return "✕ wrong path: flushed" if _redirecting() else "fetched (sequential: predicted not-taken)"
		if str(tok["status"]) == "held":
			return "waiting in IF/EX: Execute is busy"
		return "fetching"
	if not bool(tok["timed"]):
		return "not timed (Table 81 note e)"
	var s := "cycle %d of %d" % [int(tok["k"]), int(tok["cycles"])]
	if bool(tok["refilling"]):
		s += " · refilling from the target"
	elif bool(tok["redirect"]):
		s += " · branch taken → flush"
	elif int(tok["sio"]) > 0:
		s += " · SIO via IOPORT"
	return s


func _redirecting() -> bool:
	var e = pipe.get("E")
	return e != null and bool(e["redirect"])


# ── main draw ───────────────────────────────────────────────────────────────

func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), C_BG)
	_scale = min(size.x / W, size.y / H)
	_origin = (size - Vector2(W, H) * _scale) / 2.0
	if not st.get("loaded", false) or pipe.is_empty():
		_text(_p(20, 40), "Load a program to see the pipeline." if not st.get("loaded", false)
			else "Switch the machine to 2-stage pipeline.", 18, C_DIM)
		return
	var e = pipe.get("E")
	var cycle := int(pipe["cycle"])
	var insns := int(pipe["insns"])
	var head := "RP2040 Cortex-M0+  ·  2-stage pipeline  ·  cycle %d" % cycle
	if insns > 0:
		head += "  ·  %d instructions finished  ·  CPI %.2f" % [insns, float(cycle - (int(e["k"]) if e != null else 0)) / insns]
	_text(_p(20, 17), head, 13, C_TEXT)

	# stage regions with their lane labels
	for r in [[REGION_F, C_STAGE_F, "FETCH"], [REGION_E, C_STAGE_E, "DECODE + EXECUTE"]]:
		var rr: Rect2 = r[0]
		var col: Color = r[1]
		var bg := col
		bg.a = 0.07
		draw_rect(Rect2(_pv(rr.position), rr.size * _scale), bg)
		var edge := col
		edge.a = 0.45
		draw_rect(Rect2(_pv(rr.position), rr.size * _scale), edge, false, 1.0 * _scale)
		var band := Rect2(_p(rr.position.x, rr.end.y - 22), Vector2(rr.size.x, 22) * _scale)
		draw_rect(band, col)
		_text(band.position + Vector2(8, 15) * _scale, r[2], 11, C_ON_STAGE)

	var wires := _active_wires()
	var boxes := _active_boxes(wires)
	for w in WIRES:
		var on := wires.has(w[0])
		var red: bool = w[0] == "flush"
		var dashed := red
		var col := (C_FLUSH if red else C_WIRE_ACTIVE) if on else (C_CTRL.lerp(C_BG, 0.55) if red else C_WIRE)
		var pts := _screen(w[1])
		_arrow(pts, col, (3.0 if on else 1.5) * _scale, dashed)
		if w[2] != "":
			var q: Array = w[1]
			# along the wire's top run, near its left end
			var lp := _p(q[2][0], q[2][1]) + Vector2(8, -4) * _scale
			if w[0] == "inc_pcmux":
				lp = _p(q[3][0], q[3][1]) + Vector2(8, -4) * _scale
			elif w[0] == "res_pcmux":
				lp = _p(q[1][0], q[1][1]) + Vector2(-78, -4) * _scale   # clear of the title
			elif w[0] == "flush":
				lp = _p(q[2][0], q[2][1]) + Vector2(8, 12) * _scale   # below: the target wire runs above
			_text(lp, w[2], 9, (C_FLUSH if red else C_TEXT) if on else C_DIM)
	if _animating():
		for w in WIRES:
			if wires.has(w[0]):
				_draw_pulse(w[1], C_FLUSH if w[0] == "flush" else C_GLOW)

	for id in BOXES:
		_draw_box(id, boxes.has(id))
	for id in CTRLS:
		_draw_ctrl(id)
	if BOXES.has(selected_id):
		draw_rect(_rect(selected_id).grow(3 * _scale), C_SEL, false, 2.5 * _scale)

	# instruction tokens
	if _animating():
		var f := ease(_prog, -2.0)
		for m in _movers:
			var pos: Vector2 = m["from"].lerp(m["to"], f)
			var w: float = lerpf(m["w0"], m["w1"], f)
			var a: float = lerpf(m["a0"], m["a1"], f)
			_draw_token(m["tok"], m["slot"], pos, w, a, m["cross"])
	else:
		for slot in ["F", "E"]:
			var tok = pipe.get(slot)
			if tok != null:
				_draw_token(tok, slot, _slot_pos(slot), _slot_w(slot), 1.0, false)
	if pipe.get("F") == null:
		_text(_p(SLOT_F.x - 120, SLOT_F.y + 4), "(empty: the program has ended)", 10, C_DIM)
	if e == null:
		_text(_p(SLOT_E.x - 190, SLOT_E.y + 4), "(empty: the first instruction is being fetched)", 10, C_DIM)

	_draw_strip()


## The value a control input carries this cycle ("" when not driven).
func _ctrl_value(id: String) -> String:
	var e = pipe.get("E")
	var f = pipe.get("F")
	match id:
		"p_pcmux":
			if e != null and bool(e["redirect"]):
				return "target"
			if f != null and str(f["status"]) == "fetch":
				return "PC+2"
		"p_alu":
			var wires := _active_wires()
			if e != null and (wires.has("rf_alu_a") or wires.has("alu_dmem")):
				var g := _group(e)
				return _mnemonic(e).to_upper() if g in ["ALU", "multiply"] else "ADD"
	return ""


func _draw_ctrl(id: String) -> void:
	var c: Array = CTRLS[id]
	var v := _ctrl_value(id)
	var on := v != ""
	var col := C_CTRL if on else C_CTRL.lerp(C_BG, 0.55)
	_arrow(PackedVector2Array([_p(c[0][0][0], c[0][0][1]), _p(c[0][1][0], c[0][1][1])]), col,
		(2.2 if on else 1.2) * _scale, true)
	_text(_p(c[1][0], c[1][1]), v if on else c[2], 8.5, C_CTRL if on else C_DIM, mono if on else font)


func _draw_pulse(q: Array, glow: Color) -> void:
	var l := _poly_len(q)
	var d := _prog * (l + TAIL)
	var head: float = min(d, l)
	var tail0: float = max(0.0, d - TAIL)
	var n := 10
	for i in n:
		var d0: float = lerp(tail0, head, float(i) / n)
		var d1: float = lerp(tail0, head, float(i + 1) / n)
		if d1 <= d0:
			continue
		var c := glow
		c.a = float(i + 1) / n * 0.8
		draw_polyline(_slice(q, d0, d1), c, (2.0 + 3.0 * float(i + 1) / n) * _scale, true)
	if d < l:
		var hp := _along(q, head)
		for r in [[9.0, 0.15], [5.0, 0.4], [3.0, 0.85]]:
			var c := glow
			c.a = r[1]
			draw_circle(hp, r[0] * _scale, c)
		draw_circle(hp, 1.6 * _scale, C_GLOW_HEAD)


func _draw_box(id: String, active: bool) -> void:
	var b: Array = BOXES[id]
	var r := _rect(id)
	var shape: String = b[5]
	var fill := C_BOX_ACTIVE if active else C_BOX
	var edge := C_EDGE_ACTIVE if active else C_EDGE
	var ew := (2.5 if active else 1.0) * _scale
	if id == "p_ifex" and _redirecting():
		edge = C_FLUSH
		ew = 2.5 * _scale
	var poly := _box_poly(r, shape)
	if poly.is_empty():
		draw_rect(r, fill)
		draw_rect(r, edge, false, ew)
	else:
		draw_colored_polygon(poly, fill)
		draw_polyline(poly + PackedVector2Array([poly[0]]), edge, ew, true)
	var title_col := C_TEXT if active else C_DIM
	var regs: Array = st.get("regs", [])
	match shape:
		"reg":
			_text(r.position + Vector2(6, 14) * _scale, b[4], 11, title_col)
			var f = pipe.get("F")
			if f != null:
				_text(r.position + Vector2(6, 33) * _scale, Backend.hex32(int(f["addr"])), 13, C_TEXT, mono)
		"pipereg":
			var lbl: String = b[4]
			for i in lbl.length():
				_text(r.position + Vector2(0, 22 + i * 15) * _scale, lbl[i], 11, title_col, null, b[2])
			if _redirecting():
				_text(r.position + Vector2(0, r.size.y / _scale - 12) * _scale, "✕", 14, C_FLUSH, null, b[2])
		"regfile":
			_text(r.position + Vector2(8, 16) * _scale, b[4], 11, title_col)
			var changed: Array = st.get("changed", [])
			var names := ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
				"R8", "R9", "R10", "R11", "R12", "SP", "LR"]
			for i in names.size():
				if i >= regs.size():
					break
				var cx := 6.0 if i < 8 else 92.0
				var row := i if i < 8 else i - 8
				var nm: String = names[i]
				_text(r.position + Vector2(cx, 34 + row * 15) * _scale, "%-4s%08X" % [nm, int(regs[i])], 8.5,
					C_CHANGED if changed.has(nm) else C_TEXT, mono)
		"flags":
			var fl: Dictionary = st.get("flags", {})
			var x := 8.0
			var changed: Array = st.get("changed", [])
			for k in ["N", "Z", "C", "V"]:
				_text(r.position + Vector2(x, 26) * _scale, "%s%d" % [k, int(fl.get(k, 0))], 12,
					C_CHANGED if changed.has(k) else C_TEXT, mono)
				x += 23
		"alu":
			_text(r.position + Vector2(0, 36) * _scale, "ALU", 13, title_col, null, b[2])
		_:
			_text(r.position + Vector2(0, (20 if id != "p_imem" else 24)) * _scale, b[4], 11, title_col, null, b[2])
			var sub := ""
			match id:
				"p_imem":
					sub = "AHB-Lite → flash (XIP) / SRAM"
				"p_dmem":
					var e = pipe.get("E")
					sub = "AHB-Lite  ·  IOPORT → SIO"
					if active and e != null and int(e["sio"]) > 0:
						sub = "IOPORT → SIO (1 cycle)"
					elif active and e != null:
						sub = "AHB-Lite (2 cycles)"
				"p_resolve":
					var e = pipe.get("E")
					if e != null and bool(e["redirect"]):
						sub = "TAKEN → redirect + flush"
					elif e != null and active:
						sub = "not taken: keep going"
				"p_decode":
					var e = pipe.get("E")
					if active and e != null:
						sub = str(e["row"])
			if sub != "":
				_fit(r.position + Vector2(6, (r.size.y / _scale) - 7) * _scale, sub, 9,
					C_FLUSH if sub.begins_with("TAKEN") else C_MUTED, font, b[2] - 12)


## One instruction: a pill labelled with its address and assembly text.
func _draw_token(tok: Dictionary, slot: String, c: Vector2, w: float, alpha: float, cross: bool) -> void:
	if alpha <= 0.01:
		return
	var col := _token_color(tok)
	var r := _token_rect(c, w)
	var sb := StyleBoxFlat.new()
	var fill := C_BOX.lerp(col, 0.22)
	fill.a = alpha
	sb.bg_color = fill
	var bc := col
	bc.a = alpha
	var flushed := bool(tok["flushed"]) and (slot == "F" and (_redirecting() or cross))
	if flushed:
		bc = C_FLUSH
		bc.a = alpha
	sb.border_color = bc
	sb.set_border_width_all(maxi(1, roundi(2.0 * _scale)))
	sb.border_width_left = maxi(2, roundi(7.0 * _scale))
	sb.set_corner_radius_all(roundi(8 * _scale))
	if slot == "F" and str(tok.get("status", "")) == "held":
		sb.draw_center = true
		sb.bg_color = C_BOX
	draw_style_box(sb, r)
	var tc := C_TEXT
	tc.a = alpha
	var mc := C_MUTED
	mc.a = alpha
	var txt := "%04X  %s" % [int(tok["addr"]), str(tok["text"])]
	_fit(r.position + Vector2(14, 19) * _scale, txt, 12.5, tc, mono, w - 22)
	var note := _token_note(tok, slot) if slot != "" else ""
	if cross:
		note = "✕ flushed: wrong path"
	_fit(r.position + Vector2(14, 37) * _scale, note, 10, C_FLUSH if (flushed or cross) else mc, font, w - 22)
	if flushed or cross:
		var fc := C_FLUSH
		fc.a = alpha * 0.8
		draw_line(r.position + Vector2(6, 6) * _scale, r.end - Vector2(6, 6) * _scale, fc, 2.0 * _scale)
		draw_line(Vector2(r.position.x + 6 * _scale, r.end.y - 6 * _scale),
			Vector2(r.end.x - 6 * _scale, r.position.y + 6 * _scale), fc, 2.0 * _scale)


## The last few cycles, one row per instruction: each instruction is one
## column (one cycle) behind the one before.
func _draw_strip() -> void:
	var trail: Array = pipe.get("trail", [])
	var first := int(pipe["first"])
	var t := int(pipe["t"])
	var ncol := t - first + 1
	var x0 := 330.0
	var cw := (W - 20 - x0) / ncol
	var top := STRIP_Y + 34
	var rh := minf(22.0, (H - 16 - top) / maxf(1.0, trail.size()))
	_text(_p(20, STRIP_Y + 12), "Instructions in flight  ·  last %d cycles" % ncol, 11, C_TEXT)
	var lx := x0
	for e in [[C_STAGE_F, "fetch"], [C_STAGE_E, "execute"]]:
		draw_rect(Rect2(_p(lx, STRIP_Y + 2), Vector2(12, 12) * _scale), e[0])
		_text(_p(lx + 16, STRIP_Y + 12), e[1], 9.5, C_MUTED)
		lx += 70
	_text(_p(lx, STRIP_Y + 12), "outlined: waiting in IF/EX, or refilling after a taken branch   ·   ✕ flushed", 9.5, C_MUTED)
	# current cycle column
	var cur := Rect2(_p(x0 + (t - first) * cw, top - 4), Vector2(cw, rh * trail.size() + 6) * _scale)
	draw_rect(cur, C_BOX_ACTIVE)
	for u in range(first, t + 1):
		if u >= -1:
			_text(_p(x0 + (u - first) * cw + 4, top - 7), "cycle %d" % (u + 1), 8.5,
				C_TEXT if u == t else C_DIM)
	var fresh := 1.0 if not _animating() else _prog
	for i in trail.size():
		var tok: Dictionary = trail[i]
		var y := top + i * rh
		var col := _token_color(tok)
		draw_circle(_p(26, y + rh / 2.0), 4.5 * _scale, col)
		var lc := C_DIM if bool(tok["flushed"]) else C_TEXT
		_fit(_p(36, y + rh / 2.0 + 4), "%04X  %s" % [int(tok["addr"]), str(tok["text"])], 10, lc, mono, x0 - 44)
		for cell in tok["cells"]:
			var u := int(cell[0])
			var code := str(cell[1])
			var rect := Rect2(_p(x0 + (u - first) * cw + 2, y + 2), Vector2(cw - 4, rh - 4) * _scale)
			var a := fresh if u == t else 1.0
			var fill := C_STAGE_E if code in ["E", "R"] else C_STAGE_F
			fill.a = a
			var label: String = {"F": "Fetch", "E": "Execute", "R": "refill", "W": "waiting"}[code]
			if bool(tok["flushed"]):
				var g := C_PANEL_ALT
				g.a = a
				draw_rect(rect, g)
				var fc := C_FLUSH
				fc.a = a
				draw_rect(rect, fc, false, 1.0 * _scale)
				_fit(rect.position + Vector2(5 * _scale, rect.size.y - 5 * _scale), "✕ flushed", 9, fc, font, cw - 10)
			elif code in ["R", "W"]:
				draw_rect(rect, fill, false, 2.0 * _scale)
				var tc := C_TEXT
				tc.a = a
				_fit(rect.position + Vector2(5 * _scale, rect.size.y - 5 * _scale), label, 9, tc, font, cw - 10)
			else:
				draw_rect(rect, fill)
				var oc := C_ON_STAGE
				oc.a = a
				_fit(rect.position + Vector2(5 * _scale, rect.size.y - 5 * _scale), label, 9, oc, font, cw - 10)


## BBCode explaining the cycle shown (main.gd's caption panel).
static func caption(p: Dictionary, footnote := true) -> String:
	if p.is_empty():
		return ""
	var dim := Palette.hex("dim")
	var st := stage_text(p)
	var out := "[b]Pipeline cycle %d[/b]\n" % int(p["cycle"])
	out += "\n[color=%s][b]Decode + Execute[/b][/color] — %s\n" % [Palette.hex("stage_e"), st["E"]]
	out += "\n[color=%s][b]Fetch[/b][/color] — %s\n" % [Palette.hex("stage_f"), st["F"]]
	if not footnote:
		return out
	out += "\n[color=%s]The core runs each instruction as a whole, so the registers show the state after the instruction in Execute has finished. Step Cycle (F11) moves the pipeline one cycle; Step Insn (F10) jumps to the next instruction's first Execute cycle.[/color]" % dim
	return out


## What each stage holds in cycle view `p`, in BBCode: {"E": …, "F": …}.
static func stage_text(p: Dictionary) -> Dictionary:
	var e = p.get("E")
	var f = p.get("F")
	var et := ""
	if e == null:
		et = "empty: the first instruction is still being fetched."
	else:
		et = "[code]%04X  %s[/code]" % [int(e["addr"]), _esc(str(e["text"]))]
		if not bool(e["timed"]):
			et += ": the datasheet gives no cycle count for this instruction (Table 81 note e), so it is drawn as one cycle."
		else:
			et += ", Table 81 row [i]%s[/i]: cycle %d of %d." % [_esc(str(e["row"])), int(e["k"]), int(e["cycles"])]
			if bool(e["redirect"]):
				et += " The branch is [b]taken[/b]: BRANCH RESOLVE sends the target to the PC MUX and flushes the instruction fetched behind it, which was the wrong one."
			elif bool(e["refilling"]):
				et += " The PC already holds the branch target; this cycle fetches the target to refill the pipeline. This is the cost of a taken branch."
			elif int(e["cycles"]) > 1 and int(e["k"]) < int(e["cycles"]):
				et += " It needs another cycle, so the instruction behind it waits."
			if int(e["sio"]) > 0:
				et += " Its data access goes to the SIO over the single-cycle IOPORT."
	var ft := ""
	if f == null:
		ft = "empty: the program has ended."
	else:
		ft = "[code]%04X  %s[/code]" % [int(f["addr"]), _esc(str(f["text"]))]
		if bool(f["flushed"]) and e != null and bool(e["redirect"]):
			ft += ": fetched from the next sequential address (the M0+ predicts not-taken), but the branch in Execute was taken, so it is flushed (✕) and never executes."
		elif bool(f["flushed"]):
			ft += ": fetched from the next sequential address; the branch in Execute will redirect the PC."
		elif str(f["status"]) == "held":
			ft += " was fetched last cycle and waits in IF/EX until Execute is free."
		else:
			ft += " is fetched over the AHB-Lite bus. It will execute next cycle if Execute is free."
	return {"E": et, "F": ft}


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")
