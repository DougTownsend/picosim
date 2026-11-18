extends SceneTree
## Headless run of the real GUI (gui/main.tscn) against a real backend,
## driving the classroom flows from the review handoff (EDU-01 … EDU-05,
## PERF-02) the way a student would: keys, buttons, the level menu.
## tests/test_gui_flows.py starts the backend and runs this with
##   godot --headless --path gui --script tests/gui/edu_flows.gd -- --port P --tmp DIR
## Each check prints PASS or FAIL; the exit code is the number of failures.
## HOME (so settings.cfg), PICOSIM_COPIES_DIR and PICOSIM_EDITOR=none are
## set by the runner, so nothing of the user's is touched.

var Welcome: GDScript   # loaded once the autoloads exist (a preload here would compile it too early)

var main: Control
var B: Node          # the Backend autoload (not a global name in a --script main loop)
var L: Node          # the Level autoload
var fails := 0
var tmp := ""
var ex_dir := ""


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i] == "--tmp" and i + 1 < args.size():
			tmp = args[i + 1]
	ex_dir = ProjectSettings.globalize_path("res://").path_join("../examples").simplify_path()
	Welcome = load("res://scripts/welcome.gd")
	B = root.get_node("Backend")
	L = root.get_node("Level")
	main = load("res://main.tscn").instantiate()
	root.add_child(main)
	_run.call_deferred()


func check(name: String, ok: bool, detail: Variant = "") -> void:
	print(("PASS " if ok else "FAIL ") + name + ("" if ok else "   (%s)" % str(detail)))
	if not ok:
		fails += 1


func frames(n: int) -> void:
	for i in n:
		await process_frame


func until(cond: Callable, timeout := 8.0) -> bool:
	var t0 := Time.get_ticks_msec()
	while not cond.call():
		if Time.get_ticks_msec() - t0 > timeout * 1000.0:
			return false
		await process_frame
	return true


func key(code: Key, shift := false, ctrl := false) -> void:
	for pressed in [true, false]:
		var ev := InputEventKey.new()
		ev.keycode = code
		ev.physical_keycode = code
		ev.pressed = pressed
		ev.shift_pressed = shift
		ev.ctrl_pressed = ctrl
		root.push_input(ev)
	await frames(2)


func st() -> Dictionary:
	return B.state


func loaded(file: String) -> bool:
	return st().get("loaded", false) and str(st().get("path", "")).get_file() == file \
		and str(B.program.get("path", "")).get_file() == file


func steps_changed(n: int) -> Callable:
	return func(): return int(st().get("steps", -1)) != n and not st().get("running", false)


func dialog(title: String) -> ConfirmationDialog:
	for c in main.get_children():
		if c is ConfirmationDialog and c.visible and str(c.title).contains(title):
			return c
	return null


func write(path: String, text: String, ahead := 0) -> void:
	var f := FileAccess.open(path, FileAccess.WRITE)
	f.store_string(text)
	f.close()


func _run() -> void:
	check("backend connected", await until(func(): return B.online and st().has("loaded")))
	await frames(5)
	await keyboard_first_lesson()
	await stale_builds()
	await copies()
	await level_aware_launch()
	await level_changes_keep_the_cpu()
	await streamed_walkthrough()
	print("done: %d failing" % fails)
	quit(fails)


# ── EDU-03: the first lesson from the keyboard ──────────────────────────────

func keyboard_first_lesson() -> void:
	var w = main.welcome
	var focus = root.gui_get_focus_owner()
	check("welcome: the first example has the focus", focus != null and focus == w._first, focus)
	var seen := [focus]
	for i in 5:
		await key(KEY_TAB)
		seen.append(root.gui_get_focus_owner())
	check("welcome: Tab moves through the buttons", seen.all(func(c): return c is Button and w.is_ancestor_of(c))
		and seen[5] != seen[0], seen.map(func(c): return c.text if c is Button else str(c)))
	await key(KEY_TAB, true)
	check("welcome: Shift+Tab goes back", root.gui_get_focus_owner() == seen[4])
	var ring = seen[1].get_theme_stylebox("focus")
	check("welcome: the focused button has a visible ring", ring is StyleBoxFlat and ring.border_width_left > 0)
	w._first.grab_focus()
	await key(KEY_ENTER)
	check("Enter opens Say hello", await until(func(): return loaded("01_hello.s")))
	await frames(3)
	check("the welcome buttons let go of the focus", root.gui_get_focus_owner() == null, root.gui_get_focus_owner())
	check("Level 1 starts at main", int(st()["pc"]) == int(B.program["main"]))
	for i in 2:
		var n := int(st()["steps"])
		await key(KEY_S)
		await until(steps_changed(n))
	check("S steps: instruction 2", main.status_label.text.begins_with("instruction 2"), main.status_label.text)
	var n2 := int(st()["steps"])
	await key(KEY_S)                                  # bl putchar: one step
	await until(func(): return int(st()["steps"]) > n2 + 1 and not st().get("running", false))
	await frames(3)
	check("one Step over putchar counts 1", main.status_label.text.begins_with("instruction 3"), main.status_label.text)
	check("…and the tooltip says what the OS ran", main.status_label.tooltip_text.contains("built-in OS"), main.status_label.tooltip_text)
	var simple := str(st().get("explain", {}).get("now", {}).get("simple", ""))
	check("Explain says why the count went up by 1", simple.contains("went up by 1"), simple)
	# a click in the walkthrough-free Disassembly list must not swallow the keys
	main.mem_panel.tree.grab_focus()
	var n3 := int(st()["steps"])
	await key(KEY_S)
	check("S still steps with the list focused", await until(steps_changed(n3)))
	main.mem_panel.tree.release_focus()
	main.speed_slider.value = main.SPEEDS.size() - 1      # Run at full speed
	await key(KEY_SPACE)
	check("Space runs to the end", await until(func(): return st().get("halted", false) and not st().get("running", false)))
	await frames(3)
	simple = str(st().get("explain", {}).get("now", {}).get("simple", ""))
	check("after a halt Explain offers Reload / Step back, not Step", not simple.contains("Step from here") and simple.contains("Reload"), simple)
	check("Step is off after the halt", main.btn_insn.disabled)
	await key(KEY_B)
	check("B steps back", await until(func(): return not st().get("halted", true)))
	print("console: ", B.state.get("path"))


# ── EDU-02: a save that does not build ──────────────────────────────────────

func stale_builds() -> void:
	var hello := FileAccess.get_file_as_string(ex_dir.path_join("01_hello.s"))
	var p := tmp.path_join("hello.s")
	write(p, hello)
	B.send("load", {"path": p})
	check("a copy of hello loads", await until(func(): return loaded("hello.s")))
	B.send("run", {"cps": 0})
	await until(func(): return st().get("halted", false) and not st().get("running", false))
	write(p, hello.replace("#'H'", "#'Hello'"))
	B.send("reload")
	check("a typo: the error box opens", await until(func(): return main.error_dialog.visible))
	await until(func(): return main.stale_bar.visible)
	await frames(3)
	var bar: String = main.stale_bar._text.text
	check("the stale strip names the file, its save time and line", main.stale_bar.visible and bar.contains("hello.s")
		and bar.contains("line 22") and bar.contains("saved at"), bar)
	check("the error box says which save failed", main.error_dialog.get_node("Text").text.contains("saved at"))
	check("Step back, Step and Run are off", main.btn_run.disabled and main.btn_insn.disabled and main.btn_back.disabled)
	check("the status says it did not build", main.status_label.text.begins_with("Not built: hello.s line 22"), main.status_label.text)
	var cb = main.find_children("*", "PanelContainer", true, false).filter(func(c): return c.get_script() == load("res://scripts/challenge_bar.gd"))[0]
	check("the challenge result is not shown as current", cb._text.text.contains("not checked"), cb._text.text)
	var n := int(st()["steps"])
	await key(KEY_B)
	await frames(10)
	check("B does nothing while it does not build", int(st()["steps"]) == n)
	main.error_dialog.hide()
	write(p, hello.replace("movs    r0, #'i'", "movs    r0, #'e'\n        bl      putchar\n        movs    r0, #'y'"))
	B.send("reload")
	check("a good save clears the strip", await until(func(): return not main.stale_bar.visible and loaded("hello.s")))
	check("…and Run is back", not main.btn_run.disabled)
	B.send("run", {"cps": 0})
	await until(func(): return st().get("halted", false) and not st().get("running", false))
	await frames(5)
	check("the fixed program passes its challenge", cb._text.text.contains("passed"), cb._text.text)
	# another file that does not build, with Add paused: Run must not run Add
	B.send("load", {"path": ex_dir.path_join("02_add.s")})
	await until(func(): return loaded("02_add.s"))
	for i in 3:
		var k := int(st()["steps"])
		await key(KEY_S)
		await until(steps_changed(k))
	var bad := tmp.path_join("bad.s")
	write(bad, ".syntax unified\n.thumb\nmain:\n    frobnicate r0\n")
	B.send("load", {"path": bad})
	await until(func(): return main.stale_bar.visible)
	main.error_dialog.hide()
	check("another file's mistake: Run is off", main.btn_run.disabled)
	check("…and the strip offers the way back", main.stale_bar._text.text.contains("02_add.s"))
	var k2 := int(st()["steps"])
	await key(KEY_SPACE)
	await frames(15)
	check("Space does not run the old lesson", int(st()["steps"]) == k2 and not st().get("running", false))
	B.send("use_last_build")
	check("Back to 02_add.s", await until(func(): return not main.stale_bar.visible and not main.btn_insn.disabled))


# ── EDU-04: Run example, Edit my copy, and a copy that cannot be saved ──────

func copies() -> void:
	var e: Dictionary = Welcome.example_named("01_hello.s")
	main._launch_example(e, false)
	check("Run example loads the original", await until(func(): return str(st().get("path", "")) == str(e["path"])))
	main._launch_example(e, true)
	var copy := OS.get_environment("PICOSIM_COPIES_DIR").path_join("01_hello.s")
	check("Edit my copy saves and loads the copy", await until(func(): return str(st().get("path", "")) == copy))
	await until(func(): return main.notice_bar.visible and main.notice_bar.key == "example")
	check("…and says where it is", main.notice_bar._text.text.contains("01_hello.s") and main.notice_bar._text.text.contains("saved"), main.notice_bar._text.text)
	OS.set_environment("PICOSIM_COPIES_DIR", tmp.path_join("readonly/sub"))
	main._launch_example(Welcome.example_named("02_add.s"), true)
	await frames(3)
	var d := dialog("Could not save your copy")
	check("a copy that cannot be saved asks what to do", d != null)
	if d:
		check("…naming the folder", d.dialog_text.contains("readonly"), d.dialog_text)
		d.custom_action.emit("run")
		check("Just run it loads the original, not for editing", await until(func(): return loaded("02_add.s") and str(st()["path"]).begins_with(ex_dir)))
	OS.set_environment("PICOSIM_COPIES_DIR", tmp.path_join("copies"))


# ── EDU-01: an example for a higher level ───────────────────────────────────

func level_aware_launch() -> void:
	L.set_level(1)
	await frames(3)
	main._fill_examples()
	var pop: PopupMenu = main.examples_menu.get_popup()
	var id := -1
	for i in pop.item_count:
		if str(pop.get_item_metadata(i)).get_file() == "04_memory.s":
			id = pop.get_item_id(i)
	check("the Examples menu marks 04 as Level 2", pop.get_item_text(pop.get_item_index(id)).contains("Level 2"))
	main._on_example(id)
	await frames(3)
	var d := dialog("Add up a list in memory")
	check("a Level 2 example asks first", d != null)
	if d == null:
		return
	check("…and says what Level 2 shows", d.dialog_text.contains("Memory tab"), d.dialog_text)
	check("…offering to stay", d.get_ok_button().text == "Open at Level 2")
	d.get_ok_button().pressed.emit()
	check("Open at Level 2 raises the level and loads it", await until(func(): return L.current == 2 and loaded("04_memory.s")))
	await frames(5)
	check("the Memory tab is open", main.mem_panel.tabs.current_tab == 1 and not main.mem_panel.tabs.is_tab_hidden(1))
	check("a note under the toolbar", await until(func(): return main.notice_bar.visible and main.notice_bar.key == "example" and main.notice_bar._text.text.contains("numbers")), main.notice_bar._text.text)
	var t: String = main.notice_bar._text.text
	check("the note says how to find numbers", t.contains("numbers") and t.contains("Go to"), t)
	check("the note links the Memory tab guide", main.notice_bar._buttons.get_children().any(func(b): return b.text == "Memory tab guide"))
	check("the note offers Skip to main at the OS start-up", st().get("at_boot", false)
		and main.notice_bar._buttons.get_children().any(func(b): return b.text.begins_with("Skip to main")))
	var simple := str(st().get("explain", {}).get("now", {}).get("simple", ""))
	check("Explain says where it starts and how to skip", simple.contains("Skip to main"), simple)
	var mv = main.mem_panel.mem_view
	mv.goto_edit.text = "numbers"
	mv.goto_edit.text_submitted.emit("numbers")
	await frames(3)
	check("Go to numbers finds the list", int(mv.selected) == 0x20000000, mv.selected)
	# the same example, staying at Level 1
	L.set_level(1)
	await frames(3)
	main._launch_example(Welcome.example_named("04_memory.s"), false)
	await frames(3)
	d = dialog("Add up a list in memory")
	check("asked again at Level 1", d != null)
	if d:
		d.custom_action.emit("stay")
		await until(func(): return loaded("04_memory.s"))
		await until(func(): return main.notice_bar.visible and main.notice_bar.key == "example" and main.notice_bar._text.text.contains("Level 2"))
		await frames(3)
		check("Stay keeps Level 1 and its layout", L.current == 1 and main.mem_panel.tabs.is_tab_hidden(1)
			and main.mem_panel.tabs.current_tab == 0)
		check("…and points to the missing view", main.notice_bar._text.text.contains("Level 2")
			and main.notice_bar._buttons.get_children().any(func(b): return b.text == "Show Level 2"), main.notice_bar._text.text)


# ── EDU-05: changing the level never moves the CPU on ───────────────────────

func level_changes_keep_the_cpu() -> void:
	L.set_level(3)
	await frames(3)
	B.send("load", {"path": ex_dir.path_join("02_add.s")})
	await until(func(): return loaded("02_add.s") and int(st()["steps"]) == 0)
	check("Level 3 shows the diagram", main.btn_diagram.button_pressed and main.mem_panel.disasm_hidden)
	while not str(st().get("insn_text", "")).contains("r0, r2"):
		var n := int(st()["steps"])
		B.send("step_insn")
		await until(steps_changed(n))
	for i in 2:
		var c := int(st()["cycles"])
		B.send("step_cycle")
		await until(func(): return int(st()["cycles"]) != c)
	var before := [st()["regs"].duplicate(), st()["pc"], st()["cycle_index"], st()["in_insn"], st()["steps"]]
	check("partway through movs r0, r2", before[3] and int(before[2]) == 2)
	L.set_level(4)
	await until(func(): return main.notice_bar.key == "pipe_wait")
	await frames(5)
	var after := [st()["regs"], st()["pc"], st()["cycle_index"], st()["in_insn"], st()["steps"]]
	check("Level 4 leaves registers, PC and progress as they were", str(after) == str(before), [before, after])
	check("…keeps the multi-cycle CPU for now", main.machine_select.selected == 0 and str(st()["machine"]) == "multicycle")
	check("…and says why", main.notice_bar._text.text.contains("starts between instructions"))
	while st().get("in_insn", false) and str(st().get("machine", "")) == "multicycle":
		var c2 := int(st()["cycles"])
		B.send("step_cycle")
		await until(func(): return int(st()["cycles"]) != c2)
	check("the pipeline comes on when the instruction ends", await until(func(): return str(st().get("machine", "")) == "m0plus" and main.machine_select.selected == 1))
	await frames(3)
	check("…with its counts labelled", main.status_label.text.contains("pipeline cycle 0") and main.status_label.text.contains("from insn"), main.status_label.text)
	L.set_level(3)
	await frames(3)
	check("back at Level 3: multi-cycle, diagram open", main.machine_select.selected == 0 and main.btn_diagram.button_pressed)
	L.set_level(1)
	await frames(5)
	var tabs: TabContainer = main.mem_panel.tabs
	check("Level 1 with the diagram open: the Disassembly is back", not tabs.is_tab_hidden(0) and tabs.current_tab == 0
		and tabs.is_tab_hidden(1) and not main.mem_panel.disasm_hidden, [tabs.is_tab_hidden(0), tabs.current_tab, tabs.is_tab_hidden(1)])


# ── PERF-02: the walkthrough's first page arrives at once ───────────────────

func streamed_walkthrough() -> void:
	L.set_level(3)
	await frames(3)
	var wp = main.walk_panel
	var first := [-1]
	var t0 := [0]
	var on_page := func(d):
		if first[0] < 0 and d.get("compact", false):
			first[0] = Time.get_ticks_usec() - t0[0]
	B.walkthrough_received.connect(on_page)
	t0[0] = Time.get_ticks_usec()
	B.send("load", {"path": ex_dir.path_join("05_blink_led.s")})
	await until(func(): return loaded("05_blink_led.s"))
	check("Blink's walkthrough is all there", await until(func(): return wp._items.size() == 500 and wp._wt.get("done", false), 10.0), wp._items.size())
	print("PERF first walkthrough page %.1f ms after the load was sent (GUI, includes assembling Blink)" % (first[0] / 1000.0))
	check("you-are-here is on step 1", wp._cur == 0)
	var req: int = wp._req
	B.send("reload")
	await until(func(): return wp._req > req)
	check("a reload asks again and drops the old pages", await until(func(): return wp._items.size() == 500 and wp._wt.get("done", false), 10.0))
	B.walkthrough_received.disconnect(on_page)
