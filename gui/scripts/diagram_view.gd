extends Control
## Native CPU block diagram of the multi-cycle teaching datapath.
##
## Everything is drawn in a fixed virtual canvas (W × H) and scaled to fit.
## Each wire lists the control signals that make it carry data; for the
## selected cycle the active wires and boxes light up.  Storage elements
## (PC, IR, MAR, MDR, registers, flags, memory) show the value they hold
## *during* the cycle; a value they capture at the clock edge that ends the
## cycle is shown as an "old → new" caption and only appears in the box when
## the next cycle is shown.  Hover a box or tri-state buffer to see what it
## does; click it for a detailed card (what it does, how it works, what it
## connects to — from component_info.gd) and its wires are highlighted.
##
## Every mux, the ALU and the adders take data in on their long side and put
## their result out of their short side; control selects (PCMUX, ALUK,
## GatePC…) enter on a slanted side as short purple stubs.
##
## When a new cycle is shown, a glowing pulse travels along the active wires
## in the order data actually flows (source register → gate → bus → loads).
## A box's pending "old → new" caption appears when the pulse reaches it.
## The pulse's speed follows the clock-speed slider.

const W := 1000.0
const H := 640.0
const BUS_Y := 290.0

## Drawing colours: each comes from this palette key (see _load_colors).
const COLOR_KEYS := {
	"C_BG": "surface", "C_BOX": "box", "C_BOX_ACTIVE": "box_active", "C_EDGE": "edge",
	"C_EDGE_ACTIVE": "accent", "C_WIRE": "wire", "C_WIRE_ACTIVE": "accent", "C_TEXT": "text",
	"C_DIM": "dim", "C_CHANGED": "changed", "C_BUS": "bus", "C_CTRL": "purple",
	"C_GLOW": "glow", "C_GLOW_CTRL": "glow_ctrl", "C_GLOW_HEAD": "glow_head", "C_SEL": "sel",
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
var C_CHANGED: Color
var C_BUS: Color
var C_CTRL: Color
var C_GLOW: Color
var C_GLOW_CTRL: Color
var C_GLOW_HEAD: Color
var C_SEL: Color

const ComponentInfo := preload("res://scripts/component_info.gd")

## Boxes that capture their inputs at the clock edge: data flowing into them
## does not continue out of them in the same cycle.
const SEQUENTIAL := ["pc", "ir", "ir2", "mar", "mdr", "regfile", "flags"]
## Fraction of a clock period the pulse takes to cover the whole cycle's path.
const ANIM_FILL := 0.85
## Stepping by hand: seconds at 1 cyc/s, shrinking with √speed down to ANIM_MIN.
const ANIM_STEP := 1.6
const ANIM_MIN := 0.35
const ANIM_RUN_MIN := 0.06   # running faster than this per cycle → no animation
const TAIL := 70.0           # glow tail length, virtual units

## id: [x, y, w, h, label, shape, tooltip]
const BOXES := {
	"align": [30, 30, 120, 32, "PC / Align(PC,4)", "box",
		"Supplies the PC-relative base to the address adder: the PC read value A + 4 (A = the instruction's address), or that value rounded down to a word boundary for literal loads and ADR."],
	"addr1mux": [30, 110, 125, 30, "ADDR1MUX", "mux",
		"Chooses the address adder's base: the PC-relative value, a register (SR1) or SP."],
	"addr2mux": [175, 110, 125, 30, "ADDR2MUX", "mux",
		"Chooses the offset added to the base: a zero-extended, scaled immediate or a sign-extended branch offset (from IMM / OFFSET), a register (SR2), ±4n for PUSH/POP/LDM/STM, or zero."],
	"adder": [95, 180, 130, 36, "ADDRESS ADDER", "adder",
		"Adds base + offset to form an effective address or a branch target. It is separate from the ALU."],
	"pcmux": [360, 30, 110, 30, "PCMUX", "mux",
		"Chooses the next PC: PC + 2 (sequential), the address adder (branch target) or the bus through CLR THUMB BIT (BX, BLX, POP {PC}, MOV/ADD PC). The FSM picks the input and asserts LD.PC."],
	"pc": [360, 100, 110, 40, "PC", "reg",
		"Program Counter: address of the next halfword to fetch."],
	"inc": [490, 100, 50, 40, "+2", "incr",
		"Incrementer: PC + 2, used during fetch to step to the next halfword. PCINC selects the increment."],
	"clrt": [560, 170, 90, 30, "CLR THUMB", "box",
		"Clears bit 0 of a value loaded into PC from the bus (BX, BLX, POP {PC}, MOV/ADD PC) to form the halfword-aligned fetch address."],
	"ir": [665, 30, 130, 36, "IR", "reg",
		"Instruction Register: the 16-bit instruction (or first halfword) being executed. Its bits drive the FSM."],
	"ir2": [815, 30, 130, 36, "IR2", "reg",
		"Second instruction register: holds the second halfword of a 32-bit encoding such as BL."],
	"fsm": [665, 100, 145, 72, "FSM", "fsm",
		"Finite State Machine: the control unit. It shows the state of this cycle; it asserts that state's control signals (listed in the cycle panel) and moves to the next state at the clock edge."],
	"ext": [830, 100, 115, 34, "IMM / OFFSET", "box",
		"Extracts immediates and offsets from IR/IR2 and zero- or sign-extends and scales them."],
	"cond": [830, 170, 115, 34, "COND EVAL", "box",
		"Evaluates a branch condition (IR[11:8]) against the NZCV flags and sends BranchTaken to the FSM. If it is 1 the FSM selects PCMUX=ADDER and asserts LD.PC; if 0 the PC keeps its sequential value."],
	"vec": [665, 205, 110, 30, "VECTOR ADDR", "box",
		"Supplies the SVCall exception-vector address (0x0000002C) to the bus through gateVector on SVC entry. picosim then runs the supervisor call directly (simplified exception model)."],
	"mar": [30, 340, 115, 40, "MAR", "reg",
		"Memory Address Register: the address for the next memory or I/O access."],
	"marinc": [170, 344, 44, 32, "+4", "incr",
		"MAR incrementer: steps MAR to the next word between the transfers of PUSH, POP, LDM and STM (MAR+4)."],
	"mem": [30, 430, 230, 150, "MEMORY / I-O", "mem",
		"64 KB RAM at 0x0000–0xFFFF; addresses from 0x10000 up are memory-mapped I/O (GPIO: SIO at 0xD0000000, IO_BANK0, PADS_BANK0). Reads fill MDR; writes take their data from MDR."],
	"mdr": [290, 430, 115, 40, "MDR", "reg",
		"Memory Data Register: data just read from memory, or data about to be written."],
	"loadext": [290, 340, 115, 32, "LOAD EXT", "box",
		"Selects the byte/halfword lanes of a loaded value and zero- or sign-extends it to 32 bits. Its output reaches the bus through the gateMDR tri-state buffer."],
	"storealign": [425, 340, 115, 32, "STORE ALIGN", "box",
		"Places store data in the correct byte lanes and sets the byte enables for STRB/STRH."],
	"regfile": [560, 325, 230, 165, "REGISTER FILE", "regfile",
		"R0–R12, SP and LR. Two read ports (SR1, SR2) feed the ALU and the address muxes; the write port (DR) is loaded from the bus at the clock edge when LD.REG (or LD.SP / LD.LR) is asserted."],
	"sett": [825, 325, 115, 30, "SET THUMB", "box",
		"Sets bit 0 of a return address before it is written to LR (BL, BLX)."],
	"flags": [825, 420, 115, 40, "N Z C V", "flags",
		"Condition flags, loaded from the ALU when LD.CC is asserted (instructions ending in S, and CMP/CMN/TST)."],
	"sr2mux": [650, 505, 95, 26, "SR2MUX", "mux",
		"Chooses the ALU's B input: the second register read port (SR2) or an immediate from IR."],
	"alu": [560, 548, 185, 52, "ALU", "alu",
		"Arithmetic Logic Unit: ADD, SUB, AND, ORR, EOR, shifts, MUL… or PASS (forwards a register, e.g. store data or a BX target). Its operands are loaded into the ALU A / ALU B input latches (LD.ALUA, LD.ALUB) at the end of FETCH_OPERANDS; ALUK selects the operation."],
}

## [id, points, activating signals, tag]
## A signal ending in '=' matches any value, "A=x" matches values starting with x; "A&B" requires both; "@STATE"
## matches the FSM state name.
const WIRES := [
	["pc_bus", [[415, 140], [415, BUS_Y]], ["GatePC"], "gate"],
	["bus_mar", [[88, BUS_Y], [88, 340]], ["LD.MAR"], ""],
	["mar_mem", [[88, 380], [88, 430]], ["MEM.EN"], ""],
	["mem_mdr", [[260, 444], [290, 444]], ["R/W=READ"], ""],
	["mdr_mem", [[290, 458], [260, 458]], ["R/W=WRITE"], ""],
	["mdr_loadext", [[347, 430], [347, 372]], ["GateMDR"], ""],
	["loadext_bus", [[347, 340], [347, BUS_Y]], ["GateMDR"], "gate"],
	["bus_storealign", [[482, BUS_Y], [482, 340]], ["STORE ALIGN", "LD.MDR&GateALU"], ""],
	["storealign_mdr", [[482, 372], [482, 450], [405, 450]], ["STORE ALIGN", "LD.MDR&GateALU"], ""],
	["bus_ir", [[652, BUS_Y], [652, 48], [665, 48]], ["LD.IR"], ""],
	["bus_ir2", [[960, BUS_Y], [960, 48], [945, 48]], ["LD.IR2"], ""],
	["ir_fsm", [[730, 66], [730, 100]], ["@DECODE", "@FETCH2_ADDR"], "decode"],
	["ir_ext", [[785, 66], [785, 84], [860, 84], [860, 100]], ["@DECODE", "SR2MUX=IMM", "ADDR2MUX=Z", "ADDR2MUX=S"], "decode"],
	["ir2_ext", [[900, 66], [900, 100]], ["#WIDE&@DECODE", "#WIDE&ADDR2MUX=S"], "decode"],
	["pc_inc", [[470, 120], [490, 120]], ["PCINC=+2"], ""],
	["inc_pcmux", [[540, 120], [552, 120], [552, 18], [440, 18], [440, 30]], ["PCMUX=PC+2"], ""],
	["pcmux_pc", [[415, 60], [415, 100]], ["LD.PC"], ""],
	["adder_pcmux", [[200, 216], [200, 228], [330, 228], [330, 12], [392, 12], [392, 30]], ["PCMUX=ADDER"], ""],
	["bus_clrt", [[605, BUS_Y], [605, 200]], ["PCMUX=BUS"], ""],
	["clrt_pcmux", [[605, 170], [605, 6], [458, 6], [458, 30]], ["PCMUX=BUS"], ""],
	["pc_align", [[360, 112], [345, 112], [345, 46], [150, 46]], ["ADDR1MUX=PC", "ADDR1MUX=Align"], ""],
	["align_a1", [[62, 62], [62, 110]], ["ADDR1MUX=PC", "ADDR1MUX=Align"], ""],
	["reg_a1", [[118, 80], [118, 110]], ["ADDR1MUX=SR1", "ADDR1MUX=SP"], "stub:SR1 / SP"],
	["off_a2", [[238, 80], [238, 110]], ["ADDR2MUX=Z", "ADDR2MUX=S", "ADDR2MUX=-", "ADDR2MUX=+", "ADDR2MUX=SR2"],
		"stub:IMM / SR2 / ±4n"],
	["a1_adder", [[92, 140], [130, 180]], ["ADDR1MUX="], ""],
	["a2_adder", [[238, 140], [190, 180]], ["ADDR2MUX="], ""],
	["adder_bus", [[150, 216], [150, BUS_Y]], ["GateADDR"], "gate"],
	["bus_reg", [[610, BUS_Y], [610, 325]], ["LD.REG", "LD.SP"], ""],
	["bus_sett", [[882, BUS_Y], [882, 325]], ["SET THUMB BIT"], ""],
	["sett_reg", [[825, 340], [790, 340]], ["SET THUMB BIT"], ""],
	["reg_alua", [[600, 490], [600, 548]], ["SR1=&@FETCH_OPERANDS"], ""],
	["reg_sr2", [[685, 490], [685, 505]], ["SR2=&@FETCH_OPERANDS"], ""],
	["ext_sr2", [[945, 117], [978, 117], [978, 497], [725, 497], [725, 505]], ["SR2MUX=IMM&@FETCH_OPERANDS"], ""],
	["sr2_alub", [[698, 531], [698, 548]], ["SR2=&@FETCH_OPERANDS", "SR2MUX=IMM&@FETCH_OPERANDS"], ""],
	["alu_bus", [[700, 600], [700, 608], [805, 608], [805, BUS_Y]], ["GateALU"], "gate"],
	["alu_flags", [[640, 600], [640, 616], [925, 616], [925, 460]], ["LD.CC"], ""],
	["flags_cond", [[940, 440], [968, 440], [968, 187], [945, 187]], ["BranchTaken="], "ctrl"],
	["cond_fsm", [[830, 187], [819, 187], [819, 150], [810, 150]], ["BranchTaken="], "ctrl"],
	["mar_inc", [[135, 340], [135, 330], [228, 330], [228, 360], [214, 360]], ["MAR+4"], ""],
	["inc_mar", [[170, 360], [145, 360]], ["MAR+4"], ""],
	["vec_bus", [[720, 235], [720, BUS_Y]], ["GateVEC"], "gate"],
]

## Incrementers drawn as sideways adders: "right" = long (input) side on the
## left, short (output) side on the right.
const ORIENT := {"inc": "right", "marinc": "left"}

## Tri-state buffers onto the bus: id → [wire it sits on, control signal,
## component that feeds it, side its control input enters (-1 left, +1 right)].
## Each is named "gate" + the component that drives it.
const GATES := {
	"gatePC": ["pc_bus", "GatePC", "pc", -1],
	"gateAdder": ["adder_bus", "GateADDR", "adder", 1],
	"gateMDR": ["loadext_bus", "GateMDR", "loadext", 1],
	"gateALU": ["alu_bus", "GateALU", "alu", -1],
	"gateVector": ["vec_bus", "GateVEC", "vec", 1],
}
const GATE_SIZE := 8.0     # half the triangle's length, virtual units
const GATE_BACK := 26.0    # distance from the bus to the triangle's centre
const GATE_STUB := 16.0    # length of a buffer's control input

## Control inputs of the muxes, the +2 incrementer and the ALU: a short
## dashed stub into a slanted side.  id → [stub points (outside → side),
## signal prefix, label position, idle label].
const CTRLS := {
	"pcmux": [[[490, 45], [464, 45]], "PCMUX=", [478, 57], "select"],
	"addr1mux": [[[170, 125], [148, 125]], "ADDR1MUX=", [151, 137], "select"],
	"addr2mux": [[[316, 125], [293, 125]], "ADDR2MUX=", [296, 137], "select"],
	"sr2mux": [[[762, 518], [740, 518]], "SR2MUX", [747, 532], "select"],
	"alu": [[[772, 574], [734, 574]], "ALUK=", [749, 568], "ALUK"],
	"inc": [[[515, 158], [515, 138]], "PCINC=", [520, 160], "PCINC"],
}

var cycle: Dictionary = {}
var st: Dictionary = {}
var values: Dictionary = {}      # register/datapath values after the shown cycle
var font: Font
var mono: Font
var _scale := 1.0
var _origin := Vector2.ZERO
var cps := 4                     # clock-speed slider value (0 = max)
var before: Dictionary = {}      # values held during the shown cycle (before its edge)
var after: Dictionary = {}       # values after the shown cycle's clock edge
var _key := 0                    # identifies the cycle being animated
var _paths: Array = []           # [{wire, pts (virtual), start, len}] for active wires
var _arrive: Dictionary = {}     # node id → distance at which the pulse reaches it
var _total := 0.0                # distance to the end of the last wire
var _dist := INF                 # how far the pulse has travelled
var _speed := 0.0                # virtual units per second
var selected_id := ""            # component whose info card is open
var card: PanelContainer
var card_text: RichTextLabel
var card_hint: Label
var text_scale := 1.0            # set by main when the diagram is dragged bigger
var _card_pos = null             # Vector2 once the user has dragged the card
var _card_grab = null            # mouse offset into the card while dragging
var still := false               # snapshot mode (user-guide pictures): no animation


func _ready() -> void:
	font = get_theme_default_font()
	mono = Backend.mono_font()
	clip_contents = true
	mouse_filter = Control.MOUSE_FILTER_STOP
	tooltip_text = " "
	resized.connect(queue_redraw)
	resized.connect(_layout_card)
	set_process(false)
	_build_card()
	_load_colors()
	Palette.changed.connect(_load_colors)


func _load_colors() -> void:
	for k in COLOR_KEYS:
		set(k, Palette.c(COLOR_KEYS[k]))
	var sb := StyleBoxFlat.new()
	sb.bg_color = Palette.c("card")
	sb.border_color = C_SEL.darkened(0.3)
	sb.set_border_width_all(1)
	sb.set_corner_radius_all(6)
	sb.content_margin_left = 12
	sb.content_margin_right = 8
	sb.content_margin_top = 8
	sb.content_margin_bottom = 10
	sb.shadow_color = Palette.c("shadow")
	sb.shadow_size = 8
	card.add_theme_stylebox_override("panel", sb)
	card_hint.add_theme_color_override("font_color", C_DIM)
	if selected_id != "":
		select_component(selected_id)
	queue_redraw()


# ── component info card ─────────────────────────────────────────────────────

func _build_card() -> void:
	card = PanelContainer.new()
	card.visible = false
	card.mouse_filter = Control.MOUSE_FILTER_STOP
	card.mouse_default_cursor_shape = Control.CURSOR_MOVE
	card.gui_input.connect(_card_input)
	add_child(card)
	var v := VBoxContainer.new()
	v.mouse_filter = Control.MOUSE_FILTER_PASS
	card.add_child(v)
	var head := HBoxContainer.new()
	head.mouse_filter = Control.MOUSE_FILTER_PASS
	v.add_child(head)
	# the header (and the card's border) is a handle for dragging it aside
	var hint := Label.new()
	hint.text = "Component details  ·  drag to move"
	hint.mouse_filter = Control.MOUSE_FILTER_PASS
	hint.mouse_default_cursor_shape = Control.CURSOR_MOVE
	hint.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	card_hint = hint
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
	card_text.fit_content = false
	card_text.scroll_active = true
	card_text.selection_enabled = true
	card_text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	card_text.add_theme_font_override("mono_font", mono)
	card_text.add_theme_font_size_override("normal_font_size", 14)
	card_text.add_theme_font_size_override("bold_font_size", 14)
	card_text.add_theme_font_size_override("mono_font_size", 12)
	v.add_child(card_text)


## Open the info card for a component id (a BOXES or GATES key, or "bus");
## "" closes it.
func select_component(id: String) -> void:
	if id != "" and not ComponentInfo.INFO.has(id):
		id = ""
	selected_id = id
	if id == "":
		_card_pos = null  # the next card opens in its default spot again
		_card_grab = null
	card.visible = id != "" and not still
	if card.visible:
		var names := {"bus": "BUS"}
		for k in BOXES:
			names[k] = BOXES[k][4]
		for k in GATES:
			names[k] = k
		card_text.text = ComponentInfo.bbcode(id, names, roundi(17 * text_scale)) + _live_status(id)
		card_text.scroll_to_line(0)
		_layout_card()
		# the text's height is known once it has been laid out at the card's width
		await get_tree().process_frame
		_layout_card()
	queue_redraw()


## "This cycle" section of a tri-state buffer's card: is its control signal
## asserted, and what is it driving onto the bus?
func _live_status(id: String) -> String:
	if not GATES.has(id):
		return ""
	var sig: String = GATES[id][1]
	var head := Palette.hex("accent")
	var t := "\n\n[color=%s][b]This cycle[/b][/color]\n" % head
	if cycle.is_empty():
		return t + "No cycle has run yet. Press Step Cycle (F11)."
	var src: String = BOXES[GATES[id][2]][4]
	if _has(sig):
		var v = cycle.get("bus")
		t += "[color=%s][code]%s = 1[/code][/color] in %s: the buffer is enabled and drives the output of %s%s onto the bus." % [
			Palette.hex("purple"), sig, str(cycle.get("state", "")), src,
			(" (" + Backend.hex32(v) + ")") if v != null else ""]
	else:
		t += "[color=%s][code]%s = 0[/code][/color] in %s: the buffer is off (high impedance, Z), so the output of %s is disconnected from the bus." % [
			Palette.hex("muted"), sig, str(cycle.get("state", "")), src]
	return t


## The info card's text grows with the diagram (see main.gd's text scaling).
func _on_text_scale(s: float) -> void:
	text_scale = s
	if selected_id != "":
		select_component(selected_id)


func _layout_card() -> void:
	if card == null or not card.visible:
		return
	var w := minf(clampf(size.x * 0.42, 280.0 * text_scale, 480.0 * text_scale), size.x - 16.0)
	var chrome := 58.0 * text_scale
	var h := minf(card_text.get_content_height() + chrome, size.y - 16.0)
	h = maxf(h, minf(200.0, size.y - 16.0))
	# put the card on the side away from the selected component
	var cx := 0.0
	if selected_id == "bus":
		cx = size.x
	elif BOXES.has(selected_id):
		cx = _rect(selected_id).get_center().x
	elif GATES.has(selected_id):
		cx = _p(_gate_geom(selected_id)["c"].x, 0).x
	var x := 8.0 if cx > size.x / 2.0 else size.x - w - 8.0
	card.size = Vector2(w, h)
	card.position = Vector2(x, 8) if _card_pos == null else _clamp_card(_card_pos)


## Keep at least part of the card's header inside the diagram.
func _clamp_card(at: Vector2) -> Vector2:
	var keep := 60.0
	return Vector2(clampf(at.x, keep - card.size.x, size.x - keep),
		clampf(at.y, 0.0, maxf(0.0, size.y - 30.0)))


func _card_input(event: InputEvent) -> void:
	var mb := event as InputEventMouseButton
	if mb and mb.button_index == MOUSE_BUTTON_LEFT:
		_card_grab = get_local_mouse_position() - card.position if mb.pressed else null
		card.accept_event()
	elif event is InputEventMouseMotion and _card_grab != null:
		_card_pos = _clamp_card(get_local_mouse_position() - _card_grab)
		card.position = _card_pos
		card.accept_event()


func _hit(at: Vector2) -> String:
	for id in GATES:
		if _gate_rect(id).has_point(at):
			return id
	for id in BOXES:
		if _rect(id).has_point(at):
			return id
	if abs(at.y - _p(0, BUS_Y).y) < 8 * _scale and at.x >= _p(20, 0).x and at.x <= _p(970, 0).x:
		return "bus"
	return ""


func _gui_input(event: InputEvent) -> void:
	var mb := event as InputEventMouseButton
	if mb == null or not mb.pressed or mb.button_index != MOUSE_BUTTON_LEFT:
		return
	if not st.get("loaded", false):
		return
	var id := _hit(mb.position)
	select_component("" if id == selected_id else id)
	accept_event()


func _unhandled_key_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k and k.pressed and k.keycode == KEY_ESCAPE and selected_id != "":
		select_component("")
		get_viewport().set_input_as_handled()


## Wires that start or end on the selected component (for a tri-state
## buffer: the wire it sits on).
func _selected_wire(w: Array) -> bool:
	if selected_id == "":
		return false
	if GATES.has(selected_id):
		return w[0] == GATES[selected_id][0]
	var pts: Array = w[1]
	var ends := [pts[0], pts[pts.size() - 1]]
	if selected_id == "bus":
		return ends.any(func(q): return is_equal_approx(q[1], BUS_Y))
	var r := _rect(selected_id).grow(3 * _scale)
	return ends.any(func(q): return r.has_point(_p(q[0], q[1])))


func set_speed(v: int) -> void:
	cps = v


func _process(delta: float) -> void:
	_dist += _speed * delta
	if _dist >= _total + TAIL:
		_dist = INF
		set_process(false)
	queue_redraw()


func update_state(s: Dictionary) -> void:
	st = s
	if not s.get("loaded", false):
		return
	# The cycle panel announces a cycle before this state arrives, so plan
	# again with it (without restarting the animation).
	if not cycle.is_empty():
		values = _snapshot()
		_plan()
	queue_redraw()


func show_cycle(info: Dictionary) -> void:
	cycle = info
	var key := info.hash()
	if key != _key:
		_key = key
		values = _snapshot()
		_plan()
		_start_anim()
		if GATES.has(selected_id):
			select_component(selected_id)   # refresh its "This cycle" section
	queue_redraw()


func _start_anim() -> void:
	_dist = INF
	set_process(false)
	if still or cycle.is_empty() or _paths.is_empty() or not is_visible_in_tree():
		return
	var running: bool = st.get("running", false)
	var rate: int = int(st.get("cps", cps)) if running else cps
	var dur := 0.0
	if running:
		dur = ANIM_FILL / rate if rate > 0 else 0.0
		if dur < ANIM_RUN_MIN:
			return
	else:
		dur = max(ANIM_STEP / sqrt(rate), ANIM_MIN) if rate > 0 else ANIM_MIN
	_speed = (_total + TAIL) / dur
	_dist = 0.0
	set_process(true)


func _animating() -> bool:
	return _dist != INF


## How far along the pulse is at node `id` (true once it has arrived).
func _reached(id: String) -> bool:
	return not _animating() or _dist >= _arrive.get(id, _total)


# ── flow planning ───────────────────────────────────────────────────────────

func _node_at(q: Array) -> String:
	var v := Vector2(q[0], q[1])
	for id in BOXES:
		var b: Array = BOXES[id]
		if Rect2(b[0], b[1], b[2], b[3]).grow(2).has_point(v):
			return id
	if abs(v.y - BUS_Y) < 1.0:
		return "bus"
	return ""


static func _poly_len(pts: Array) -> float:
	var l := 0.0
	for i in pts.size() - 1:
		l += Vector2(pts[i][0], pts[i][1]).distance_to(Vector2(pts[i + 1][0], pts[i + 1][1]))
	return l


## Orders the active wires by data flow: each wire starts where the wires
## feeding its source end, so the pulse moves at one speed through every
## junction.  Wires leaving the bus first run along it from the driver.
func _plan() -> void:
	_paths = []
	_arrive = {}
	_total = 0.0
	if cycle.is_empty():
		return
	var act := []
	for w in WIRES:
		if _wire_active(w):
			act.append({"wire": w, "src": _node_at(w[1][0]), "dst": _node_at(w[1][w[1].size() - 1]),
				"pts": w[1].duplicate(), "start": -1.0, "len": _poly_len(w[1])})
	for a in act:
		_resolve(a, act, 0)
	for a in act:
		var end: float = a["start"] + a["len"]
		_total = max(_total, end)
		if a["dst"] != "":
			_arrive[a["dst"]] = max(_arrive.get(a["dst"], 0.0), end)
	_paths = act


func _resolve(a: Dictionary, act: Array, depth: int) -> float:
	if a["start"] >= 0.0:
		return a["start"]
	var src: String = a["src"]
	var start := 0.0
	if src != "" and not SEQUENTIAL.has(src) and depth < 16:
		var best: Dictionary = {}
		for f in act:
			if f != a and f["dst"] == src:
				var end: float = _resolve(f, act, depth + 1) + f["len"]
				if best.is_empty() or end > start:
					start = end
					best = f
		if src == "bus" and not best.is_empty():
			# run along the bus from where the driver meets it
			var fp: Array = best["pts"][best["pts"].size() - 1]
			var from := [fp[0], BUS_Y]
			if abs(from[0] - a["pts"][0][0]) > 0.5:
				a["pts"].push_front(from)
				a["len"] += abs(from[0] - a["pts"][1][0])
	a["start"] = start
	return start


# ── value reconstruction ─────────────────────────────────────────────────────

func _snapshot(extra: int = 0) -> Dictionary:
	var v := {}
	if not st.get("loaded", false):
		return v
	var names := ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10",
		"R11", "R12", "SP", "LR", "PC"]
	for i in 16:
		v[names[i]] = int(st["regs"][i])
	for f in ["N", "Z", "C", "V"]:
		v[f] = int(st["flags"][f])
	for k in st["datapath"]:
		v[k] = int(st["datapath"][k])
	# Undo every cycle after the one on display so the boxes show the values
	# the machine held right after that cycle.
	var hist: Array = st.get("history", [])
	if not cycle.is_empty():
		var idx := int(cycle.get("index", hist.size() - 1)) - extra
		for j in range(hist.size() - 1, idx, -1):
			for c in hist[j]["changes"]:
				if c["kind"] in ["reg", "flag", "datapath"]:
					v[c["name"]] = int(c["old"])
	return v


# ── signal matching ─────────────────────────────────────────────────────────

func _has(sig: String) -> bool:
	if cycle.is_empty():
		return false
	if sig.contains("&"):
		for part in sig.split("&"):
			if part != "" and not _has(part):
				return false
		return true
	if sig.begins_with("@"):
		return cycle.get("state", "") == sig.substr(1)
	if sig == "#WIDE":
		var top := (int(values.get("IR", 0)) >> 11) & 0x1F
		return top == 0x1D or top == 0x1E or top == 0x1F
	for s in cycle.get("signals", []):
		var ss := str(s)
		if ss == sig or (sig.ends_with("=") and ss.begins_with(sig)) or \
				(sig.contains("=") and not sig.ends_with("=") and ss.begins_with(sig)):
			return true
	return false


func _wire_active(w: Array) -> bool:
	if cycle.is_empty():
		return false
	var decode := str(cycle.get("state", "")) == "DECODE"
	if decode and w[3] != "decode":
		return false
	for sig in w[2]:
		if _has(sig):
			return true
	return false


# ── drawing helpers ─────────────────────────────────────────────────────────

func _p(x: float, y: float) -> Vector2:
	return _origin + Vector2(x, y) * _scale


func _fs(base: float) -> int:
	return max(8, int(round(base * _scale)))


func _text(pos: Vector2, s: String, size: float, col: Color, f: Font = null, center_w: float = -1.0) -> void:
	var fnt := f if f != null else font
	var fs := _fs(size)
	if center_w > 0:
		var tw := fnt.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x
		pos.x += (center_w * _scale - tw) / 2.0
	draw_string(fnt, pos, s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, col)


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


func _box_poly(r: Rect2, shape: String, orient := "") -> PackedVector2Array:
	var inset := r.size.x * 0.12
	match shape:
		"incr":
			# sideways adder: long input side, short output side
			var d := r.size.y * 0.2
			if orient == "left":
				return PackedVector2Array([Vector2(r.end.x, r.position.y), r.end,
					Vector2(r.position.x, r.end.y - d), r.position + Vector2(0, d)])
			return PackedVector2Array([r.position, r.position + Vector2(r.size.x, d),
				r.end - Vector2(0, d), Vector2(r.position.x, r.end.y)])
		"mux":
			return PackedVector2Array([r.position, r.position + Vector2(r.size.x, 0),
				r.end - Vector2(inset, 0), Vector2(r.position.x + inset, r.end.y)])
		"adder", "alu":
			var notch := r.size.x * 0.5
			return PackedVector2Array([r.position, Vector2(r.position.x + notch - 10 * _scale, r.position.y),
				Vector2(r.position.x + notch, r.position.y + r.size.y * 0.3),
				Vector2(r.position.x + notch + 10 * _scale, r.position.y), Vector2(r.end.x, r.position.y),
				r.end - Vector2(inset, 0), Vector2(r.position.x + inset, r.end.y)])
	return PackedVector2Array()


# ── main draw ───────────────────────────────────────────────────────────────

func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), C_BG)
	_scale = min(size.x / W, size.y / H)
	_origin = (size - Vector2(W, H) * _scale) / 2.0
	if not st.get("loaded", false):
		_text(_p(20, 40), "Load a program to see the datapath.", 18, C_DIM)
		return
	# Storage elements show what they hold during this cycle; what they load
	# at the edge that ends it is the "old → new" caption (from `after`).
	after = _snapshot()
	before = _snapshot(1)
	values = before

	var active_boxes := {}
	var changed_boxes := {}
	for c in cycle.get("changes", []):
		var b := _box_for_change(str(c["name"]), str(c["kind"]))
		if b != "":
			changed_boxes[b] = changed_boxes.get(b, []) + [c]

	# bus
	var bus_on: bool = cycle.get("bus") != null and _reached("bus")
	var bus_rect := Rect2(_p(20, BUS_Y - 6), Vector2(950, 12) * _scale)
	draw_rect(bus_rect, C_WIRE_ACTIVE.darkened(0.25) if bus_on else C_BUS)
	_text(_p(24, BUS_Y - 10), "BUS · 32-bit", 11, C_DIM)
	if bus_on:
		# above the bus, in the gap between the PC gate and CLR THUMB's wire
		_text(_p(432, BUS_Y - 11), "bus = " + Backend.hex32(cycle["bus"]), 13, C_CHANGED, mono)

	# wires: idle ones first, then the active ones (lit behind the pulse)
	var plan := {}
	for a in _paths:
		plan[a["wire"][0]] = a
	for w in WIRES:
		var tag: String = w[3]
		var pts := _screen(w[1])
		if not plan.has(w[0]):
			_arrow(pts, C_WIRE, 1.5 * _scale, tag == "ctrl")
		if tag.begins_with("stub:"):
			var lbl := tag.substr(5)
			var tw := font.get_string_size(lbl, HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(9)).x
			draw_string(font, pts[0] + Vector2(-tw / 2.0, -4 * _scale), lbl,
				HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(9), C_DIM)
	for a in _paths:
		_draw_flow(a)
		if _reached_wire(a, 0.0):
			for q in a["wire"][1]:
				for id in BOXES:
					if _rect(id).grow(2 * _scale).has_point(_p(q[0], q[1])) and \
							(id == a["src"] or _reached_wire(a, 1.0)):
						active_boxes[id] = true
	for a in _paths:
		_draw_pulse(a)
	for w in WIRES:
		if _selected_wire(w):
			_arrow(_screen(w[1]), C_SEL, 2.5 * _scale, w[3] == "ctrl")
	for gid in GATES:
		var gw: String = GATES[gid][0]
		_draw_gate(gid, plan.has(gw) and _reached_wire(plan[gw], 0.0))
	if selected_id == "bus":
		draw_rect(bus_rect.grow(2 * _scale), C_SEL, false, 2.5 * _scale)

	var decode := str(cycle.get("state", "")) == "DECODE"
	if _has("BranchTaken=") and _reached("cond"):
		active_boxes["cond"] = true
	if _has("ALUK=") and not decode:
		active_boxes["alu"] = true
	if _has("GateVEC"):
		active_boxes["vec"] = true
	active_boxes["fsm"] = true

	for id in BOXES:
		_draw_box(id, active_boxes.has(id), changed_boxes.get(id, []) if _reached(id) else [])
	for id in CTRLS:
		_draw_ctrl(id)
	if BOXES.has(selected_id):
		var sr := _rect(selected_id)
		var sp := _box_poly(sr, BOXES[selected_id][5], ORIENT.get(selected_id, ""))
		if sp.is_empty():
			draw_rect(sr.grow(3 * _scale), C_SEL, false, 2.5 * _scale)
		else:
			draw_polyline(sp + PackedVector2Array([sp[0]]), C_SEL, 3.0 * _scale, true)

	_text(_p(20, H - 8), "Boxes hold their values for the whole cycle · yellow old → new = loaded at the clock edge that ends it · bright = carries data · purple dashed = control · click any box or ▲ buffer",
		11, C_DIM)


func _screen(q: Array) -> PackedVector2Array:
	var pts := PackedVector2Array()
	for v in q:
		pts.append(_p(v[0], v[1]))
	return pts


## True once the pulse has covered fraction `f` of this wire.
func _reached_wire(a: Dictionary, f: float) -> bool:
	return not _animating() or _dist >= a["start"] + a["len"] * f + (0.001 if f == 0.0 else 0.0)


## Point `d` virtual units along a polyline of virtual points, in screen space.
func _along(q: Array, d: float) -> Vector2:
	for i in q.size() - 1:
		var p0 := Vector2(q[i][0], q[i][1])
		var p1 := Vector2(q[i + 1][0], q[i + 1][1])
		var l := p0.distance_to(p1)
		if d <= l or i == q.size() - 2:
			var t := clampf(d / l, 0.0, 1.0) if l > 0 else 1.0
			var v := p0.lerp(p1, t)
			return _p(v.x, v.y)
		d -= l
	return _p(q[0][0], q[0][1])


## Sub-polyline of `q` from distance d0 to d1, in screen space.
func _slice(q: Array, d0: float, d1: float) -> PackedVector2Array:
	var out := PackedVector2Array([_along(q, d0)])
	var acc := 0.0
	for i in range(1, q.size() - 1):
		acc += Vector2(q[i - 1][0], q[i - 1][1]).distance_to(Vector2(q[i][0], q[i][1]))
		if acc > d0 and acc < d1:
			out.append(_p(q[i][0], q[i][1]))
	out.append(_along(q, d1))
	return out


## The lit part of an active wire: dim ahead of the pulse, bright behind it.
func _draw_flow(a: Dictionary) -> void:
	var w: Array = a["wire"]
	var ctrl: bool = w[3] == "ctrl"
	var col := C_CTRL if ctrl else C_WIRE_ACTIVE
	var q: Array = w[1]
	var full := _screen(q)
	var l := _poly_len(q)
	# progress along the wire's own points (the bus run in front is extra)
	var d: float = (_dist - a["start"]) - (a["len"] - l)
	if not _animating() or d >= l:
		_arrow(full, col, 3.0 * _scale, ctrl)
		return
	_arrow(full, C_WIRE, 1.5 * _scale, ctrl)
	if d > 0.0:
		var lit := _slice(q, 0.0, d)
		if ctrl:
			for i in lit.size() - 1:
				draw_dashed_line(lit[i], lit[i + 1], col, 3.0 * _scale, 5.0 * _scale)
		else:
			draw_polyline(lit, col, 3.0 * _scale, true)


## The glowing head and fading tail of the pulse on this wire.
func _draw_pulse(a: Dictionary) -> void:
	if not _animating():
		return
	var d: float = _dist - a["start"]
	var l: float = a["len"]
	if d <= 0.0 or d >= l + TAIL:
		return
	var q: Array = a["pts"]
	var glow := C_GLOW_CTRL if a["wire"][3] == "ctrl" else C_GLOW
	var base := C_CTRL if a["wire"][3] == "ctrl" else C_WIRE_ACTIVE
	var head: float = min(d, l)
	var tail0: float = max(0.0, d - TAIL)
	# tail: segments that brighten and thicken toward the head
	var n := 14
	for i in n:
		var d0: float = lerp(tail0, head, float(i) / n)
		var d1: float = lerp(tail0, head, float(i + 1) / n)
		if d1 <= d0:
			continue
		var t := float(i + 1) / n
		var c := base.lerp(glow, t)
		c.a = t * 0.9
		draw_polyline(_slice(q, d0, d1), c, (2.0 + 4.0 * t) * _scale, true)
	if d >= l:
		return   # head has arrived; let the tail drain into the box
	var hp := _along(q, head)
	for r in [[13.0, 0.08], [9.0, 0.16], [6.0, 0.35], [3.5, 0.8]]:
		var c := glow
		c.a = r[1]
		draw_circle(hp, r[0] * _scale, c)
	draw_circle(hp, 2.0 * _scale, C_GLOW_HEAD)


## Geometry of a tri-state buffer, in virtual units: the triangle sits on
## its wire GATE_BACK before the bus, pointing the way data flows; its
## control input comes in horizontally to the middle of one slanted side.
func _gate_geom(gid: String) -> Dictionary:
	var pts: Array = []
	for w in WIRES:
		if w[0] == GATES[gid][0]:
			pts = w[1]
	var tip := Vector2(pts[-1][0], pts[-1][1])
	var dir := (tip - Vector2(pts[-2][0], pts[-2][1])).normalized()
	var c := tip - dir * GATE_BACK
	var n := Vector2(-dir.y, dir.x)
	var s := GATE_SIZE
	var side := Vector2(GATES[gid][3], 0)
	var tri := PackedVector2Array([c + dir * s, c - dir * s + n * s, c - dir * s - n * s])
	var s0 := c + side * (s * 0.5 + GATE_STUB)
	var s1 := c + side * (s * 0.5)
	return {"c": c, "tri": tri, "stub": [s0, s1], "side": side.x}


## Clickable area of a buffer: the triangle and its control label.
func _gate_rect(gid: String) -> Rect2:
	var g := _gate_geom(gid)
	var c: Vector2 = g["c"]
	var r := Rect2(_p(c.x - 11, c.y - 11), Vector2(22, 22) * _scale)
	var lw := font.get_string_size(GATES[gid][1], HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(9)).x
	var s0: Vector2 = g["stub"][0]
	var lx := _p(s0.x, 0).x + (3 * _scale if g["side"] > 0 else -3 * _scale - lw)
	return r.merge(Rect2(Vector2(lx, _p(0, c.y - 9).y), Vector2(lw, 14 * _scale)))


func _draw_gate(gid: String, on: bool) -> void:
	var g := _gate_geom(gid)
	var tri := PackedVector2Array()
	for q in g["tri"]:
		tri.append(_p(q.x, q.y))
	var ctrl_on := _has(GATES[gid][1])
	draw_colored_polygon(tri, C_EDGE_ACTIVE if on else C_BOX)
	draw_polyline(tri + PackedVector2Array([tri[0]]), C_EDGE_ACTIVE if on else C_EDGE, 1.0 * _scale)
	var s0: Vector2 = g["stub"][0]
	var s1: Vector2 = g["stub"][1]
	_ctrl_stub(_p(s0.x, s0.y), _p(s1.x, s1.y), ctrl_on)
	var lbl: String = GATES[gid][1]
	var lw := font.get_string_size(lbl, HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(9)).x
	var lp := _p(s0.x, s0.y + 3)
	lp.x += 3 * _scale if g["side"] > 0 else -3 * _scale - lw
	draw_string(font, lp, lbl, HORIZONTAL_ALIGNMENT_LEFT, -1, _fs(9), C_CTRL if ctrl_on else C_DIM)
	if selected_id == gid:
		draw_polyline(tri + PackedVector2Array([tri[0]]), C_SEL, 2.5 * _scale, true)
		_arrow(PackedVector2Array([_p(s0.x, s0.y), _p(s1.x, s1.y)]), C_SEL, 2.0 * _scale, true)


## A control input: short dashed purple arrow, bright when asserted.
func _ctrl_stub(a: Vector2, b: Vector2, on: bool) -> void:
	var col := C_CTRL if on else C_CTRL.lerp(C_BG, 0.55)
	_arrow(PackedVector2Array([a, b]), col, (2.2 if on else 1.2) * _scale, true)


## The value a mux / ALU / incrementer control input carries this cycle
## ("" when the FSM is not driving it).
func _ctrl_value(id: String) -> String:
	var pre: String = CTRLS[id][1]
	var sigs: Array = cycle.get("signals", [])
	if id == "sr2mux":
		if _has("SR2MUX=IMM"):
			return "IMM"
		# SR2 feeds SR2MUX unless the address adder is using it ([Rn, Rm] addressing)
		if not _has("ADDR2MUX=") and sigs.any(func(x): return str(x).begins_with("SR2=")):
			return "REG"
		return ""
	for x in sigs:
		var sx := str(x)
		if sx.begins_with(pre):
			var v := sx.substr(pre.length())
			if id == "alu":
				return "ALUK=" + v
			return "Align" if v.begins_with("Align") else v.get_slice("(", 0)
	return ""


func _draw_ctrl(id: String) -> void:
	var c: Array = CTRLS[id]
	var v := _ctrl_value(id)
	var on := v != ""
	_ctrl_stub(_p(c[0][0][0], c[0][0][1]), _p(c[0][1][0], c[0][1][1]), on)
	_text(_p(c[2][0], c[2][1]), v if on else c[3], 8.5, C_CTRL if on else C_DIM, mono if on else font)


func _rect(id: String) -> Rect2:
	var b: Array = BOXES[id]
	return Rect2(_p(b[0], b[1]), Vector2(b[2], b[3]) * _scale)


func _box_for_change(name: String, kind: String) -> String:
	match kind:
		"flag": return "flags"
		"mem", "io": return "mem"
		"datapath":
			match name:
				"MAR": return "mar"
				"MDR": return "mdr"
				"IR": return "ir"
				"IR2": return "ir2"
				"ALU_A", "ALU_B": return "alu"
		"reg":
			return "pc" if name == "PC" else "regfile"
	return ""


func _draw_box(id: String, active: bool, changes: Array) -> void:
	var b: Array = BOXES[id]
	var r := _rect(id)
	var shape: String = b[5]
	var fill := C_BOX_ACTIVE if active else C_BOX
	var edge := C_CHANGED if not changes.is_empty() else (C_EDGE_ACTIVE if active else C_EDGE)
	var ew := (2.5 if active or not changes.is_empty() else 1.0) * _scale
	var poly := _box_poly(r, shape, ORIENT.get(id, ""))
	if poly.is_empty():
		draw_rect(r, fill)
		draw_rect(r, edge, false, ew)
	else:
		draw_colored_polygon(poly, fill)
		draw_polyline(poly + PackedVector2Array([poly[0]]), edge, ew, true)

	var title_col := C_TEXT if active else C_DIM
	match shape:
		"reg":
			_text(r.position + Vector2(6, 14) * _scale, b[4], 11, title_col)
			var v := int(values.get(b[4], 0))
			var txt := Backend.hex16(v) if b[4].begins_with("IR") else Backend.hex32(v)
			_text(r.position + Vector2(6, r.size.y / _scale - 7) * _scale, txt, 14,
				C_CHANGED if not changes.is_empty() else C_TEXT, mono)
		"fsm":
			_text(r.position + Vector2(6, 16) * _scale, "FSM  (control)", 11, C_DIM)
			var state := str(cycle.get("state", st.get("next_state", "")))
			var phase := str(cycle.get("phase", ""))
			if cycle.is_empty():
				state = "next: " + str(st.get("next_state", ""))
			_text(r.position + Vector2(6, 40) * _scale, state, 13, C_TEXT, mono)
			_text(r.position + Vector2(6, 60) * _scale, phase, 11, C_CTRL)
		"regfile":
			_draw_regfile(r, changes)
		"flags":
			_text(r.position + Vector2(6, 14) * _scale, "FLAGS", 10, title_col)
			var x := 8.0
			for f in ["N", "Z", "C", "V"]:
				var hot := false
				for c in changes:
					if c["name"] == f:
						hot = true
				_text(r.position + Vector2(x, 33) * _scale, "%s%d" % [f, int(values.get(f, 0))], 13,
					C_CHANGED if hot else C_TEXT, mono)
				x += 27
			if not changes.is_empty():
				var o := ""
				var nw := ""
				for f in ["N", "Z", "C", "V"]:
					o += str(int(before.get(f, 0)))
					nw += str(int(after.get(f, 0)))
				_text(Vector2(r.position.x, r.end.y + 13 * _scale), "NZCV %s → %s" % [o, nw], 9.5, C_CHANGED, mono)
		"alu":
			var op := ""
			if str(cycle.get("state", "")) != "DECODE":
				for s in cycle.get("signals", []):
					if str(s).begins_with("ALUK="):
						op = str(s).substr(5)
			var bw: float = b[2]
			if op == "":
				_text(r.position + Vector2(0, 30) * _scale, "ALU", 13, title_col, null, bw)
			else:
				# "ALU" in the title colour, the operation in yellow, centred as one line
				var fs := _fs(13)
				var w1 := font.get_string_size("ALU  ", HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x
				var w2 := mono.get_string_size(op, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x
				var x0 := r.position.x + (r.size.x - w1 - w2) / 2.0
				draw_string(font, Vector2(x0, r.position.y + 30 * _scale), "ALU  ",
					HORIZONTAL_ALIGNMENT_LEFT, -1, fs, title_col)
				draw_string(mono, Vector2(x0 + w1, r.position.y + 30 * _scale), op,
					HORIZONTAL_ALIGNMENT_LEFT, -1, fs, C_CHANGED)
			var line := ""
			if _has("ALUK=PASS") and _has("GateALU") and cycle.get("bus") != null:
				line = "out = " + Backend.hex32(cycle["bus"])
			elif _has("@FETCH_OPERANDS") and not _has("GateALU"):
				# the input latches load these at the edge that ends this cycle
				line = _operands(cycle.get("signals", []), after, " ← ")
			elif _has("@EXECUTE_COMMIT") or (_has("@EXECUTE_PC") and _has("ALUK=")):
				# operands were latched by this instruction's FETCH_OPERANDS cycle
				for h in st.get("history", []):
					if h["state"] == "FETCH_OPERANDS" and int(h["insn_addr"]) == int(cycle.get("insn_addr", -1)):
						line = _operands(h["signals"], values, "=")
			if line != "":
				_text(r.position + Vector2(0, 45) * _scale, line, 10, C_DIM, mono, bw)
		"mem":
			_text(r.position + Vector2(8, 18) * _scale, b[4], 12, title_col)
			_text(r.position + Vector2(8, 36) * _scale, "64 KB RAM · I/O at ≥ 0x10000 (GPIO)", 10, C_DIM)
			var line := ""
			if _has("R/W=READ"):
				line = "read  M[%s]" % Backend.hex32(before.get("MAR", 0))
			elif _has("R/W=WRITE"):
				line = "write M[%s]" % Backend.hex32(before.get("MAR", 0))
			if line != "":
				_text(r.position + Vector2(8, 62) * _scale, line, 12, C_TEXT, mono)
			var y := 84.0
			for c in changes.slice(0, 3):
				var w := int(c.get("width", 4))
				var line2 := "%s ← %s" % [c["name"], _hexw(int(c["new"]), w)] if c["kind"] == "io" \
					else "%s: %s → %s" % [c["name"], _hexw(int(c["old"]), w), _hexw(int(c["new"]), w)]
				_text(r.position + Vector2(8, y) * _scale, line2, 11, C_CHANGED, mono)
				y += 18
		_:
			_text(r.position + Vector2(0, r.size.y / _scale / 2 + 5) * _scale, b[4], 11,
				title_col, null, b[2])

	# old → new caption under registers that changed at this edge
	if shape == "reg" and not changes.is_empty():
		var c: Dictionary = changes[0]
		var ir: bool = b[4].begins_with("IR")
		var o: String = Backend.hex16(c["old"]) if ir else Backend.hex32(c["old"])
		var n: String = Backend.hex16(c["new"]) if ir else Backend.hex32(c["new"])
		_text(Vector2(r.position.x, r.end.y + 13 * _scale), "%s → %s" % [o, n], 10, C_CHANGED, mono)


## "A=… B=…" for the ALU inputs a FETCH_OPERANDS cycle with these signals
## loads, read from `vals` ("A ← …" while they are still being loaded).
func _operands(sigs: Array, vals: Dictionary, op: String) -> String:
	var a := false
	var b := false
	for sg in sigs:
		var ss := str(sg)
		a = a or ss.begins_with("SR1=")
		b = b or ss.begins_with("SR2=") or ss == "SR2MUX=IMM"
	var parts := []
	if a:
		parts.append("A%s%08X" % [op, int(vals.get("ALU_A", 0))])
	if b:
		parts.append("B%s%08X" % [op, int(vals.get("ALU_B", 0))])
	return "  ".join(parts)


func _draw_regfile(r: Rect2, changes: Array) -> void:
	var active := _has("LD.REG") or _has("SR1=") or _has("SR2=") or _has("SET THUMB BIT") or _has("LD.SP")
	_text(r.position + Vector2(8, 16) * _scale, "REGISTER FILE", 11, C_TEXT if active else C_DIM)
	var hot := {}
	for c in changes:
		hot[c["name"]] = c
	var reads := {}
	for s in cycle.get("signals", []):
		var ss := str(s)
		if ss.begins_with("SR1=") or ss.begins_with("SR2=") or ss.begins_with("SR="):
			reads[ss.split("=")[1]] = true
	var names := ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
		"R8", "R9", "R10", "R11", "R12", "SP", "LR"]
	for i in names.size():
		var col_x := 8.0 if i < 8 else 118.0
		var row := i if i < 8 else i - 8
		var nm: String = names[i]
		var y := 36.0 + row * 16.0
		var colr := C_TEXT
		if hot.has(nm):
			colr = C_CHANGED
		elif reads.has(nm):
			colr = C_EDGE_ACTIVE
		_text(r.position + Vector2(col_x, y) * _scale, "%-3s %08X" % [nm, int(values.get(nm, 0))], 10.5, colr, mono)
	var y2 := 36.0 + 7 * 16.0 + 14
	for c in changes.slice(0, 1):
		_text(r.position + Vector2(118, y2) * _scale, "%s → %s" % [c["name"], Backend.hex32(c["new"])], 10, C_CHANGED, mono)


func _hexw(v: int, w: int) -> String:
	return ("0x%0" + str(w * 2) + "X") % v


func _get_tooltip(at_position: Vector2) -> String:
	for id in GATES:
		if _gate_rect(id).has_point(at_position):
			return "%s — tri-state buffer\nConnects %s to the bus while %s = 1; otherwise it is off (Z).\n(click for details)" % [
				id, BOXES[GATES[id][2]][4], GATES[id][1]]
	for id in BOXES:
		if _rect(id).has_point(at_position):
			return "%s\n%s\n(click for details)" % [BOXES[id][4], BOXES[id][6]]
	if abs(at_position.y - _p(0, BUS_Y).y) < 8 * _scale:
		return "BUS\nThe single shared 32-bit bus. At most one tri-state buffer (gatePC, gateAdder, gateALU, gateMDR, gateVector) drives it in any cycle; any register whose LD signal is asserted captures it at the clock edge.\n(click for details)"
	return ""
