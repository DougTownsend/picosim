extends Control
## Raspberry Pi Pico board, drawn natively to scale (51 × 21 mm) and laid on
## its side with the micro-USB connector at the left. Header pins 1–20 run
## along the bottom edge and 21–40 along the top, as on the real board, with
## castellated edge pads, the RP2040, QSPI flash, crystal, regulator, BOOTSEL
## button, LED, mounting holes and SWD pads in their real places.
##
## Each GPIO pad shows the pin's live logic level: a bright "1" when high, a
## dim "0" when an output or pulled pin is low, and nothing when floating.
## Pins changed by the last step get a yellow ring. GP25 lights the on-board
## LED. Click an input pin to drive it: Z → 1 → 0 → Z.
## The ⛶ button in the corner (or Esc to leave) toggles a full-window view.

signal fullscreen_toggled(on: bool)

const W := 1000.0
const H := 545.0
const MM := 15.75                       # canvas units per millimetre (2.54 mm = 40)
const BOARD := Rect2(98, 120, 803, 330)  # 51 mm × 21 mm
const PITCH := 40.0
const X0 := 120.0                       # x of pin 1 / pin 40
const EDGE_TOP := 120.0
const EDGE_BOT := 450.0
const TOP_Y := 145.0                    # through-hole centres, 17.78 mm apart
const BOT_Y := 425.0
const CY := 285.0                       # board centre line

## Physical header pins: number -> GPIO index, or a power/control name.
## Pins 1–20 are along the bottom edge; 40–21 along the top, left to right.
const PINS := {
	1: 0, 2: 1, 3: "GND", 4: 2, 5: 3, 6: 4, 7: 5, 8: "GND", 9: 6, 10: 7,
	11: 8, 12: 9, 13: "GND", 14: 10, 15: 11, 16: 12, 17: 13, 18: "GND", 19: 14, 20: 15,
	21: 16, 22: 17, 23: "GND", 24: 18, 25: 19, 26: 20, 27: 21, 28: "GND", 29: 22, 30: "RUN",
	31: 26, 32: 27, 33: "AGND", 34: 28, 35: "VREF", 36: "3V3", 37: "3V3E", 38: "GND",
	39: "VSYS", 40: "VBUS",
}
const PIN_TIPS := {
	"GND": "Ground", "AGND": "Analog ground", "RUN": "RUN — pull low to reset the RP2040",
	"VREF": "ADC_VREF — ADC reference voltage", "3V3": "3V3(OUT) — 3.3 V supply output",
	"3V3E": "3V3_EN — pull low to turn off the 3.3 V regulator",
	"VSYS": "VSYS — main system input, 1.8–5.5 V", "VBUS": "VBUS — 5 V from the USB connector",
}

const C_PCB := Color("1d7a40")
const C_PCB_DARK := Color("145a2f")
const C_PCB_LIGHT := Color("2a9152")
const C_TRACE := Color(0.45, 0.85, 0.55, 0.18)
const C_SILK := Color(0.93, 0.96, 0.93, 0.9)
const C_GOLD := Color("d4a93a")
const C_GOLD_DARK := Color("9c7a22")
const C_HOLE := Color("0b1510")
const C_CHIP := Color("1b1c1f")
const C_METAL := Color("c3c8cf")
const C_METAL_DARK := Color("8b929c")
const C_HIGH := Color("39d353")
const C_LOW := Color("5b6472")
const C_IN := Color("61afef")
const C_CHANGED := Color("f2cc60")
const C_CHIP_TEXT := Color("7f8794")
# Colours of things drawn on the board stay fixed; text and the background
# around it follow the colour scheme (set in _load_colors).
var C_BG: Color
var C_TEXT: Color
var C_DIM: Color

## Small passives (x, y, w, h in canvas units) scattered as on the real board.
const PASSIVES := [
	[276, 200, 14, 8], [276, 214, 14, 8], [228, 330, 8, 14], [242, 330, 8, 14],
	[300, 206, 14, 8], [300, 360, 14, 8], [322, 360, 14, 8], [420, 212, 8, 14],
	[434, 212, 8, 14], [448, 212, 8, 14], [590, 212, 8, 14], [604, 212, 8, 14],
	[420, 350, 8, 14], [434, 350, 8, 14], [590, 350, 8, 14], [604, 350, 8, 14],
	[618, 350, 8, 14], [690, 250, 14, 8], [690, 318, 14, 8], [740, 232, 8, 14],
	[754, 232, 8, 14], [740, 330, 8, 14], [790, 280, 14, 8], [375, 300, 8, 14],
]

var st: Dictionary = {}
var font: Font
var mono: Font
var _scale := 1.0
var _origin := Vector2.ZERO
var btn_full: Button


func _ready() -> void:
	font = get_theme_default_font()
	mono = Backend.mono_font()
	clip_contents = true
	mouse_filter = Control.MOUSE_FILTER_STOP
	tooltip_text = " "
	resized.connect(queue_redraw)
	_load_colors()
	Palette.changed.connect(_load_colors)

	btn_full = Button.new()
	btn_full.text = "⛶ Enlarge"
	btn_full.toggle_mode = true
	btn_full.focus_mode = Control.FOCUS_NONE
	btn_full.tooltip_text = "Show the board full-window (Esc to return)"
	btn_full.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT, Control.PRESET_MODE_MINSIZE, 6)
	btn_full.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	btn_full.toggled.connect(_on_full_toggled)
	add_child(btn_full)


func _load_colors() -> void:
	C_BG = Palette.c("surface")
	C_TEXT = Palette.c("text")
	C_DIM = Palette.c("dim")
	queue_redraw()


func set_fullscreen(on: bool) -> void:
	btn_full.button_pressed = on


func _on_full_toggled(on: bool) -> void:
	btn_full.text = "✕ Close" if on else "⛶ Enlarge"
	fullscreen_toggled.emit(on)


func update_state(s: Dictionary) -> void:
	st = s
	queue_redraw()


# ── geometry ────────────────────────────────────────────────────────────────

func _p(x: float, y: float) -> Vector2:
	return _origin + Vector2(x, y) * _scale


func _r(x: float, y: float, w: float, h: float) -> Rect2:
	return Rect2(_p(x, y), Vector2(w, h) * _scale)


func _fs(base: float) -> int:
	return max(7, int(round(base * _scale)))


func _pin_pos(num: int) -> Vector2:
	if num <= 20:
		return Vector2(X0 + (num - 1) * PITCH, BOT_Y)
	return Vector2(X0 + (40 - num) * PITCH, TOP_Y)


func _gpio(i: int) -> Dictionary:
	var pins: Array = st.get("gpio", [])
	return pins[i] if i < pins.size() else {}


func _text(pos: Vector2, s: String, size: float, col: Color, f: Font = null, center := false) -> void:
	var fnt := f if f != null else font
	var fs := _fs(size)
	if center:
		pos.x -= fnt.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x / 2.0
	draw_string(fnt, pos, s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, col)


func _box(rect: Rect2, bg: Color, radius := 0.0, border := Color.TRANSPARENT, bw := 0.0,
		shadow := 0.0) -> void:
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	sb.set_corner_radius_all(int(radius * _scale))
	if bw > 0.0:
		sb.border_color = border
		sb.set_border_width_all(max(1, int(bw * _scale)))
	if shadow > 0.0:
		sb.shadow_color = Color(0, 0, 0, 0.55)
		sb.shadow_size = int(shadow * _scale)
		sb.shadow_offset = Vector2(0, shadow * 0.5 * _scale)
	sb.anti_aliasing = true
	draw_style_box(sb, rect)


func _half_disc(center: Vector2, radius: float, up: bool, col: Color) -> void:
	# Half of a disc on the board side of an edge (castellation plating/notch).
	var pts := PackedVector2Array()
	for k in 17:
		var a := PI * k / 16.0
		pts.append(center + Vector2(cos(a), sin(a) * (-1.0 if up else 1.0)) * radius)
	draw_colored_polygon(pts, col)


# ── drawing ─────────────────────────────────────────────────────────────────

func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), C_BG)
	_scale = min(size.x / W, size.y / H)
	_origin = (size - Vector2(W, H) * _scale) / 2.0

	_text(_p(W / 2, 36), "Raspberry Pi Pico", 34, C_TEXT, null, true)
	_text(_p(W / 2, 58), "live GPIO levels · click an input pin to drive it (Z → 1 → 0)", 13, C_DIM, null, true)

	_draw_pcb()
	_draw_traces()
	_draw_silkscreen()
	_draw_components()

	var changed := {}
	for c in st.get("gpio_changed", []):
		changed[int(c)] = true
	for num in range(1, 41):
		_draw_pin(num, changed)

	_draw_usb()
	_draw_io_caption()
	_draw_legend()


func _draw_pcb() -> void:
	var br := _r(BOARD.position.x, BOARD.position.y, BOARD.size.x, BOARD.size.y)
	_box(br, C_PCB, 16, C_PCB_DARK, 2.5, 14)
	# soft top-light bevel
	_box(_r(BOARD.position.x + 4, BOARD.position.y + 3, BOARD.size.x - 8, 10),
		Color(1, 1, 1, 0.05), 8)
	# mounting holes: 2.1 mm, 2 mm from each end, 11.4 mm apart
	for hx in [BOARD.position.x + 2.0 * MM, BOARD.end.x - 2.0 * MM]:
		for hy in [CY - 5.7 * MM, CY + 5.7 * MM]:
			draw_circle(_p(hx, hy), 21 * _scale, C_GOLD)           # plated ring
			draw_circle(_p(hx, hy), 18 * _scale, C_GOLD_DARK)
			draw_circle(_p(hx, hy), 1.05 * MM * _scale, C_BG)       # 2.1 mm drill


func _draw_traces() -> void:
	# Faint copper traces from each GPIO pad toward the RP2040.
	var chip_c := Vector2(520, CY)
	for num in range(1, 41):
		if typeof(PINS[num]) != TYPE_INT:
			continue
		var pos := _pin_pos(num)
		var top := num > 20
		var y1 := pos.y + (30.0 if top else -30.0)
		var target := chip_c + Vector2(clampf((pos.x - chip_c.x) * 0.12, -48, 48), -60.0 if top else 60.0)
		var mid_y := (y1 + target.y) / 2.0
		var pts := PackedVector2Array([_p(pos.x, pos.y), _p(pos.x, y1), _p(pos.x + (target.x - pos.x) * 0.35, mid_y),
			_p(target.x, target.y)])
		draw_polyline(pts, C_TRACE, 3.0 * _scale, true)


func _draw_silkscreen() -> void:
	# pin numbers beside each through-hole
	for num in range(1, 41):
		var pos := _pin_pos(num)
		_text(_p(pos.x, pos.y + (32 if num > 20 else -22)), str(num), 8, C_SILK, null, true)
	# board name and year near the SWD end
	_text(_p(772, 360), "Raspberry Pi Pico", 12, C_SILK, null, true)
	_text(_p(772, 377), "© 2020", 9, C_SILK, null, true)
	# component outlines
	draw_rect(_r(226, 240, 48, 52), C_SILK, false, 1.0 * _scale)


func _draw_components() -> void:
	# passives: tan bodies with tinned ends
	for p in PASSIVES:
		var r := _r(p[0], p[1], p[2], p[3])
		draw_rect(r, Color("b89a6a"))
		if p[2] > p[3]:
			draw_rect(Rect2(r.position, Vector2(r.size.x * 0.25, r.size.y)), C_METAL)
			draw_rect(Rect2(r.position + Vector2(r.size.x * 0.75, 0), Vector2(r.size.x * 0.25, r.size.y)), C_METAL)
		else:
			draw_rect(Rect2(r.position, Vector2(r.size.x, r.size.y * 0.25)), C_METAL)
			draw_rect(Rect2(r.position + Vector2(0, r.size.y * 0.75), Vector2(r.size.x, r.size.y * 0.25)), C_METAL)

	# buck-boost regulator (RT6150) and its inductor, by VBUS/VSYS
	_box(_r(172, 196, 34, 28), C_CHIP, 2, Color("2c2e33"), 1, 3)
	_text(_p(189, 214), "RT6150", 6.5, C_CHIP_TEXT, null, true)
	_box(_r(220, 190, 42, 38), Color("3a3d42"), 5, Color("55595f"), 1.5, 3)
	_text(_p(241, 214), "2R2", 8, Color("9aa0a8"), null, true)

	# BOOTSEL button: white housing with a round actuator
	_box(_r(228, 242, 44, 48), Color("f1f1ee"), 4, Color("c9c9c4"), 1.5, 4)
	draw_circle(_p(250, 266), 13 * _scale, Color("dcdcd7"))
	draw_circle(_p(250, 266), 10 * _scale, Color("e9e9e4"))
	_text(_p(250, 306), "BOOTSEL", 8, C_SILK, null, true)

	# on-board LED (GP25), next to the USB connector
	var led := _gpio(25)
	var lit: bool = led.get("level") != null and int(led["level"]) == 1
	if lit:
		draw_circle(_p(190, 352), 26 * _scale, Color(0.22, 0.95, 0.35, 0.18))
		draw_circle(_p(190, 352), 15 * _scale, Color(0.22, 0.95, 0.35, 0.35))
	_box(_r(182, 346, 16, 12), C_HIGH if lit else Color("e7dfc4"), 2, Color("9e977f"), 1)
	_text(_p(190, 374), "LED", 8, C_SILK, null, true)
	if lit:
		_text(_p(190, 339), "1", 13, C_HIGH, mono, true)

	# QSPI flash (W25Q16JV, SOIC-8) with gull-wing leads
	for k in 4:
		draw_rect(_r(356 + k * 16, 196, 6, 10), C_METAL)
		draw_rect(_r(356 + k * 16, 262, 6, 10), C_METAL)
	_box(_r(348, 204, 70, 60), C_CHIP, 3, Color("2c2e33"), 1, 4)
	draw_circle(_p(356, 213), 2.5 * _scale, Color("3a3c41"))
	_text(_p(383, 232), "W25Q16JV", 8, Color("8c9098"), null, true)
	_text(_p(383, 246), "IQ", 7, Color("6d7179"), null, true)

	# RP2040 (7 × 7 mm QFN-56) with perimeter pads
	var cx := 520.0
	var s := 7.0 * MM
	var x0 := cx - s / 2
	var y0 := CY - s / 2
	for k in 14:
		var tx := x0 + 8 + k * (s - 16) / 13.0
		var ty := y0 + 8 + k * (s - 16) / 13.0
		draw_rect(_r(tx - 1.5, y0 - 4, 3, 5), C_METAL_DARK)
		draw_rect(_r(tx - 1.5, y0 + s - 1, 3, 5), C_METAL_DARK)
		draw_rect(_r(x0 - 4, ty - 1.5, 5, 3), C_METAL_DARK)
		draw_rect(_r(x0 + s - 1, ty - 1.5, 5, 3), C_METAL_DARK)
	_box(_r(x0, y0, s, s), C_CHIP, 4, Color("2f3136"), 1.5, 6)
	draw_circle(_p(x0 + 10, y0 + 10), 3 * _scale, Color("34363b"))
	_draw_raspberry(Vector2(cx, y0 + 30), 0.9)
	_text(_p(cx, y0 + 64), "RP2040", 13, Color("c7cad0"), null, true)
	_text(_p(cx, y0 + 80), "RP2-B2", 8, Color("80848c"), null, true)
	_text(_p(cx, y0 + 93), "Cortex-M0+", 7, Color("6d7179"), null, true)

	# 12 MHz crystal
	_box(_r(634, 300, 54, 40), C_METAL, 5, C_METAL_DARK, 1.5, 3)
	_text(_p(661, 324), "12.000", 8, Color("5a6068"), null, true)

	# SWD debug pads at the far end
	var names := ["SWCLK", "GND", "SWDIO"]
	for k in 3:
		var py := CY - 40 + k * 40
		draw_circle(_p(866, py), 9 * _scale, C_GOLD)
		draw_circle(_p(866, py), 4 * _scale, C_GOLD_DARK)
		_text(_p(821, py + 4), names[k], 7, C_SILK)
	_text(_p(866, CY - 58), "DEBUG", 7, C_SILK, null, true)


func _draw_raspberry(c: Vector2, k: float) -> void:
	# Tiny raspberry logo etched on the RP2040 lid.
	var col := Color("55585e")
	var leaf := Color("4a4d52")
	draw_circle(_p(c.x - 6 * k, c.y - 10 * k), 4 * k * _scale, leaf)
	draw_circle(_p(c.x + 6 * k, c.y - 10 * k), 4 * k * _scale, leaf)
	for off in [Vector2(0, 0), Vector2(-7, 2), Vector2(7, 2), Vector2(-4, 8), Vector2(4, 8), Vector2(0, 13)]:
		draw_circle(_p(c.x + off.x * k, c.y + off.y * k - 2), 4.2 * k * _scale, col)


func _draw_usb() -> void:
	# Micro-USB B receptacle overhanging the left edge (8 × 5.5 mm).
	var r := _r(72, CY - 63, 92, 126)
	_box(r, C_METAL, 6, C_METAL_DARK, 2, 8)
	_box(_r(76, CY - 57, 84, 12), Color(1, 1, 1, 0.25), 4)        # highlight
	_box(_r(72, CY - 34, 14, 68), Color("3a3f46"), 2)              # port opening
	draw_rect(_r(74, CY - 20, 8, 40), Color("1e2227"))
	for k in 5:                                                     # solder tabs
		draw_rect(_r(150, CY - 44 + k * 22, 12, 6), C_METAL_DARK)


func _draw_pin(num: int, changed: Dictionary) -> void:
	var pos := _pin_pos(num)
	var top := num > 20
	var edge := EDGE_TOP if top else EDGE_BOT
	var c := _p(pos.x, pos.y)
	var info = PINS[num]
	var r := 11.0 * _scale

	# pad: castellated half-hole at the edge joined to the through-hole
	var pad := _r(pos.x - 11, minf(edge, pos.y), 22, absf(pos.y - edge))
	draw_rect(pad, C_GOLD)
	_half_disc(_p(pos.x, edge), 11 * _scale, not top, C_GOLD)
	_half_disc(_p(pos.x, edge), 5.5 * _scale, not top, C_BG)
	draw_circle(c, r, C_GOLD)
	draw_arc(c, r, 0, TAU, 24, C_GOLD_DARK, 1.2 * _scale)
	draw_circle(c, r * 0.5, C_HOLE)

	var label_y := edge - 12 if top else edge + 24
	if typeof(info) == TYPE_STRING:
		var col := C_DIM
		if info == "GND" or info == "AGND":
			col = Palette.c("gnd")
		elif info.begins_with("3V3") or info.begins_with("V"):
			col = Palette.c("red")
		_text(_p(pos.x, label_y), info, 9.5, col, null, true)
		return

	var gp: int = info
	var g := _gpio(gp)
	var level = g.get("level")
	var is_out: bool = g.get("dir", "in") == "out"
	_text(_p(pos.x, label_y), "GP%d" % gp, 10.5, C_TEXT if level != null else C_DIM, null, true)
	var tag := "out" if is_out else ("in" if level != null else "")
	if tag != "":
		_text(_p(pos.x, label_y + (-12 if top else 13)), tag, 8, Palette.c("accent") if not is_out else C_DIM, null, true)

	# live value badge on the pad
	if level != null:
		var high := int(level) == 1
		var fill := C_HIGH if high else Color("2a2f38")
		if not is_out and high:
			fill = C_IN
		draw_circle(c, r * 1.15, fill)
		draw_arc(c, r * 1.15, 0, TAU, 24, C_HIGH if high and is_out else (C_IN if not is_out else C_LOW), 1.5 * _scale)
		_text(c + Vector2(0, 5.5 * _scale), "1" if high else "0", 15.0 if high else 11.0,
			Color("0b1a0f") if high else C_LOW, mono, true)
	if changed.has(gp):
		draw_arc(c, r * 1.7, 0, TAU, 32, C_CHANGED, 2.5 * _scale)


func _draw_io_caption() -> void:
	# Most recent I/O write of the current instruction, decoded.
	var hist: Array = st.get("history", [])
	var text := ""
	for i in range(hist.size() - 1, -1, -1):
		for ch in hist[i]["changes"]:
			if ch["kind"] == "io":
				text = "%s  ←  %s" % [_io_name(int(ch["addr"])), Backend.hex32(ch["new"])]
				break
		if text != "":
			break
	var changed: Array = st.get("gpio_changed", [])
	var parts := []
	for gp in changed:
		var g := _gpio(int(gp))
		var lv = g.get("level")
		parts.append("GP%d → %s" % [int(gp), "Z" if lv == null else str(int(lv))])
	var y := 512.0
	if text != "":
		_text(_p(12, y), "Last I/O write:  " + text, 12, C_TEXT, mono)
		y += 20
	if not parts.is_empty():
		_text(_p(12, y), "Changed by the last step:  " + ", ".join(parts), 12, Palette.c("changed"), mono)


func _draw_legend() -> void:
	var y := 80.0
	var x := 330.0
	for item in [[C_HIGH, "output 1"], [Color("2a2f38"), "0"], [C_IN, "input 1"], [C_CHANGED, "just changed"]]:
		draw_circle(_p(x, y - 4), 6 * _scale, item[0])
		_text(_p(x + 11, y), item[1], 10, C_DIM)
		x += 95


static func _io_name(addr: int) -> String:
	const SIO := {0x04: "GPIO_IN", 0x10: "GPIO_OUT", 0x14: "GPIO_OUT_SET", 0x18: "GPIO_OUT_CLR",
		0x1C: "GPIO_OUT_XOR", 0x20: "GPIO_OE", 0x24: "GPIO_OE_SET", 0x28: "GPIO_OE_CLR", 0x2C: "GPIO_OE_XOR"}
	if addr >= 0xD0000000 and addr < 0xD0000040:
		return "SIO " + SIO.get(addr - 0xD0000000, "0x%X" % (addr - 0xD0000000))
	if addr >= 0x40014000 and addr < 0x40014800:
		var off := addr - 0x40014000
		return "IO_BANK0 GPIO%d_%s" % [off >> 3, "CTRL" if (off & 7) == 4 else "STATUS"]
	if addr >= 0x4001C000 and addr < 0x4001C100:
		return "PADS_BANK0 " + ("VOLTAGE_SELECT" if addr == 0x4001C000 else "GPIO%d" % ((addr - 0x4001C004) >> 2))
	if addr >= 0x4000C000 and addr < 0x4000C100:
		return "RESETS +0x%X" % (addr - 0x4000C000)
	return Backend.hex32(addr)


# ── interaction ─────────────────────────────────────────────────────────────

func _hit_pin(at: Vector2) -> int:
	for num in range(1, 41):
		var pos := _pin_pos(num)
		if _p(pos.x, pos.y).distance_to(at) <= 14 * _scale:
			return num
	return -1


func _gui_input(event: InputEvent) -> void:
	var mb := event as InputEventMouseButton
	if mb == null or not mb.pressed or mb.button_index != MOUSE_BUTTON_LEFT:
		return
	var num := _hit_pin(mb.position)
	if num < 0 or typeof(PINS[num]) != TYPE_INT:
		return
	var gp: int = PINS[num]
	var g := _gpio(gp)
	if g.get("dir", "in") == "out":
		return
	# cycle the external drive: floating → 1 → 0 → floating
	var nxt = 1
	if g.get("driven", false):
		nxt = 0 if int(g.get("level", 0)) == 1 else null
	Backend.send("gpio_set", {"pin": gp, "value": nxt})
	accept_event()


## Simulate a click on header pin `num` (used by the --demo-click-pin test hook).
func demo_click(num: int) -> void:
	var ev := InputEventMouseButton.new()
	ev.button_index = MOUSE_BUTTON_LEFT
	ev.pressed = true
	var pos := _pin_pos(num)
	ev.position = _p(pos.x, pos.y)
	_gui_input(ev)


func _get_tooltip(at_position: Vector2) -> String:
	var num := _hit_pin(at_position)
	if num < 0:
		return ""
	var info = PINS[num]
	if typeof(info) == TYPE_STRING:
		return "Pin %d: %s" % [num, PIN_TIPS.get(info, info)]
	var g := _gpio(info)
	var lv = g.get("level")
	var level := "floating (Z)" if lv == null else ("high (1)" if int(lv) == 1 else "low (0)")
	var dir := "output" if g.get("dir", "in") == "out" else "input"
	var extra := ""
	if g.get("pull") != null:
		extra += ", pull-%s" % g["pull"]
	if g.get("driven", false):
		extra += ", driven externally"
	var hint := "" if dir == "output" else "\nClick to drive it: Z → 1 → 0 → Z"
	return "Pin %d: GP%d — %s, %s%s%s" % [num, info, dir, level, extra, hint]
