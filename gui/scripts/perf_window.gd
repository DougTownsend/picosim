extends Node
## Performance window (toolbar "Performance"): four tabs about the run so far.
##
##   Metrics   — instructions, cycles and CPI on each machine, the multi-cycle
##               machine's phase breakdown and bus / ALU / memory use, the
##               instruction mix and the costliest instructions.
##   Compare   — architecture options (multiplier, memory wait states) and
##               how they change the cycle count on each machine.
##   Pipeline  — the last instructions laid out on the RP2040's 2-stage
##               pipeline (or a 3-stage teaching pipeline), cycle by cycle;
##               click a cycle to see what every stage holds.
##   Heat map  — how much each component, register and flag is used: totals,
##               a timeline down to single cycles, per program line, and a
##               before/after comparison (heat_tab.gd, picosim/heat.py).
##
## All numbers come from the backend's "perf" command (picosim/timing.py).
## While the window is open it asks again whenever the machine has moved on.

const POLL := 0.35          # seconds between requests while the machine changes
const PIPE_N := [16, 32, 64, 128]
const HeatTab := preload("res://scripts/heat_tab.gd")

var win: Window
var tabs: TabContainer
var metrics: RichTextLabel
var compare: RichTextLabel
var phase_bars
var mix_bars
var mul_select: OptionButton
var wait_select: OptionButton
var machine_select: OptionButton
var n_select: OptionButton
var chart
var pipe_text: RichTextLabel
var btn_prev: Button
var btn_next: Button
var bg: PanelContainer
var heat_tab: Node

var data: Dictionary = {}
var _asked_key := ""        # machine state + options we last asked about
var _pending := false
var _timer := 0.0
var selected := -1          # pipeline cycle on display
var follow := true          # keep showing the latest cycle until the user picks one
var _asked_at := 0.0


func _ready() -> void:
	win = Window.new()
	win.title = "picosim — Performance"
	win.visible = false
	win.transient = true
	win.min_size = Vector2i(720, 480)
	win.close_requested.connect(win.hide)
	win.window_input.connect(_on_window_input)
	add_child(win)
	bg = PanelContainer.new()
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	win.add_child(bg)
	tabs = TabContainer.new()
	bg.add_child(tabs)
	_build_metrics()
	_build_compare()
	_build_pipeline()
	heat_tab = HeatTab.new()
	heat_tab.name = "Heat map"
	tabs.add_child(heat_tab)
	tabs.tab_changed.connect(func(_t): if _heat_showing(): heat_tab.tick(1.0))
	Backend.perf_received.connect(_on_perf)
	Backend.program_loaded.connect(func(_p): _reset())
	_apply_palette()
	Palette.changed.connect(_apply_palette)
	set_process(false)
	var args := OS.get_cmdline_user_args()
	if args.has("--perf-pipe3"):
		machine_select.select(1)
	if args.has("--perf-mul32"):
		mul_select.select(1)
	if args.has("--perf-wait2"):
		wait_select.select(2)
	var i := args.find("--perf")
	if i >= 0:
		var t := args[i + 1] if i + 1 < args.size() else "0"
		open.call_deferred(3 if t == "heat" else int(t))


func open(tab := -1) -> void:
	if tab >= 0:
		tabs.current_tab = clampi(tab, 0, tabs.get_tab_count() - 1)
	var area := get_viewport().get_visible_rect().size
	win.popup_centered(Vector2i(mini(int(area.x * 0.85), 1300), int(area.y * 0.85)))
	set_process(true)
	_ask(true)


func _on_window_input(event: InputEvent) -> void:
	var k := event as InputEventKey
	if k and k.pressed and not k.echo and k.keycode == KEY_ESCAPE:
		win.hide()
		win.set_input_as_handled()


func _reset() -> void:
	data = {}
	selected = -1
	follow = true
	_asked_key = ""


func _process(delta: float) -> void:
	if not win.visible:
		set_process(false)
		return
	if _heat_showing():
		heat_tab.tick(delta)
		return
	_timer -= delta
	if _timer <= 0.0:
		_timer = POLL
		_ask(false)


func _heat_showing() -> bool:
	return heat_tab != null and tabs.current_tab == heat_tab.get_index()


## Ask the backend for a fresh report if anything it depends on changed.
func _ask(force: bool) -> void:
	var st := Backend.state
	if not st.get("loaded", false):
		_show_unloaded()
		return
	var key := "%s|%s|%s|%d|%d|%d|%d" % [str(st.get("path", "")), str(st.get("steps", 0)),
		str(st.get("cycles", 0)), mul_select.selected, wait_select.selected,
		machine_select.selected, n_select.selected]
	var now := Time.get_ticks_msec() / 1000.0
	if _pending and now - _asked_at < 3.0:
		return
	if key == _asked_key and not force:
		return
	_asked_at = now
	_asked_key = key
	_pending = true
	Backend.send("perf", {"mul": "iterative" if mul_select.selected == 1 else "fast",
		"wait": wait_select.selected, "machine": "pipe3" if machine_select.selected == 1 else "m0plus",
		"n": PIPE_N[n_select.selected]})


func _on_perf(d: Dictionary) -> void:
	_pending = false
	data = d
	if not d.get("loaded", false):
		_show_unloaded()
		return
	_fill_metrics()
	_fill_compare()
	_fill_pipeline()


func _show_unloaded() -> void:
	var t := "[color=%s]Load a program to see its performance.[/color]" % Palette.hex("dim")
	metrics.text = t
	compare.text = t
	pipe_text.text = t


# ── building ────────────────────────────────────────────────────────────────

func _rich(parent: Control) -> RichTextLabel:
	var r := RichTextLabel.new()
	r.bbcode_enabled = true
	r.selection_enabled = true
	r.fit_content = true
	r.scroll_active = false
	r.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	r.add_theme_font_override("mono_font", Backend.mono_font())
	r.add_theme_font_size_override("normal_font_size", 14)
	r.add_theme_font_size_override("bold_font_size", 14)
	r.add_theme_font_size_override("mono_font_size", 13)
	parent.add_child(r)
	return r


func _scroll_page(title: String) -> VBoxContainer:
	var sc := ScrollContainer.new()
	sc.name = title
	sc.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tabs.add_child(sc)
	var m := MarginContainer.new()
	m.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	for side in ["left", "right", "top", "bottom"]:
		m.add_theme_constant_override("margin_" + side, 14)
	sc.add_child(m)
	var v := VBoxContainer.new()
	v.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	v.add_theme_constant_override("separation", 10)
	m.add_child(v)
	return v


func _option(parent: Control, label: String, items: Array, tip: String) -> OptionButton:
	var h := HBoxContainer.new()
	parent.add_child(h)
	var l := Label.new()
	l.text = label
	h.add_child(l)
	var o := OptionButton.new()
	for it in items:
		o.add_item(it)
	o.tooltip_text = tip
	o.focus_mode = Control.FOCUS_NONE
	o.item_selected.connect(func(_i): _ask(true))
	h.add_child(o)
	return o


func _build_metrics() -> void:
	var v := _scroll_page("Metrics")
	metrics = _rich(v)
	var h1 := _rich(v)
	h1.text = "[b]Multi-cycle machine: cycles in each phase[/b]"
	phase_bars = BarList.new()
	v.add_child(phase_bars)
	var h2 := _rich(v)
	h2.text = "[b]Instruction mix: instructions in each group[/b]"
	mix_bars = BarList.new()
	v.add_child(mix_bars)
	set_meta("metrics_tail", _rich(v))
	set_meta("metrics_headings", [h1, h2])   # hidden until something has run


func _build_compare() -> void:
	var v := _scroll_page("Compare")
	var intro := _rich(v)
	intro.text = "Change the design and see how many cycles [i]this run[/i] would take. The program's results never change — only timing does — because every machine executes the same instructions with the same values.\n"
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 24)
	v.add_child(row)
	mul_select = _option(row, "Multiplier", ["Fast: 1 cycle (RP2040)", "Iterative: 32 cycles"],
		"The Cortex-M0+ can be built with either multiplier; RP2040 has the fast one (datasheet §2.4.1.2)")
	wait_select = _option(row, "Memory wait states (multi-cycle machine)", ["0 (like SRAM)", "1", "2", "3"],
		"Extra cycles every memory access waits in the multi-cycle machine's MEM.EN states")
	compare = _rich(v)


func _build_pipeline() -> void:
	var v := VBoxContainer.new()
	v.name = "Pipeline"
	v.add_theme_constant_override("separation", 8)
	tabs.add_child(v)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 16)
	v.add_child(row)
	machine_select = _option(row, "Pipeline", ["2-stage (RP2040 Cortex-M0+)", "3-stage (teaching)"],
		"2-stage: the real Pico's Cortex-M0+. 3-stage: fetch | decode | execute, a teaching design")
	n_select = _option(row, "Show last", ["16", "32", "64", "128"], "How many recent instructions to lay out")
	n_select.select(1)
	btn_prev = Button.new()
	btn_prev.text = "◀ Cycle"
	btn_prev.focus_mode = Control.FOCUS_NONE
	btn_prev.pressed.connect(func(): _step_sel(-1))
	row.add_child(btn_prev)
	btn_next = Button.new()
	btn_next.text = "Cycle ▶"
	btn_next.focus_mode = Control.FOCUS_NONE
	btn_next.pressed.connect(func(): _step_sel(1))
	row.add_child(btn_next)

	var split := VSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	v.add_child(split)
	# the stages themselves are drawn in the main window's CPU diagram
	# (machine: 2-stage pipeline); this tab is the cycle-by-cycle chart
	var ts := ScrollContainer.new()
	ts.custom_minimum_size.y = 150
	ts.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	split.add_child(ts)
	pipe_text = _rich(ts)
	var cs := ScrollContainer.new()
	cs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	cs.custom_minimum_size.y = 180
	split.add_child(cs)
	chart = PipeChart.new()
	chart.cycle_clicked.connect(func(c): selected = c; follow = false; _fill_pipeline())
	cs.add_child(chart)
	set_meta("chart_scroll", cs)
	cs.get_h_scroll_bar().value_changed.connect(func(v): chart.xoff = v; chart.queue_redraw())


func _apply_palette() -> void:
	win.theme = Palette.ui_theme
	bg.add_theme_stylebox_override("panel", Palette.panel_box("bg", 0, 8))
	for c in [phase_bars, mix_bars, chart]:
		c.queue_redraw()
	if not data.is_empty():
		_on_perf(data)


# ── formatting helpers ──────────────────────────────────────────────────────

static func _c(k: String) -> String:
	return Palette.hex(k)


static func _n(v) -> String:
	## 1234567 → "1,234,567"
	var s := str(int(v))
	var out := ""
	var neg := s.begins_with("-")
	if neg:
		s = s.substr(1)
	for i in s.length():
		if i > 0 and (s.length() - i) % 3 == 0:
			out += ","
		out += s[i]
	return ("-" if neg else "") + out


static func _f(v: float, d := 2) -> String:
	return ("%." + str(d) + "f") % v


static func _cell(s: String) -> String:
	return "[cell]%s   [/cell]" % s


static func _hcell(s: String) -> String:
	return "[cell][b]%s[/b]   [/cell]" % s


static func _time(cycles: float, mhz: float) -> String:
	var us := cycles / mhz
	if us >= 1000.0:
		return _f(us / 1000.0, 3) + " ms"
	return _f(us, 2) + " µs"


static func _esc(s: String) -> String:
	return s.replace("[", "[lb]")


# ── metrics tab ─────────────────────────────────────────────────────────────

func _fill_metrics() -> void:
	var s: Dictionary = data["stock"]
	var names: Dictionary = data["names"]
	var mhz := float(data["clock_mhz"])
	var dim := _c("dim")
	var t := "[font_size=20][b]Performance of this run[/b][/font_size]\n"
	t += "[color=%s]%s · counts every completed instruction since the program was loaded%s.[/color]\n\n" % [
		dim, str(Backend.state.get("path", "")).get_file(),
		(" (the instruction in progress, %d cycles so far, is not included yet)" % int(data["cycles_in_progress"])) if data["in_insn"] else ""]
	if int(s["insns"]) == 0:
		metrics.text = t + "Nothing has run yet. Press Step cycle, Step instruction or Run."
		phase_bars.set_rows([])
		mix_bars.set_rows([])
		get_meta("metrics_tail").text = ""
		for hd in get_meta("metrics_headings"):
			hd.visible = false
		return
	for hd in get_meta("metrics_headings"):
		hd.visible = true
	t += "[table=4]" + _hcell("Machine") + _hcell("Cycles") + _hcell("CPI") + _hcell("Time at %d MHz" % int(mhz))
	for m in ["multicycle", "m0plus", "pipe3"]:
		var e: Dictionary = s["machines"][m]
		t += _cell(names[m]) + _cell(_n(e["cycles"])) + _cell(_f(e["cpi"])) + _cell(_time(e["cycles"], mhz))
	t += "[/table]\n"
	t += "[color=%s]%s instructions completed. CPI = cycles per instruction. The RP2040 runs at 125 MHz by default (Pico SDK); the other two machines are teaching designs, shown at the same clock for comparison.[/color]\n" % [dim, _n(s["insns"])]
	var un := int(s["machines"]["m0plus"]["untimed"])
	if un > 0:
		t += "[color=%s]• %s SVC / BKPT (or non-ARMv6-M) instructions are not in the pipeline totals: the datasheet gives no fixed count for them (Table 81 note e), and picosim runs SVC calls directly. CPI is over the %s timed instructions.[/color]\n" % [
			dim, _n(un), _n(s["machines"]["m0plus"]["insns_timed"])]
	var mc := float(s["multicycle_total"])
	t += "\n[b]Multi-cycle datapath use[/b]\n"
	t += "• Bus driven in %s of %s cycles ([b]%s%%[/b]) — one tri-state buffer at a time.\n" % [_n(s["bus_cycles"]), _n(mc), _f(100.0 * s["bus_cycles"] / mc, 1)]
	t += "• ALU computing in %s cycles ([b]%s%%[/b]).\n" % [_n(s["alu_cycles"]), _f(100.0 * s["alu_cycles"] / mc, 1)]
	t += "• Memory busy (MEM.EN) in %s cycles ([b]%s%%[/b]): %s instruction-halfword reads and %s data accesses.\n" % [
		_n(s["mem_cycles"]), _f(100.0 * s["mem_cycles"] / mc, 1), _n(s["fetch_reads"]),
		_n(_sum(s["data"]["reads"]) + _sum(s["data"]["writes"]))]
	metrics.text = t

	var prows := []
	for ph in ["FETCH", "DECODE", "EVALUATE ADDRESS", "FETCH OPERANDS", "EXECUTE", "STORE RESULT"]:
		var v := int(s["phases"].get(ph, 0))
		prows.append([ph.capitalize(), v, "%s cycles · %s%%" % [_n(v), _f(100.0 * v / mc, 1)]])
	phase_bars.set_rows(prows)
	var mrows := []
	for g in s["groups"]:
		var gi: Dictionary = s["groups"][g]
		if int(gi["insns"]) > 0:
			mrows.append([g, int(gi["insns"]), "%s insns · %s%%" % [_n(gi["insns"]), _f(100.0 * gi["insns"] / s["insns"], 1)]])
	mix_bars.set_rows(mrows)

	var tail := "[b]Memory and I/O (data accesses)[/b]\n"
	var regs := ["RAM", "SIO (single-cycle IOPORT)", "APB peripherals", "other AHB"]
	tail += "[table=3]" + _hcell("Where") + _hcell("Reads") + _hcell("Writes")
	for i in 4:
		tail += _cell(regs[i]) + _cell(_n(s["data"]["reads"][i])) + _cell(_n(s["data"]["writes"][i]))
	tail += "[/table]\n"
	var apb := int(s["data"]["reads"][2]) + int(s["data"]["writes"][2])
	if apb > 0:
		tail += "[color=%s]• APB accesses (IO_BANK0, PADS_BANK0, …): on the real RP2040 these take extra bus cycles — APB accesses take at least two cycles and the bridge adds one more for reads and two for writes (datasheet §2.1.3). Table 81 assumes zero wait states, so the RP2040 totals above do not include them.[/color]\n" % dim
	if int(s["data"]["reads"][1]) + int(s["data"]["writes"][1]) > 0:
		tail += "[color=%s]• SIO accesses go over the Cortex-M0+'s single-cycle IOPORT: on the RP2040 each such load or store takes 1 cycle instead of 2.[/color]\n" % dim
	var b: Dictionary = s["branches"]
	tail += "\n[b]Control flow[/b]\n• %s conditional branches, %s taken (%s%%).\n• %s times the PC was redirected (taken branches, calls, returns): each one makes a pipeline throw away what it had fetched — 1 cycle on the 2-stage RP2040, 2 on the 3-stage design.\n" % [
		_n(b["cond"]), _n(b["cond_taken"]), _f(100.0 * b["cond_taken"] / maxf(1.0, b["cond"]), 1), _n(b["pc_writes"])]
	tail += "\n[b]Costliest instructions (multi-cycle cycles)[/b]\n[table=5]" + _hcell("Address") + _hcell("Instruction") + _hcell("Runs") + _hcell("Multi-cycle") + _hcell("RP2040")
	for r in s["top"]:
		var p = r["cycles"]["m0plus"]
		tail += _cell("[code]%04X[/code]" % int(r["addr"])) + _cell("[code]%s[/code]" % _esc(str(r["text"]))) + \
			_cell(_n(r["execs"])) + _cell(_n(r["cycles"]["multicycle"]) if r["cycles"]["multicycle"] != null else "—") + \
			_cell(_n(p) if p != null else "not timed")
	tail += "[/table]\n"
	get_meta("metrics_tail").text = tail


static func _sum(a: Array) -> int:
	var t := 0
	for x in a:
		t += int(x)
	return t


# ── compare tab ─────────────────────────────────────────────────────────────

func _fill_compare() -> void:
	var s: Dictionary = data["stock"]
	var c: Dictionary = data["custom"]
	var names: Dictionary = data["names"]
	var mhz := float(data["clock_mhz"])
	var dim := _c("dim")
	if int(s["insns"]) == 0:
		compare.text = "Nothing has run yet. Run the program (or step it), then compare."
		return
	var t := "[table=6]" + _hcell("Machine") + _hcell("Stock design") + _hcell("Your design") + _hcell("Change") + _hcell("CPI") + _hcell("Time at %d MHz" % int(mhz))
	var base := float(c["machines"]["multicycle"]["cycles"])
	for m in ["multicycle", "m0plus", "pipe3"]:
		var a := float(s["machines"][m]["cycles"])
		var b := float(c["machines"][m]["cycles"])
		var ch := "same" if a == b else ("%+.1f%%" % (100.0 * (b - a) / a))
		t += _cell(names[m]) + _cell(_n(a)) + _cell("[b]%s[/b]" % _n(b)) + _cell(ch) + _cell(_f(c["machines"][m]["cpi"])) + _cell(_time(b, mhz))
	t += "[/table]\n"
	var p2 := float(c["machines"]["m0plus"]["cycles"])
	if p2 > 0:
		t += "With your options the RP2040's pipeline needs [b]%s×[/b] fewer cycles than the multi-cycle teaching machine for this run.\n" % _f(base / p2, 2)
	t += "\n[b]Where the cycles go (your design)[/b]\n[table=4]" + _hcell("Group") + _hcell("Instructions") + _hcell("Multi-cycle") + _hcell("RP2040 2-stage")
	for g in c["groups"]:
		var gi: Dictionary = c["groups"][g]
		if int(gi["insns"]) > 0:
			var untimed: bool = g == "OS / debug" or g == "not ARMv6-M"
			t += _cell(g) + _cell(_n(gi["insns"])) + _cell(_n(gi["multicycle"])) + \
				_cell("not timed" if untimed else _n(gi["m0plus"]))
	t += "[/table]\n"
	t += "\n[b]What each option does[/b]\n"
	t += "• [b]Multiplier[/b] — the Cortex-M0+ can be built with a fast single-cycle multiplier or a small iterative one that takes 32 cycles. The RP2040 has the fast one. Only MULS changes: 1 → 32 cycles on the pipelines, and its EXECUTE_COMMIT state takes 32 cycles instead of 1 on the multi-cycle machine.\n"
	t += "• [b]Memory wait states[/b] — slower memory: every MEM.EN cycle of the multi-cycle machine (instruction fetches, loads and stores) waits this many extra cycles. It is not applied to the pipelines: how a pipelined core hides or exposes wait states depends on its prefetch timing, which ARM does not publish for the Cortex-M0+, so a number there would be a guess.\n"
	t += "• [b]Pipeline depth[/b] — the three machines themselves: no overlap (multi-cycle), 2 stages (RP2040) and 3 stages. More stages overlap more work, but a taken branch throws away more: 1 refill cycle on 2 stages, 2 on 3 stages.\n"
	t += "\n[color=%s]Exact for each model: cycle counts are computed from this run's actual instructions, branch outcomes and memory addresses. RP2040 counts are datasheet Table 81 (zero wait states).[/color]" % dim
	compare.text = t


# ── pipeline tab ────────────────────────────────────────────────────────────

func _step_sel(d: int) -> void:
	var sch: Dictionary = data.get("schedule", {})
	if sch.is_empty():
		return
	var lo := int(chart.cmin)
	var hi := int(chart.cmax)
	if selected < 0:
		selected = hi
	selected = clampi(selected + d, lo, hi)
	follow = selected == chart.last_exec
	_fill_pipeline()


func _fill_pipeline() -> void:
	var sch: Dictionary = data.get("schedule", {})
	if sch.is_empty():
		return
	chart.set_schedule(sch)
	if follow or selected > chart.cmax or selected < chart.cmin:
		selected = chart.last_exec
	chart.selected = selected
	chart.queue_redraw()
	btn_prev.disabled = selected <= chart.cmin
	btn_next.disabled = selected >= chart.cmax
	pipe_text.text = _pipe_explain(sch, selected)
	if follow:
		_scroll_to_latest.call_deferred()


func _scroll_to_latest() -> void:
	# the scroll range follows the chart's new size only after layout
	await get_tree().process_frame
	await get_tree().process_frame
	var cs: ScrollContainer = get_meta("chart_scroll")
	cs.scroll_horizontal = int(cs.get_h_scroll_bar().max_value)
	cs.scroll_vertical = int(cs.get_v_scroll_bar().max_value)


func _pipe_explain(sch: Dictionary, t: int) -> String:
	var depth := int(sch["depth"])
	var dim := _c("dim")
	var out := "[b]Cycle %d[/b]\n" % (t - int(chart.cmin))
	if sch["rows"].is_empty():
		return "Nothing has run yet."
	var names := {"F": "Fetch", "D": "Decode", "E": "Execute"}
	for st in (["F", "E"] if depth == 2 else ["F", "D", "E"]):
		var r = _row_at(sch, t, st)
		out += "\n[b]%s[/b] — " % names[st]
		if r == null:
			out += "[color=%s]not shown: it holds an instruction outside the window drawn here (older, or one the program has not reached yet)[/color]\n" % dim
			continue
		var txt := "[code]%04X  %s[/code]" % [int(r["addr"]), _esc(str(r["text"]))]
		if st == "E":
			var k := t - int(r["start"]) + 1
			if not r["timed"]:
				out += txt + ": the datasheet gives no fixed cycle count for this instruction (Table 81 note e), so it is drawn as one cycle.\n"
				continue
			out += txt + " — Table 81 row [i]%s[/i]: %d cycle%s. This is cycle %d of %d." % [
				_esc(str(r["row"])), int(r["cycles"]), "" if int(r["cycles"]) == 1 else "s", k, int(r["cycles"])]
			if int(r["refill"]) > 0 and k > int(r["exec"]):
				out += " The PC has already been redirected; this cycle refills the pipeline from the new address."
			elif int(r["refill"]) > 0 and k == int(r["exec"]):
				out += " It redirects the PC, so the instruction%s fetched behind it %s discarded." % [
					"" if depth == 2 else "s", "is" if depth == 2 else "are"]
			if int(r["sio"]) > 0:
				out += " Its data access goes to the SIO over the single-cycle IOPORT, so it takes 1 cycle instead of 2."
			if int(r["apb"]) > 0:
				out += " [color=%s]Its data access goes to an APB peripheral: on the real chip that adds bus wait states (datasheet §2.1.3) that Table 81 does not include.[/color]" % dim
			out += "\n"
		elif r["flushed"]:
			out += txt + ": fetched from the next sequential address, but the branch in Execute changed the PC, so it will be discarded (✕).\n"
		elif r.get("pending", false):
			out += txt + " is the next instruction: it is in the pipeline but has not executed yet, so its execute cycles are not known yet.\n"
		elif st == "F":
			out += txt + " is fetched from memory over the AHB-Lite bus (the Cortex-M0+ fetches 32 bits at a time).\n"
		else:
			out += txt + " is decoded: its registers and operation are worked out for the execute stage.\n"
	out += "\n[color=%s]How to read this: every instruction spends %s before it executes. Execute-stage cycle counts are RP2040 datasheet Table 81 (zero wait states)%s. The exact cycle in which the real core fetches is not published (it fetches up to 32 bits ahead), so F marks when the instruction is in the fetch stage of this model.[/color]" % [
		dim, "one cycle in Fetch" if depth == 2 else "one cycle in Fetch and one in Decode",
		"" if depth == 2 else ", plus one more refill cycle per taken branch for the extra stage"]
	return out


static func _row_at(sch: Dictionary, t: int, st: String):
	var found = null
	for r in sch["rows"]:
		for p in r["stages"]:
			if int(p[0]) == t and str(p[1]) == st:
				found = r
	return found


# ═══════════════════════════════════════════════════════════════════════════
# Single-series horizontal bars: label · bar · value (hover for the value).

class BarList extends Control:
	var rows: Array = []      # [label, value, value text]
	const ROW := 24.0
	const LABEL_W := 170.0

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_PASS
		tooltip_text = " "

	func set_rows(r: Array) -> void:
		rows = r
		custom_minimum_size.y = maxf(ROW, rows.size() * ROW + 6)
		queue_redraw()

	func _draw() -> void:
		var font := get_theme_default_font()
		var fs := 13
		var mx := 1.0
		for r in rows:
			mx = maxf(mx, float(r[1]))
		var bar_w := maxf(40.0, size.x - LABEL_W - 190.0)
		for i in rows.size():
			var r: Array = rows[i]
			var y := i * ROW
			draw_string(font, Vector2(0, y + 16), str(r[0]), HORIZONTAL_ALIGNMENT_LEFT, LABEL_W - 8, fs, Palette.c("text"))
			var w := bar_w * float(r[1]) / mx
			if w > 0.0:
				var sb := StyleBoxFlat.new()
				sb.bg_color = Palette.c("stage_f")
				sb.corner_radius_top_right = 4
				sb.corner_radius_bottom_right = 4
				draw_style_box(sb, Rect2(LABEL_W, y + 5, maxf(w, 2.0), ROW - 10))
			draw_string(font, Vector2(LABEL_W + w + 8, y + 16), str(r[2]), HORIZONTAL_ALIGNMENT_LEFT, -1, fs, Palette.c("muted"))

	func _get_tooltip(at: Vector2) -> String:
		var i := int(at.y / ROW)
		if i >= 0 and i < rows.size():
			return "%s: %s" % [rows[i][0], rows[i][2]]
		return ""


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline chart: one row per instruction, one column per cycle.

class PipeChart extends Control:
	signal cycle_clicked(c: int)
	const CELL := 26.0
	const ROW_H := 22.0
	const LABEL_W := 290.0
	const HEAD := 44.0
	var mono: Font = Backend.mono_font()
	var sch: Dictionary = {}
	var cmin := 0
	var cmax := 0
	var last_exec := 0
	var selected := -1
	var xoff := 0.0           # horizontal scroll: the label column stays in view

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_STOP
		tooltip_text = " "

	func set_schedule(s: Dictionary) -> void:
		sch = s
		cmin = 1 << 30
		cmax = -(1 << 30)
		last_exec = 0
		for r in s["rows"]:
			for p in r["stages"]:
				cmin = mini(cmin, int(p[0]))
				cmax = maxi(cmax, int(p[0]))
				if str(p[1]) == "E":
					last_exec = maxi(last_exec, int(p[0]))
		if s["rows"].is_empty():
			cmin = 0
			cmax = 0
		custom_minimum_size = Vector2(LABEL_W + (cmax - cmin + 1) * CELL + 10, HEAD + s["rows"].size() * ROW_H + 8)
		queue_redraw()

	func _col_x(c: int) -> float:
		return LABEL_W + (c - cmin) * CELL

	func _draw() -> void:
		var font := get_theme_default_font()
		draw_rect(Rect2(Vector2.ZERO, size), Palette.c("surface"))
		if sch.is_empty():
			return
		var dim := Palette.c("dim")
		# selected column
		if selected >= cmin and selected <= cmax:
			draw_rect(Rect2(_col_x(selected), 0, CELL, size.y), Palette.c("box_active"))
		# cycle numbers (every 5th labelled)
		var cols := {"F": Palette.c("stage_f"), "D": Palette.c("stage_d"), "E": Palette.c("stage_e")}
		var on := Palette.c("on_stage")
		var rows: Array = sch["rows"]
		for i in rows.size():
			var r: Dictionary = rows[i]
			var y := HEAD + i * ROW_H
			var k := 0
			for p in r["stages"]:
				var c := int(p[0])
				var st := str(p[1])
				var rect := Rect2(_col_x(c) + 1, y + 2, CELL - 2, ROW_H - 4)
				var letter := st
				if r["flushed"]:
					draw_rect(rect, Palette.c("panel_alt"))
					draw_rect(rect, dim, false, 1.0)
					letter = st + "✕"
					draw_string(font, rect.position + Vector2(2, 14), letter, HORIZONTAL_ALIGNMENT_LEFT, CELL - 4, 11, dim)
					continue
				if st == "E":
					k += 1
					if not r["timed"]:
						draw_rect(rect, Palette.c("panel_alt"))
						letter = "?"
						draw_string(font, rect.position + Vector2(8, 14), letter, HORIZONTAL_ALIGNMENT_LEFT, -1, 12, dim)
						continue
					if k > int(r["exec"]):
						# refill cycle of a branch: outlined, not filled
						draw_rect(rect, cols["E"], false, 2.0)
						draw_string(font, rect.position + Vector2(7, 14), "R", HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Palette.c("text"))
						continue
				draw_rect(rect, cols[st])
				draw_string(font, rect.position + Vector2(8, 14), letter, HORIZONTAL_ALIGNMENT_LEFT, -1, 12, on)
		# sticky label column: instruction names stay visible while cycles scroll
		draw_rect(Rect2(xoff, 0, LABEL_W, size.y), Palette.c("surface"))
		draw_rect(Rect2(xoff, 0, size.x, HEAD), Palette.c("surface"))
		for c in range(cmin, cmax + 1):
			var n := c - cmin
			if n % 5 == 0 and _col_x(c) >= xoff + LABEL_W:
				draw_string(font, Vector2(_col_x(c) + 3, 38), str(n), HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)
		draw_string(font, Vector2(xoff + 4, 38), "cycle →", HORIZONTAL_ALIGNMENT_LEFT, -1, 11, dim)
		for i in rows.size():
			var r: Dictionary = rows[i]
			var lbl := "%04X  %s" % [int(r["addr"]), str(r["text"])]
			if r.get("pending", false):
				lbl += "   (next)"
			draw_string(mono, Vector2(xoff + 4, HEAD + i * ROW_H + 15), lbl, HORIZONTAL_ALIGNMENT_LEFT, LABEL_W - 10, 12,
				dim if r["flushed"] or r.get("pending", false) else Palette.c("text"))
		# legend (always present: the stages are a categorical encoding)
		var lx := xoff + 4.0
		for e in [["F", "Fetch", "F"], ["D", "Decode", "D"], ["E", "Execute", "E"]]:
			if e[0] == "D" and int(sch["depth"]) == 2:
				continue
			draw_rect(Rect2(lx, 5, 14, 14), cols[e[0]])
			draw_string(font, Vector2(lx + 3, 16), e[2], HORIZONTAL_ALIGNMENT_LEFT, -1, 10, on)
			draw_string(font, Vector2(lx + 18, 16), e[1], HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Palette.c("muted"))
			lx += 74
		draw_string(font, Vector2(lx, 16), "R refill  ✕ discarded  ? not timed", HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Palette.c("muted"))

	func _cell_at(at: Vector2) -> Array:
		if sch.is_empty() or at.x < xoff + LABEL_W:
			return [-1, -1]
		var c := cmin + int((at.x - LABEL_W) / CELL)
		var i := int((at.y - HEAD) / ROW_H) if at.y >= HEAD else -1
		return [c, i]

	func _gui_input(event: InputEvent) -> void:
		var mb := event as InputEventMouseButton
		if mb and mb.pressed and mb.button_index == MOUSE_BUTTON_LEFT:
			var ci := _cell_at(mb.position)
			if ci[0] >= cmin and ci[0] <= cmax:
				cycle_clicked.emit(ci[0])
				accept_event()

	func _get_tooltip(at: Vector2) -> String:
		var ci := _cell_at(at)
		var rows: Array = sch.get("rows", [])
		if ci[1] < 0 or ci[1] >= rows.size():
			return "Cycle %d (click to inspect)" % (ci[0] - cmin) if ci[0] >= cmin else ""
		var r: Dictionary = rows[ci[1]]
		for p in r["stages"]:
			if int(p[0]) == ci[0]:
				var nm: String = {"F": "Fetch", "D": "Decode", "E": "Execute"}[str(p[1])]
				return "Cycle %d · %s\n%04X  %s%s" % [ci[0] - cmin, nm, int(r["addr"]), str(r["text"]),
					"\n(discarded: wrong path after a branch)" if r["flushed"] else ""]
		return "%04X  %s" % [int(r["addr"]), str(r["text"])]
