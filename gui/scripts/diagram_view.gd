extends Control
## Native CPU block diagram of the multi-cycle teaching datapath.
##
## Everything is drawn in a fixed virtual canvas (W × H) and scaled to fit.
## Each wire lists the control signals that make it carry data; for the
## selected cycle the active wires and boxes light up, live values are shown
## inside the boxes, and every box the cycle changed gets an "old → new"
## caption.  Hover a box to see what it does.

const W := 1000.0
const H := 640.0
const BUS_Y := 290.0

const C_BG := Color("15171c")
const C_BOX := Color("262b34")
const C_BOX_ACTIVE := Color("2d3a52")
const C_EDGE := Color("444c59")
const C_EDGE_ACTIVE := Color("61afef")
const C_WIRE := Color("3a414d")
const C_WIRE_ACTIVE := Color("61afef")
const C_TEXT := Color("d7dae0")
const C_DIM := Color("7f8794")
const C_CHANGED := Color("f2cc60")
const C_BUS := Color("4b5363")
const C_CTRL := Color("c678dd")

## id: [x, y, w, h, label, shape, tooltip]
const BOXES := {
	"align": [30, 30, 120, 32, "PC / Align(PC,4)", "box",
		"Supplies the PC-relative base to the address adder: the PC read value (instruction address + 4), or that value rounded down to a word boundary for literal loads and ADR."],
	"addr1mux": [30, 110, 125, 30, "ADDR1MUX", "mux",
		"Chooses the address adder's base: the PC-relative value, a register (SR1) or SP."],
	"addr2mux": [175, 110, 125, 30, "ADDR2MUX", "mux",
		"Chooses the offset added to the base: a zero-extended, scaled immediate from IR, a sign-extended branch offset, a register (SR2), or zero."],
	"adder": [95, 180, 130, 36, "ADDRESS ADDER", "adder",
		"Adds base + offset to form an effective address or a branch target. It is separate from the ALU."],
	"pcmux": [360, 30, 110, 30, "PCMUX", "mux",
		"Chooses the next PC: PC + 2 (sequential), the address adder (branch target) or the bus (indirect branch, through CLR THUMB BIT)."],
	"pc": [360, 100, 110, 40, "PC", "reg",
		"Program Counter: address of the next halfword to fetch."],
	"inc": [490, 100, 50, 40, "+2", "box",
		"Incrementer: PC + 2, used during fetch to step to the next halfword."],
	"clrt": [560, 170, 90, 30, "CLR THUMB", "box",
		"Clears bit 0 of an indirect target (BX, POP {PC}) to form the halfword-aligned fetch address."],
	"ir": [665, 30, 130, 36, "IR", "reg",
		"Instruction Register: the 16-bit instruction (or first halfword) being executed. Its bits drive the FSM."],
	"ir2": [815, 30, 130, 36, "IR2", "reg",
		"Second instruction register: holds the second halfword of a 32-bit encoding such as BL."],
	"fsm": [665, 100, 145, 72, "FSM", "fsm",
		"Finite State Machine: the control unit. Each clock it moves to the next state and asserts that state's control signals (shown in the cycle panel)."],
	"ext": [830, 100, 115, 34, "IMM / OFFSET", "box",
		"Extracts immediates and offsets from IR/IR2 and zero- or sign-extends and scales them."],
	"cond": [830, 170, 115, 34, "COND EVAL", "box",
		"Evaluates a branch condition (IR[11:8]) against the NZCV flags and produces BranchTaken, which steers PCMUX."],
	"vec": [665, 205, 110, 30, "VECTOR ADDR", "box",
		"Supplies the handler address on exception entry (SVC)."],
	"mar": [30, 340, 115, 40, "MAR", "reg",
		"Memory Address Register: the address for the next memory or I/O access."],
	"mem": [30, 430, 230, 150, "MEMORY / I-O", "mem",
		"64 KB RAM plus memory-mapped I/O (GPIO). Reads fill MDR; writes take their data from MDR."],
	"mdr": [290, 430, 115, 40, "MDR", "reg",
		"Memory Data Register: data just read from memory, or data about to be written."],
	"loadext": [290, 340, 115, 32, "LOAD EXT", "box",
		"Selects the byte/halfword lanes of a loaded value and zero- or sign-extends it to 32 bits."],
	"storealign": [425, 340, 115, 32, "STORE ALIGN", "box",
		"Places store data in the correct byte lanes and sets the byte enables for STRB/STRH."],
	"regfile": [560, 325, 230, 165, "REGISTER FILE", "regfile",
		"R0–R12, SP and LR. Two read ports (SR1, SR2) feed the ALU; the write port (DR) is loaded from the bus when LD.REG is asserted."],
	"sett": [825, 325, 115, 30, "SET THUMB", "box",
		"Sets bit 0 of a return address before it is written to LR (BL, BLX)."],
	"flags": [825, 420, 115, 40, "N Z C V", "flags",
		"Condition flags, loaded from the ALU when LD.CC is asserted (instructions ending in S, and CMP/CMN/TST)."],
	"sr2mux": [650, 505, 95, 26, "SR2MUX", "mux",
		"Chooses the ALU's B input: the second register read port (SR2) or an immediate from IR."],
	"alu": [560, 548, 185, 52, "ALU", "alu",
		"Arithmetic Logic Unit: ADD, SUB, AND, ORR, EOR, shifts, MUL… or PASS (forwards a register, e.g. store data)."],
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
	["ir_fsm", [[730, 66], [730, 100]], ["@DECODE", "@FETCH_IR", "@FETCH2_IR"], "decode"],
	["ir_ext", [[785, 66], [785, 84], [860, 84], [860, 100]], ["@DECODE", "SR2MUX=IMM", "ADDR2MUX=Z", "ADDR2MUX=S"], "decode"],
	["ir2_ext", [[900, 66], [900, 100]], ["ADDR2MUX=ZEXT(IR2"], ""],
	["pc_inc", [[470, 120], [490, 120]], ["PCINC=+2"], ""],
	["inc_pcmux", [[515, 100], [515, 18], [440, 18], [440, 30]], ["PCMUX=PC+2"], ""],
	["pcmux_pc", [[415, 60], [415, 100]], ["LD.PC"], ""],
	["adder_pcmux", [[225, 198], [322, 198], [322, 12], [392, 12], [392, 30]], ["PCMUX=ADDER"], ""],
	["bus_clrt", [[605, BUS_Y], [605, 200]], ["PCMUX=BUS"], ""],
	["clrt_pcmux", [[605, 170], [605, 6], [458, 6], [458, 30]], ["PCMUX=BUS"], ""],
	["pc_align", [[360, 112], [345, 112], [345, 46], [150, 46]], ["ADDR1MUX=PC", "ADDR1MUX=Align"], ""],
	["align_a1", [[62, 62], [62, 110]], ["ADDR1MUX=PC", "ADDR1MUX=Align"], ""],
	["reg_a1", [[4, 125], [30, 125]], ["ADDR1MUX=SR1", "ADDR1MUX=SP"], "stub:SR1/SP"],
	["off_a2", [[238, 84], [238, 110]], ["ADDR2MUX=Z", "ADDR2MUX=S", "ADDR2MUX=-", "ADDR2MUX=SR2"], "stub:offset"],
	["a1_adder", [[92, 140], [130, 180]], ["ADDR1MUX="], ""],
	["a2_adder", [[238, 140], [190, 180]], ["ADDR2MUX="], ""],
	["adder_bus", [[160, 216], [160, BUS_Y]], ["GateADDR"], "gate"],
	["bus_reg", [[610, BUS_Y], [610, 325]], ["LD.REG", "LD.SP"], ""],
	["bus_sett", [[882, BUS_Y], [882, 325]], ["SET THUMB BIT"], ""],
	["sett_reg", [[825, 340], [790, 340]], ["SET THUMB BIT"], ""],
	["reg_alua", [[600, 490], [600, 548]], ["SR1=&@FETCH_OPERANDS", "GateALU"], ""],
	["reg_sr2", [[685, 490], [685, 505]], ["SR2=&@FETCH_OPERANDS"], ""],
	["ext_sr2", [[945, 117], [978, 117], [978, 518], [745, 518]], ["SR2MUX=IMM&@FETCH_OPERANDS"], ""],
	["sr2_alub", [[698, 531], [698, 548]], ["SR2=&@FETCH_OPERANDS", "SR2MUX=IMM&@FETCH_OPERANDS"], ""],
	["alu_bus", [[745, 568], [805, 568], [805, BUS_Y]], ["GateALU"], "gate"],
	["alu_flags", [[745, 588], [882, 588], [882, 460]], ["LD.CC"], ""],
	["flags_cond", [[940, 440], [968, 440], [968, 187], [945, 187]], ["COND=", "BranchTaken="], "ctrl"],
	["cond_pcmux", [[830, 196], [812, 196], [812, 182], [480, 182], [480, 45], [470, 45]], ["BranchTaken="], "ctrl"],
	["vec_bus", [[720, 235], [720, BUS_Y]], ["GateVEC"], "gate"],
]

var cycle: Dictionary = {}
var st: Dictionary = {}
var values: Dictionary = {}      # register/datapath values after the shown cycle
var font: Font
var mono: Font
var _scale := 1.0
var _origin := Vector2.ZERO


func _ready() -> void:
	font = get_theme_default_font()
	mono = Backend.mono_font()
	clip_contents = true
	mouse_filter = Control.MOUSE_FILTER_STOP
	tooltip_text = " "
	resized.connect(queue_redraw)


func update_state(s: Dictionary) -> void:
	st = s
	if not s.get("loaded", false):
		return
	queue_redraw()


func show_cycle(info: Dictionary) -> void:
	cycle = info
	queue_redraw()


# ── value reconstruction ─────────────────────────────────────────────────────

func _snapshot() -> Dictionary:
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
		var idx := int(cycle.get("index", hist.size() - 1))
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


func _box_poly(r: Rect2, shape: String) -> PackedVector2Array:
	var inset := r.size.x * 0.12
	match shape:
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
	values = _snapshot()

	var active_boxes := {}
	var changed_boxes := {}
	for c in cycle.get("changes", []):
		var b := _box_for_change(str(c["name"]), str(c["kind"]))
		if b != "":
			changed_boxes[b] = changed_boxes.get(b, []) + [c]

	# bus
	var bus_on := cycle.get("bus") != null
	var bus_rect := Rect2(_p(20, BUS_Y - 6), Vector2(950, 12) * _scale)
	draw_rect(bus_rect, C_WIRE_ACTIVE.darkened(0.25) if bus_on else C_BUS)
	_text(_p(24, BUS_Y - 10), "BUS (32-bit, one driver per cycle)", 11, C_DIM)
	if bus_on:
		var label := "bus = " + Backend.hex32(cycle["bus"])
		_text(_p(460, BUS_Y + 22), label, 14, C_CHANGED, mono)

	# wires
	for w in WIRES:
		var on := _wire_active(w)
		var pts := PackedVector2Array()
		for q in w[1]:
			pts.append(_p(q[0], q[1]))
		var tag: String = w[3]
		var ctrl := tag == "ctrl"
		var col := (C_CTRL if ctrl else C_WIRE_ACTIVE) if on else C_WIRE
		_arrow(pts, col, (3.0 if on else 1.5) * _scale, ctrl)
		if tag == "gate":
			_gate(pts, on)
		if tag.begins_with("stub:"):
			var lbl := tag.substr(5)
			_text(pts[0] + Vector2(0, -8) * _scale, lbl, 9, C_DIM)
		if on:
			for q in w[1]:
				for id in BOXES:
					if _rect(id).grow(2 * _scale).has_point(_p(q[0], q[1])):
						active_boxes[id] = true

	if _has("COND=") or _has("BranchTaken="):
		active_boxes["cond"] = true
	if _has("ALUK="):
		active_boxes["alu"] = true
	if _has("GateVEC"):
		active_boxes["vec"] = true
	active_boxes["fsm"] = true

	for id in BOXES:
		_draw_box(id, active_boxes.has(id), changed_boxes.get(id, []))

	_text(_p(20, H - 8), "Bright wires carry data this cycle · purple dashed = control · yellow = changed at this clock edge · hover a box to learn what it does",
		11, C_DIM)


func _gate(pts: PackedVector2Array, on: bool) -> void:
	# Tri-state gate: a small triangle just above the bus.
	var tip := pts[pts.size() - 1]
	var dir := (tip - pts[pts.size() - 2]).normalized()
	var c := tip - dir * 26.0 * _scale
	var n := Vector2(-dir.y, dir.x)
	var s := 8.0 * _scale
	var tri := PackedVector2Array([c + dir * s, c - dir * s + n * s, c - dir * s - n * s])
	draw_colored_polygon(tri, C_EDGE_ACTIVE if on else C_BOX)
	draw_polyline(tri + PackedVector2Array([tri[0]]), C_EDGE_ACTIVE if on else C_EDGE, 1.0 * _scale)


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
	var poly := _box_poly(r, shape)
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
		"alu":
			_text(r.position + Vector2(10, 24) * _scale, "ALU", 13, title_col)
			var op := ""
			for s in cycle.get("signals", []):
				if str(s).begins_with("ALUK="):
					op = str(s).substr(5)
			if op != "":
				_text(r.position + Vector2(118, 24) * _scale, op, 13, C_CHANGED, mono)
			if _has("@FETCH_OPERANDS") or _has("@EXECUTE_COMMIT"):
				_text(r.position + Vector2(22, 45) * _scale, "A=%s  B=%s" % [
					Backend.hex32(values.get("ALU_A", 0)), Backend.hex32(values.get("ALU_B", 0))], 11, C_DIM, mono)
		"mem":
			_text(r.position + Vector2(8, 18) * _scale, b[4], 12, title_col)
			_text(r.position + Vector2(8, 36) * _scale, "64 KB RAM · GPIO at 0xD0000000", 10, C_DIM)
			var line := ""
			if _has("R/W=READ"):
				line = "read  M[%s]" % Backend.hex32(values.get("MAR", 0))
			elif _has("R/W=WRITE"):
				line = "write M[%s]" % Backend.hex32(values.get("MAR", 0))
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
		_text(r.position + Vector2(118, y2) * _scale, "%s ← %s" % [c["name"], Backend.hex32(c["new"])], 10, C_CHANGED, mono)


func _hexw(v: int, w: int) -> String:
	return ("0x%0" + str(w * 2) + "X") % v


func _get_tooltip(at_position: Vector2) -> String:
	for id in BOXES:
		if _rect(id).has_point(at_position):
			return "%s\n%s" % [BOXES[id][4], BOXES[id][6]]
	if abs(at_position.y - _p(0, BUS_Y).y) < 8 * _scale:
		return "BUS\nThe single shared 32-bit bus. Exactly one tri-state gate (GatePC, GateADDR, GateALU, GateMDR, …) drives it in any cycle; any register whose LD signal is asserted captures it at the clock edge."
	return ""
