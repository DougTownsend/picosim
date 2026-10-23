extends VBoxContainer
## The Performance window's "Heat map" tab: how much each part of the CPU,
## each register and each flag is used, as a grid of coloured cells.
##
## Rows are components; columns are the whole run (Totals), slices of time
## (Timeline, down to one clock cycle), the lines of the program (Per line),
## or a before/after comparison with a saved baseline (Compare).  The data
## comes from the backend's `heat` command (picosim/heat.py): exact counts of
## the multi-cycle FSM's control signals, or an estimate for the RP2040's
## 2-stage pipeline.  Every control has a tooltip; "About the heat map"
## opens the guide's page on it.

signal guide_requested(topic: String)
signal component_clicked(id: String)

const MODELS := ["multicycle", "m0plus"]
const VIEWS := ["totals", "timeline", "lines", "compare"]
const ROW_SETS := ["All rows", "Datapath and bus", "Registers", "Flags and memory"]
const POLL := 0.5

var model_select: OptionButton
var view_select: OptionButton
var rows_select: OptionButton
var metric_select: OptionButton
var code_only: CheckBox
var scale_select: OptionButton
var min_box: SpinBox
var max_box: SpinBox
var auto_scale: CheckBox
var threshold: HSlider
var cell_w: HSlider
var cell_h: HSlider
var bucket_box: SpinBox
var per_select: OptionButton
var time_row: HFlowContainer
var compare_row: HFlowContainer
var grid: HeatGrid
var scroll: ScrollContainer
var legend: Legend
var tiles: RegTiles
var info: RichTextLabel
var detail: RichTextLabel
var save_dialog: FileDialog
var png_dialog: FileDialog

var data: Dictionary = {}
var _req := 0
var _key := ""
var _wait := 0.0
var _pending := false
var _asked_at := 0.0
var start := 0                 # timeline window, in cycles
var end := 0
var follow := true             # the timeline window grows with the run


func _ready() -> void:
	name = "Heat map"
	add_theme_constant_override("separation", 6)
	var r1 := _flow()
	model_select = _option(r1, "Model", ["Multi-cycle datapath (exact)", "2-stage pipeline (estimate)"],
		"Which CPU to measure:\nMulti-cycle datapath — exact: counted from the FSM's control signals in every clock cycle.\n2-stage pipeline — the RP2040's Cortex-M0+: an estimate from the datasheet's cycle counts (picosim has no control signals for it).")
	view_select = _option(r1, "View", ["Totals", "Timeline", "Per line", "Compare"],
		"Totals — the whole run, one number per component.\nTimeline — use over time, one column per bucket of clock cycles (down to 1 cycle).\nPer line — which line of the program uses each component.\nCompare — this run against a saved baseline: blue = used less, red = used more.")
	rows_select = _option(r1, "Rows", ROW_SETS, "Which rows to show")
	metric_select = _option(r1, "Value", ["Count", "% of cycles"],
		"Count — how many clock cycles the component was used (registers and flags: how many reads or writes).\n% of cycles — the same as a share of the clock cycles (in Timeline, of each column's cycles): its utilisation.")
	code_only = CheckBox.new()
	code_only.text = "Your code only"
	code_only.tooltip_text = "Leave out the built-in OS (addresses below 0x3000): its start-up, putchar and getchar"
	code_only.focus_mode = Control.FOCUS_NONE
	code_only.toggled.connect(func(_on): _ask(true))
	r1.add_child(code_only)
	var about := _button(r1, "About the heat map", "What the heat map measures and how to read it (opens the guide)",
		func(): guide_requested.emit("heat"))
	about.size_flags_horizontal = Control.SIZE_SHRINK_END

	var r2 := _flow()
	scale_select = _option(r2, "Scale", ["Linear", "Log", "Percentile"],
		"How values map to colour:\nLinear — colour in proportion to the value.\nLog — spreads out small values, so a little use still shows.\nPercentile — by rank: every step of colour holds the same number of cells, so small differences become visible.")
	auto_scale = CheckBox.new()
	auto_scale.text = "Auto range"
	auto_scale.button_pressed = true
	auto_scale.focus_mode = Control.FOCUS_NONE
	auto_scale.tooltip_text = "Colour from 0 to the largest value shown. Untick to set Min and Max yourself (values outside are clamped)."
	auto_scale.toggled.connect(func(on): min_box.editable = not on; max_box.editable = not on; _redraw())
	r2.add_child(auto_scale)
	min_box = _spin(r2, "Min", 0, 1e12, 0, "The value drawn in the lightest colour (with Auto range off)")
	max_box = _spin(r2, "Max", 0, 1e12, 100, "The value drawn in the strongest colour (with Auto range off)")
	min_box.editable = false
	max_box.editable = false
	threshold = _slider(r2, "Threshold", 0, 50, 0, 1,
		"Cells below this % of the colour range are drawn as unused, so only the busy parts stand out")
	cell_w = _slider(r2, "Cell width", 2, 48, 14, 1, "Width of each column, in pixels (Ctrl + mouse wheel on the grid also zooms)")
	cell_h = _slider(r2, "Row height", 2, 28, 14, 1, "Height of each row, in pixels")

	time_row = _flow()
	bucket_box = _spin(time_row, "Bucket (cycles)", 1, 1e9, 1,
		"Clock cycles in each Timeline column. 1 shows every cycle. picosim raises it when the window would need more than 4096 columns.")
	bucket_box.value_changed.connect(func(_v): _ask(true))
	_button(time_row, "Fit to width", "Choose the bucket so the whole window fits the grid's width", _fit_bucket)
	_button(time_row, "Zoom out", "Show twice as many cycles", _zoom_out)
	_button(time_row, "Whole run", "Show the run from its first cycle to now (and keep following it)", _whole_run)
	var drag := Label.new()
	drag.text = "Drag across the grid to zoom into those cycles"
	drag.add_theme_color_override("font_color", Palette.c("dim"))
	time_row.add_child(drag)

	compare_row = _flow()
	_button(compare_row, "Save baseline", "Keep this run's usage to compare later runs with (saved next to your program as <name>.heat-baseline.json)",
		func(): Backend.send("heat_baseline", {"action": "save"}))
	_button(compare_row, "Clear baseline", "Delete the saved baseline", func(): Backend.send("heat_baseline", {"action": "clear"}))
	per_select = _option(compare_row, "Compare", ["Raw counts", "Per 1000 cycles"],
		"Raw counts — total uses in each run.\nPer 1000 cycles — each run's uses per 1000 of its own clock cycles, so a shorter or longer run compares fairly.")

	var r4 := _flow()
	_button(r4, "Export CSV…", "Save what is shown as a CSV file (opens in Excel, or Python's csv / pandas)", func(): _export("csv"))
	_button(r4, "Export JSON…", "Save what is shown as a JSON file", func(): _export("json"))
	_button(r4, "Save PNG…", "Save a picture of the grid", func(): png_dialog.popup_centered_ratio(0.6))

	var split := HSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(split)
	scroll = ScrollContainer.new()
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	split.add_child(scroll)
	grid = HeatGrid.new()
	scroll.add_child(grid)
	scroll.get_h_scroll_bar().value_changed.connect(func(v): grid.xoff = v; grid.queue_redraw())
	grid.range_selected.connect(_zoom_to)
	grid.row_clicked.connect(func(id): component_clicked.emit(id))
	grid.cell_zoom.connect(func(d): cell_w.value += d)
	grid.hovered.connect(func(t): detail.text = t)

	var side := VBoxContainer.new()
	side.custom_minimum_size.x = 290
	split.add_child(side)
	legend = Legend.new()
	side.add_child(legend)
	tiles = RegTiles.new()
	side.add_child(tiles)
	info = _rich(side)
	detail = _rich(side)

	save_dialog = _file_dialog(["*.csv ; CSV", "*.json ; JSON"], _on_export_path)
	png_dialog = _file_dialog(["*.png ; PNG image"], _on_png_path)

	for c in [model_select, view_select, rows_select, metric_select, scale_select, per_select]:
		c.item_selected.connect(func(_i): _on_option())
	for s in [min_box, max_box]:
		s.value_changed.connect(func(_v): _redraw())
	threshold.value_changed.connect(func(_v): _redraw())
	cell_w.value_changed.connect(func(_v): _redraw())
	cell_h.value_changed.connect(func(_v): _redraw())
	Backend.heat_received.connect(_on_heat)
	Backend.heat_baseline_changed.connect(func(_e): _ask(true))
	Backend.heat_exported.connect(_on_exported)
	Backend.program_loaded.connect(func(_p): _whole_run())
	Palette.changed.connect(_redraw)
	var args := OS.get_cmdline_user_args()
	for a in args:
		if a.begins_with("--heat-view="):
			view_select.select(maxi(0, VIEWS.find(a.substr(12))))
		if a.begins_with("--heat-model="):
			model_select.select(maxi(0, MODELS.find(a.substr(13))))
		if a.begins_with("--heat-bucket="):
			bucket_box.set_value_no_signal(float(a.substr(14)))
		if a.begins_with("--heat-scale="):
			scale_select.select(clampi(int(a.substr(13)), 0, 2))
	_on_option()


# ── building blocks ─────────────────────────────────────────────────────────

func _flow() -> HFlowContainer:
	var f := HFlowContainer.new()
	f.add_theme_constant_override("h_separation", 10)
	f.add_theme_constant_override("v_separation", 4)
	add_child(f)
	return f


func _label(parent: Control, text: String, tip: String) -> void:
	var l := Label.new()
	l.text = text
	l.tooltip_text = tip
	l.mouse_filter = Control.MOUSE_FILTER_PASS
	parent.add_child(l)


func _option(parent: Control, text: String, items: Array, tip: String) -> OptionButton:
	var h := HBoxContainer.new()
	parent.add_child(h)
	_label(h, text, tip)
	var o := OptionButton.new()
	for it in items:
		o.add_item(it)
	o.tooltip_text = tip
	o.focus_mode = Control.FOCUS_NONE
	h.add_child(o)
	return o


func _spin(parent: Control, text: String, lo: float, hi: float, v: float, tip: String) -> SpinBox:
	var h := HBoxContainer.new()
	parent.add_child(h)
	_label(h, text, tip)
	var s := SpinBox.new()
	s.min_value = lo
	s.max_value = hi
	s.step = 1
	s.value = v
	s.allow_greater = true
	s.custom_minimum_size.x = 96
	s.tooltip_text = tip
	h.add_child(s)
	return s


func _slider(parent: Control, text: String, lo: float, hi: float, v: float, step: float, tip: String) -> HSlider:
	var h := HBoxContainer.new()
	parent.add_child(h)
	_label(h, text, tip)
	var s := HSlider.new()
	s.min_value = lo
	s.max_value = hi
	s.step = step
	s.value = v
	s.custom_minimum_size.x = 90
	s.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	s.focus_mode = Control.FOCUS_NONE
	s.tooltip_text = tip
	h.add_child(s)
	return s


func _button(parent: Control, text: String, tip: String, cb: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.tooltip_text = tip
	b.focus_mode = Control.FOCUS_NONE
	b.pressed.connect(cb)
	parent.add_child(b)
	return b


func _rich(parent: Control) -> RichTextLabel:
	var r := RichTextLabel.new()
	r.bbcode_enabled = true
	r.fit_content = true
	r.scroll_active = false
	r.selection_enabled = true
	parent.add_child(r)
	return r


func _file_dialog(filters: Array, cb: Callable) -> FileDialog:
	var d := FileDialog.new()
	d.file_mode = FileDialog.FILE_MODE_SAVE_FILE
	d.access = FileDialog.ACCESS_FILESYSTEM
	d.use_native_dialog = true
	d.filters = PackedStringArray(filters)
	d.file_selected.connect(cb)
	add_child(d)
	return d


# ── asking the backend ──────────────────────────────────────────────────────

func view() -> String:
	return VIEWS[view_select.selected]


func _msg() -> Dictionary:
	return {"model": MODELS[model_select.selected], "view": view(), "code_only": code_only.button_pressed,
		"start": start, "end": end if not follow else 0, "bucket": int(bucket_box.value),
		"per": 1000 if per_select.selected == 1 else 0}


## Called by the Performance window while this tab is showing.
func tick(delta: float) -> void:
	_wait -= delta
	if _wait <= 0.0:
		_wait = POLL
		_ask(false)


func _ask(force: bool) -> void:
	var st := Backend.state
	var key := "%s|%s|%s|%s" % [str(_msg()), st.get("steps", 0), st.get("cycles", 0), Backend.program.get("path", "")]
	var now := Time.get_ticks_msec() / 1000.0
	if _pending and now - _asked_at > 3.0:
		_pending = false           # no answer: ask again
	if not force and (key == _key or _pending):
		return
	_asked_at = now
	_key = key
	_req += 1
	_pending = true
	var m := _msg()
	m["req"] = _req
	Backend.send("heat", m)


func _on_option() -> void:
	var v := view()
	time_row.visible = v == "timeline"
	compare_row.visible = true
	per_select.get_parent().visible = v == "compare"
	metric_select.disabled = v == "compare"
	_ask(true)


func _on_heat(d: Dictionary) -> void:
	if int(d.get("req", -1)) != _req:
		return                     # an older answer, or the diagram overlay's
	_pending = false
	data = d
	if d.get("view") == "timeline" and follow:
		start = 0
		end = int(d.get("end", 0))
	if d.get("view") == "timeline":
		bucket_box.set_value_no_signal(float(d.get("bucket", bucket_box.value)))
	_redraw()


func _redraw() -> void:
	if data.is_empty() or not data.get("loaded", false):
		grid.set_data({}, {})
		info.text = "[color=%s]Load a program and run it: the heat map shows how much each part of the CPU was used.[/color]" % Palette.hex("dim")
		return
	var opts := {
		"rows": rows_select.selected, "percent": metric_select.selected == 1 and view() != "compare",
		"scale": scale_select.selected, "auto": auto_scale.button_pressed,
		"lo": min_box.value, "hi": max_box.value, "threshold": threshold.value / 100.0,
		"cw": cell_w.value, "ch": cell_h.value,
	}
	grid.set_data(data, opts)
	legend.set_range(grid.legend_info())
	tiles.set_values(grid.register_values(), grid.legend_info())
	info.text = _info_text()


func _info_text() -> String:
	var d := data
	var t := ""
	var dim := Palette.hex("dim")
	if d.get("estimate", false):
		t += "[b]2-stage pipeline: estimate.[/b] [color=%s]picosim times the RP2040's Cortex-M0+ from the datasheet but has no control signals for it; each block's use is estimated from the instruction (see About).[/color]\n" % dim
	else:
		t += "[b]Multi-cycle datapath: exact.[/b] [color=%s]Counted from the FSM's control signals in every clock cycle.[/color]\n" % dim
	if d.get("view") == "compare":
		if not d.get("baseline", false):
			return t + "No baseline yet. Run your program, press [b]Save baseline[/b], change the program, run it again and come back here."
		t += "Baseline saved %s: %s cycles.  Now: %s cycles.\n" % [str(d.get("saved", "")), _n(d.get("before_cycles", 0)), _n(d.get("after_cycles", 0))]
		t += "[color=%s]Blue = used less than the baseline, red = used more.[/color]" % dim
		return t
	t += "%s clock cycles · %s instructions%s\n" % [_n(d.get("cycles", 0)), _n(d.get("insns", 0)),
		" (your code only)" if d.get("code_only", false) else ""]
	if d.get("view") == "timeline":
		t += "Cycles %s–%s, %s per column." % [_n(d.get("start", 0)), _n(d.get("end", 0)), _n(d.get("bucket", 1))]
		var traced := int(d.get("traced_cycles", 0))
		if int(d.get("trace_dropped", 0)) > 0:
			t += "\n[color=%s]Cycle-by-cycle detail is kept for the first %s cycles (20 million instructions); after that, columns are samples about every few thousand cycles.[/color]" % [Palette.hex("changed"), _n(traced)]
		t += "\n[color=%s]The timeline includes the built-in OS (Your code only does not apply here).[/color]" % dim
	return t


static func _n(v) -> String:
	var s := str(int(v))
	var out := ""
	while s.length() > 3:
		out = "," + s.right(3) + out
		s = s.left(s.length() - 3)
	return s + out


# ── timeline zoom ───────────────────────────────────────────────────────────

func _zoom_to(c0: int, c1: int) -> void:
	if c1 <= c0:
		return
	follow = false
	start = c0
	end = c1
	_fit_bucket()


func _zoom_out() -> void:
	var w := maxi(2, end - start)
	follow = false
	start = maxi(0, start - w / 2)
	end = start + w * 2
	_fit_bucket()


func _whole_run() -> void:
	follow = true
	start = 0
	end = 0
	_ask(true)


func _fit_bucket() -> void:
	var span := maxi(1, (end if not follow else int(data.get("end", 1))) - start)
	var cols := maxi(1, int((scroll.size.x - HeatGrid.LABEL_W - 8) / cell_w.value))
	bucket_box.set_value_no_signal(maxf(1.0, ceilf(float(span) / cols)))
	_ask(true)


# ── export ──────────────────────────────────────────────────────────────────

func _export(fmt: String) -> void:
	var base := str(Backend.program.get("path", "program")).get_file().get_basename()
	save_dialog.current_file = "%s-heat-%s.%s" % [base, view(), fmt]
	save_dialog.popup_centered_ratio(0.6)


func _on_export_path(path: String) -> void:
	var m := _msg()
	m["path"] = path
	Backend.send("heat_export", m)


func _on_exported(d: Dictionary) -> void:
	detail.text = ("Saved %s" % d.get("path", "")) if d.get("ok", false) else \
		"[color=%s]Could not save: %s[/color]" % [Palette.hex("error"), d.get("error", "")]


func _on_png_path(path: String) -> void:
	var img := get_viewport().get_texture().get_image()
	var r := Rect2i(scroll.get_global_rect())
	r = r.intersection(Rect2i(Vector2i.ZERO, img.get_size()))
	if r.size.x > 0 and r.size.y > 0:
		img = img.get_region(r)
	var err := img.save_png(path if path.ends_with(".png") else path + ".png")
	detail.text = "Saved %s" % path if err == OK else "[color=%s]Could not save the picture[/color]" % Palette.hex("error")


# ── the grid ────────────────────────────────────────────────────────────────

class HeatGrid extends Control:
	signal range_selected(c0: int, c1: int)
	signal row_clicked(id: String)
	signal cell_zoom(delta: float)
	signal hovered(text: String)

	const LABEL_W := 190.0
	const HEAD := 30.0
	const GROUP_GAP := 16.0

	var d: Dictionary = {}
	var o: Dictionary = {}
	var rows: Array = []        # visible rows: [index into d.rows, y]
	var lo := 0.0
	var hi := 1.0
	var ranks: Array = []       # sorted values for the percentile scale
	var diverge := false
	var xoff := 0.0
	var _drag_from := -1.0
	var _drag_to := -1.0
	var font: Font
	var mono: Font = Backend.mono_font()

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_STOP
		tooltip_text = " "

	func set_data(data: Dictionary, opts: Dictionary) -> void:
		d = data
		o = opts
		font = get_theme_default_font()
		rows.clear()
		if d.is_empty():
			custom_minimum_size = Vector2.ZERO
			queue_redraw()
			return
		diverge = d.get("view") == "compare"
		var y := HEAD
		var group := ""
		var drows: Array = d.get("rows", [])
		for i in drows.size():
			if not _wanted(drows[i]):
				continue
			if drows[i]["group"] != group:
				group = drows[i]["group"]
				y += GROUP_GAP
			rows.append([i, y])
			y += o["ch"]
		_scale_range()
		var w := LABEL_W + _ncols() * _cw() + 12
		if d.get("view") in ["totals", "compare"]:
			w = LABEL_W + _table_w() + 12
		custom_minimum_size = Vector2(w, y + 8)
		queue_redraw()

	func _wanted(r: Dictionary) -> bool:
		var g: String = r["group"]
		match int(o.get("rows", 0)):
			1: return g in ["control", "fetch", "decode", "address", "memory", "execute", "bus", "stall"]
			2: return g in ["register reads", "register writes"]
			3: return g in ["flags", "memory split"]
		return true

	func _cw() -> float:
		return o["cw"] if d.get("view") in ["timeline", "lines"] else maxf(o["cw"], 40.0)

	func _ncols() -> int:
		return d.get("cols", []).size()

	func _table_w() -> float:
		return 4 * 92.0 if diverge else 300.0

	# value of a visible row at column j, in the chosen metric
	func value(ri: int, j: int) -> float:
		if diverge:
			var r: Dictionary = d["rows"][ri]
			return float(r["change"]) if r["change"] != null else 0.0
		var v := float(d["values"][ri][j])
		if o.get("percent", false):
			var cyc := _col_cycles(j)
			return 100.0 * v / cyc if cyc > 0 else 0.0
		return v

	func _col_cycles(j: int) -> float:
		match d.get("view"):
			"timeline":
				var c: Array = d["cols"][j]
				return float(c[1]) - float(c[0])
			"lines":
				return float(d.get("cycles", 0))
		return float(d.get("cycles", 0))

	func _scale_range() -> void:
		var vals: Array = []
		for r in rows:
			var n := 1 if d.get("view") in ["totals", "compare"] else _ncols()
			for j in n:
				vals.append(value(r[0], j))
		if diverge:
			var m := 0.0
			for v in vals:
				m = maxf(m, absf(v))
			lo = 0.0
			hi = m if o["auto"] else maxf(o["hi"], 1e-9)
		else:
			var m := 0.0
			for v in vals:
				m = maxf(m, v)
			lo = 0.0 if o["auto"] else float(o["lo"])
			hi = m if o["auto"] else float(o["hi"])
		ranks = vals.filter(func(v): return v > 0.0) if not diverge else vals.map(func(v): return absf(v)).filter(func(v): return v > 0.0)
		ranks.sort()

	## 0..1 (or -1..1 for Compare) for a value, after the scale and clamp
	func norm(v: float) -> float:
		var sgn := signf(v) if diverge else 1.0
		var a := absf(v) if diverge else v
		var t := 0.0
		match int(o.get("scale", 0)):
			1:
				t = log(1.0 + maxf(0.0, a - lo)) / log(1.0 + maxf(1e-9, hi - lo)) if hi > lo else 0.0
			2:
				if ranks.is_empty() or a <= 0.0:
					t = 0.0
				else:
					t = float(ranks.bsearch(a, false)) / float(ranks.size())
			_:
				t = (a - lo) / (hi - lo) if hi > lo else 0.0
		return clampf(t, 0.0, 1.0) * sgn

	func color_for(v: float) -> Color:
		var t := norm(v)
		if diverge:
			return Palette.diverging(t)
		if v <= 0.0 or t < float(o.get("threshold", 0.0)):
			return Color(Palette.c("panel_alt"), 1.0)     # unused (or below the threshold)
		return Palette.heat(maxf(t, 0.04))

	func legend_info() -> Dictionary:
		return {"lo": lo, "hi": hi, "diverge": diverge, "scale": int(o.get("scale", 0)),
			"percent": o.get("percent", false), "threshold": float(o.get("threshold", 0.0)),
			"estimate": d.get("estimate", false)}

	## {R0:r: value, …} summed over the shown columns, for the register tiles
	func register_values() -> Dictionary:
		var out := {}
		var drows: Array = d.get("rows", [])
		for i in drows.size():
			var id: String = drows[i]["id"]
			if not (id.ends_with(":r") or id.ends_with(":w")):
				continue
			if diverge:
				out[id] = float(drows[i]["change"]) if drows[i]["change"] != null else 0.0
			else:
				var s := 0.0
				for v in d["values"][i]:
					s += float(v)
				out[id] = s
		return out

	func _draw() -> void:
		draw_rect(Rect2(Vector2.ZERO, size), Palette.c("surface"))
		if d.is_empty() or rows.is_empty():
			return
		var text := Palette.c("text")
		var muted := Palette.c("muted")
		var dim := Palette.c("dim")
		var ch: float = o["ch"]
		var fs := int(clampf(ch - 3.0, 9.0, 13.0))
		var view: String = d.get("view")
		var drows: Array = d["rows"]
		var x0 := LABEL_W
		if view in ["timeline", "lines"]:
			var cw := _cw()
			var gap := 1.0 if cw >= 6.0 and ch >= 6.0 else 0.0
			var vis_w := get_parent_area_size().x if get_parent() is Control else size.x
			var j0 := maxi(0, int((xoff) / cw) - 1)
			var j1 := mini(_ncols(), int((xoff + vis_w) / cw) + 2)
			for r in rows:
				for j in range(j0, j1):
					draw_rect(Rect2(x0 + j * cw, r[1], cw - gap, ch - gap), color_for(value(r[0], j)))
			_draw_header(j0, j1, cw, dim)
			if _drag_from >= 0.0:
				var a := minf(_drag_from, _drag_to)
				var b := maxf(_drag_from, _drag_to)
				draw_rect(Rect2(a, HEAD, b - a, size.y - HEAD), Color(Palette.c("focus"), 0.25))
				draw_rect(Rect2(a, HEAD, b - a, size.y - HEAD), Palette.c("focus"), false, 1.5)
		else:
			_draw_table(x0, ch, fs, text, muted)
		# the label column stays in view while the grid scrolls sideways
		draw_rect(Rect2(xoff, 0, LABEL_W - 4, size.y), Palette.c("surface"))
		var group := ""
		for r in rows:
			var row: Dictionary = drows[r[0]]
			if row["group"] != group:
				group = row["group"]
				draw_string(font, Vector2(xoff + 6, r[1] - 4), group.to_upper(), HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)
			if ch >= 7.0:
				draw_string(font, Vector2(xoff + 10, r[1] + ch - maxf(2.0, (ch - fs) / 2.0)), row["label"],
					HORIZONTAL_ALIGNMENT_LEFT, LABEL_W - 16, fs, muted)
		if ch < 7.0:
			draw_string(font, Vector2(xoff + 6, HEAD - 8), "rows too short for labels: hover for names", HORIZONTAL_ALIGNMENT_LEFT, LABEL_W - 10, 11, dim)

	func _draw_header(j0: int, j1: int, cw: float, dim: Color) -> void:
		var view: String = d.get("view")
		var every := maxi(1, int(ceilf(70.0 / cw)))
		for j in range(j0, j1):
			if j % every != 0:
				continue
			var x := LABEL_W + j * cw
			var lbl := ""
			if view == "timeline":
				lbl = HeatTabNum.n(int(d["cols"][j][0]))
			else:
				var c: Dictionary = d["cols"][j]
				lbl = ("line %d" % int(c["line"])) if int(c["line"]) > 0 else "%04X" % int(c["addr"])
				if c.get("os", false):
					lbl = "OS " + lbl
			draw_line(Vector2(x, HEAD - 6), Vector2(x, HEAD - 1), dim, 1.0)
			draw_string(font, Vector2(x + 2, HEAD - 10), lbl, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)
		draw_string(font, Vector2(xoff + 6, 12), "cycle →" if view == "timeline" else "program line →",
			HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)

	func _draw_table(x0: float, ch: float, fs: int, text: Color, muted: Color) -> void:
		var drows: Array = d["rows"]
		var gap := 1.0 if ch >= 6.0 else 0.0
		if diverge:
			var heads := ["before", "after", "change", "change %"]
			for k in 4:
				draw_string(font, Vector2(x0 + k * 92 + 4, HEAD - 8), heads[k], HORIZONTAL_ALIGNMENT_LEFT, -1, 11, muted)
			for r in rows:
				var row: Dictionary = drows[r[0]]
				draw_rect(Rect2(x0 + 2 * 92, r[1], 88, ch - gap), color_for(value(r[0], 0)))
				if ch >= 9.0:
					var pct = row["pct"]
					var cells := [_fmt(row["before"]), _fmt(row["after"]), _signed(row["change"]),
						"new" if pct == null else ("%+.1f%%" % float(pct))]
					for k in 4:
						var col := text if k != 2 else _ink_on(color_for(value(r[0], 0)))
						draw_string(mono, Vector2(x0 + k * 92 + 4, r[1] + ch - maxf(2.0, (ch - fs) / 2.0)), cells[k],
							HORIZONTAL_ALIGNMENT_LEFT, 84, fs, col)
			return
		draw_string(font, Vector2(x0 + 4, HEAD - 8), "% of cycles" if o.get("percent", false) else "count", HORIZONTAL_ALIGNMENT_LEFT, -1, 11, muted)
		var cyc := float(d.get("cycles", 0))
		for r in rows:
			var v := value(r[0], 0)
			var raw := float(d["values"][r[0]][0])
			draw_rect(Rect2(x0, r[1], 120, ch - gap), color_for(v))
			if ch >= 9.0:
				var s := _fmt(raw) + ("   %.1f%%" % (100.0 * raw / cyc) if cyc > 0 else "")
				draw_string(mono, Vector2(x0 + 128, r[1] + ch - maxf(2.0, (ch - fs) / 2.0)), s, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, text)

	static func _ink_on(bg: Color) -> Color:
		return Color("0b0b0b") if bg.get_luminance() > 0.5 else Color("f0efec")

	static func _fmt(v) -> String:
		var f := float(v)
		if absf(f - roundf(f)) < 1e-9:
			return HeatTabNum.n(int(f))
		return "%.2f" % f

	static func _signed(v) -> String:
		var f := float(v)
		return ("+" if f > 0 else "") + _fmt(f) if f != 0.0 else "0"

	func _cell_at(p: Vector2) -> Array:
		if d.is_empty():
			return []
		var ri := -1
		for r in rows:
			if p.y >= r[1] and p.y < r[1] + o["ch"]:
				ri = r[0]
				break
		if ri < 0:
			return []
		if d.get("view") in ["timeline", "lines"]:
			var j := int((p.x - LABEL_W) / _cw())
			if p.x < LABEL_W + xoff or j < 0 or j >= _ncols():
				return [ri, -1]
			return [ri, j]
		return [ri, 0]

	func _describe(cell: Array) -> String:
		var row: Dictionary = d["rows"][cell[0]]
		var t := "%s  (%s)" % [row["label"], row["group"]]
		if cell[1] < 0:
			return t + "\nClick to show it on the CPU diagram."
		if diverge:
			var pct = row["pct"]
			return t + "\nbefore %s, after %s: %s%s" % [_fmt(row["before"]), _fmt(row["after"]), _signed(row["change"]),
				"" if pct == null else " (%+.1f%%)" % float(pct)]
		var j: int = cell[1]
		var raw := float(d["values"][cell[0]][j])
		var cyc := _col_cycles(j)
		t += "\n%s use%s" % [_fmt(raw), "s" if raw != 1.0 else ""]
		if cyc > 0:
			t += " = %.2f%% of %s cycles" % [100.0 * raw / cyc, HeatTabNum.n(int(cyc))]
		match d.get("view"):
			"timeline":
				var c: Array = d["cols"][j]
				t += "\ncycles %s–%s" % [HeatTabNum.n(int(c[0])), HeatTabNum.n(int(c[1]) - 1)]
			"lines":
				var c: Dictionary = d["cols"][j]
				t += "\n%s%s: %s\nran %s times, %s cycles" % ["OS " if c["os"] else "",
					("line %d" % int(c["line"])) if int(c["line"]) > 0 else "%04X" % int(c["addr"]),
					c["text"], HeatTabNum.n(int(c["execs"])), HeatTabNum.n(int(c["cycles"]))]
		return t

	func _get_tooltip(p: Vector2) -> String:
		var cell := _cell_at(p)
		if cell.is_empty():
			return ""
		var t := _describe(cell)
		hovered.emit(t.replace("\n", "\n"))
		return t

	func _gui_input(e: InputEvent) -> void:
		var mb := e as InputEventMouseButton
		if mb and mb.pressed and mb.is_command_or_control_pressed() and \
				mb.button_index in [MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN]:
			cell_zoom.emit(2.0 if mb.button_index == MOUSE_BUTTON_WHEEL_UP else -2.0)
			accept_event()
			return
		if mb and mb.button_index == MOUSE_BUTTON_LEFT:
			if mb.pressed:
				if mb.position.x < LABEL_W + xoff:
					var cell := _cell_at(mb.position)
					if not cell.is_empty():
						row_clicked.emit(d["rows"][cell[0]]["id"])
					return
				if d.get("view") == "timeline":
					_drag_from = mb.position.x
					_drag_to = mb.position.x
			elif _drag_from >= 0.0:
				var a := minf(_drag_from, _drag_to)
				var b := maxf(_drag_from, _drag_to)
				_drag_from = -1.0
				queue_redraw()
				if b - a > 4.0:
					var cw := _cw()
					var j0 := clampi(int((a - LABEL_W) / cw), 0, _ncols() - 1)
					var j1 := clampi(int((b - LABEL_W) / cw), 0, _ncols() - 1)
					range_selected.emit(int(d["cols"][j0][0]), int(d["cols"][j1][1]))
		var mm := e as InputEventMouseMotion
		if mm and _drag_from >= 0.0:
			_drag_to = mm.position.x
			queue_redraw()


# thousands separators, shared by the inner classes
class HeatTabNum:
	static func n(v: int) -> String:
		var neg := v < 0
		var s := str(absi(v))
		var out := ""
		while s.length() > 3:
			out = "," + s.right(3) + out
			s = s.left(s.length() - 3)
		return ("-" if neg else "") + s + out


# ── legend: the colour scale ────────────────────────────────────────────────

class Legend extends Control:
	var info: Dictionary = {}

	func _init() -> void:
		custom_minimum_size = Vector2(270, 74)

	func set_range(i: Dictionary) -> void:
		info = i
		queue_redraw()

	func _draw() -> void:
		if info.is_empty():
			return
		var font := get_theme_default_font()
		var dim := Palette.c("dim")
		var text := Palette.c("muted")
		var w := size.x - 20
		var div: bool = info["diverge"]
		var names := ["linear", "log", "percentile"]
		draw_string(font, Vector2(4, 12), ("Change (blue = less, red = more), %s" if div else "Use, %s scale") % names[int(info["scale"])],
			HORIZONTAL_ALIGNMENT_LEFT, -1, 12, text)
		var steps := 64
		for k in steps:
			var t := float(k) / (steps - 1)
			var col := Palette.diverging(t * 2.0 - 1.0) if div else Palette.heat(t)
			draw_rect(Rect2(4 + w * k / steps, 20, w / steps + 1, 14), col)
		var hi: float = info["hi"]
		var lo: float = info["lo"]
		var unit := "%" if info["percent"] else ""
		var a := (("−" + _s(hi)) if div else _s(lo)) + unit
		var b := (("+" + _s(hi)) if div else _s(hi)) + unit
		if int(info["scale"]) == 2:
			a = "lowest"
			b = "highest"
		draw_string(font, Vector2(4, 48), a, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)
		var bw := font.get_string_size(b, HORIZONTAL_ALIGNMENT_LEFT, -1, 11).x
		draw_string(font, Vector2(4 + w - bw, 48), b, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)
		if div:
			draw_string(font, Vector2(4 + w / 2 - 4, 48), "0", HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)
		else:
			draw_rect(Rect2(4, 56, 14, 12), Palette.c("panel_alt"))
			var note := "not used" + (" / below the threshold" if float(info["threshold"]) > 0.0 else "")
			draw_string(font, Vector2(24, 66), note, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)

	static func _s(v: float) -> String:
		if v >= 1000.0:
			return HeatTabNum.n(int(roundf(v)))
		return ("%.0f" % v) if absf(v - roundf(v)) < 1e-6 else ("%.2f" % v)


# ── R0–R15 tiles: reads and writes ──────────────────────────────────────────

class RegTiles extends Control:
	const NAMES := ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
		"R8", "R9", "R10", "R11", "R12", "SP", "LR", "PC"]
	var vals: Dictionary = {}
	var info: Dictionary = {}
	var mono: Font = Backend.mono_font()

	func _init() -> void:
		custom_minimum_size = Vector2(270, 2 * (18 + 4 * 30) + 8)
		tooltip_text = " "

	func set_values(v: Dictionary, i: Dictionary) -> void:
		vals = v
		info = i
		queue_redraw()

	func _max(suffix: String) -> float:
		var m := 0.0
		for n in NAMES:
			m = maxf(m, absf(float(vals.get(n + suffix, 0.0))))
		return m

	func _draw() -> void:
		if vals.is_empty():
			return
		var font := get_theme_default_font()
		var div: bool = info.get("diverge", false)
		var y := 0.0
		for part in [[":r", "Register reads"], [":w", "Register writes"]]:
			var m := _max(part[0])
			draw_string(font, Vector2(4, y + 13), part[1] + ("  (change)" if div else ""), HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Palette.c("muted"))
			y += 18
			var tw := (size.x - 8) / 4.0
			for i in 16:
				var nm: String = NAMES[i]
				if not vals.has(nm + part[0]):
					continue
				var v := float(vals[nm + part[0]])
				var r := Rect2(4 + (i % 4) * tw, y + (i / 4) * 30, tw - 3, 27)
				var col: Color
				if div:
					col = Palette.diverging(v / m if m > 0 else 0.0)
				elif v <= 0.0:
					col = Palette.c("panel_alt")
				else:
					col = Palette.heat(maxf(0.04, v / m if m > 0 else 0.0))
				draw_rect(r, col)
				var ink := Color("0b0b0b") if col.get_luminance() > 0.5 else Color("f0efec")
				draw_string(font, r.position + Vector2(4, 11), nm, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, ink)
				var s := ("+" if div and v > 0 else "") + HeatTabNum.n(int(roundf(v)))
				draw_string(mono, r.position + Vector2(4, 24), s, HORIZONTAL_ALIGNMENT_LEFT, tw - 8, 11, ink)
			y += 4 * 30 + 4

	func _get_tooltip(p: Vector2) -> String:
		var tw := (size.x - 8) / 4.0
		var y0 := 18.0
		for part in [[":r", "reads"], [":w", "writes"]]:
			if p.y >= y0 and p.y < y0 + 4 * 30:
				var i := int((p.y - y0) / 30) * 4 + clampi(int((p.x - 4) / tw), 0, 3)
				var nm: String = NAMES[i]
				if vals.has(nm + part[0]):
					return "%s %s: %s" % [nm, part[1], HeatTabNum.n(int(roundf(float(vals[nm + part[0]]))))] + \
						("  (change from the baseline)" if info.get("diverge", false) else "") + \
						"\nA read is one use of a register-file read port (SR1 or SR2); a write is one use of the write port (DR)." + \
						("\nThe PC is read by every fetch and written by every fetch and branch." if nm == "PC" else "")
			y0 += 18 + 4 * 30 + 4
		return ""
