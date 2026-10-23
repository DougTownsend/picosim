extends RefCounted
## Heat overlay for the CPU diagram and the pipeline diagram (View ▸ Heat
## overlay): each box is tinted by how much of the run it was used, with a
## small pill giving its share of the clock cycles.  The colours are the
## heat map's (Palette.heat); main.gd fetches the numbers from the backend's
## heat command and hands each view a map id → {t: 0..1, pct, count}.

const ALPHA := 0.6


## The overlay's colour for t: the heat ramp without its extreme steps, so
## the diagram's labels stay readable on the busiest boxes in either theme.
static func color(t: float) -> Color:
	return Palette.heat(0.12 + 0.6 * clampf(t, 0.0, 1.0))


## Tint a box (its polygon, or its rect when the polygon is empty).
static func tint(c: CanvasItem, poly: PackedVector2Array, r: Rect2, t: float) -> void:
	var col := Color(color(t), ALPHA)
	if poly.is_empty():
		c.draw_rect(r, col)
	else:
		c.draw_colored_polygon(poly, col)


## A small "37%" pill on a box's top-right corner.
static func pill(c: CanvasItem, r: Rect2, h: Dictionary, font: Font, scale: float) -> void:
	var pct := float(h.get("pct", 0.0))
	var s := ("%.0f%%" % pct) if pct >= 10.0 else ("%.1f%%" % pct)
	var fs := maxi(8, int(round(10.0 * scale)))
	var w := font.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x + 8 * scale
	var hh := fs + 4 * scale
	var box := Rect2(Vector2(r.end.x - w + 2 * scale, r.position.y - hh * 0.6), Vector2(w, hh))
	var bg := color(float(h.get("t", 0.0)))
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	sb.set_corner_radius_all(int(hh / 2))
	c.draw_style_box(sb, box)
	var ink := Color("0b0b0b") if bg.get_luminance() > 0.5 else Color("f0efec")
	c.draw_string(font, box.position + Vector2(4 * scale, hh - 3.5 * scale), s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, ink)


## The heat map's totals (a heat reply) as overlay values: datapath blocks
## scaled against the busiest block (the FSM, used every cycle, is left
## out), registers by reads + writes against the busiest register.
static func from_totals(d: Dictionary) -> Dictionary:
	var out := {}
	var rows: Array = d.get("rows", [])
	var vals: Array = d.get("values", [])
	var cyc := float(d.get("cycles", 0))
	var blocks := {}
	var regs := {}
	for i in rows.size():
		var id: String = rows[i]["id"]
		var v := float(vals[i][0]) if i < vals.size() else 0.0
		if id.ends_with(":r") or id.ends_with(":w"):
			var reg := id.left(id.length() - 2)
			if reg.length() > 1:
				regs[reg] = float(regs.get(reg, 0.0)) + v
		elif not id.begins_with("mem:") and id != "fsm":
			blocks[id] = v
	var bmax := 0.0
	for v in blocks.values():
		bmax = maxf(bmax, v)
	var rmax := 0.0
	for reg in regs:
		if reg != "PC":                  # the register file's rows: R0–R12, SP, LR
			rmax = maxf(rmax, regs[reg])
	for id in blocks:
		if blocks[id] <= 0.0:
			continue
		out[id] = {"t": blocks[id] / bmax if bmax > 0 else 0.0, "count": blocks[id],
			"pct": 100.0 * blocks[id] / cyc if cyc > 0 else 0.0}
	for reg in regs:
		if regs[reg] <= 0.0:
			continue                     # never used: left untinted
		out[reg + ":rw"] = {"t": regs[reg] / rmax if rmax > 0 else 0.0, "count": regs[reg],
			"pct": 100.0 * regs[reg] / cyc if cyc > 0 else 0.0}
	return out
