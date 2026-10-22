extends Node
## TCP client for the Python backend (picosim/server.py).
##
## Normally `picosim --gui` starts the server and passes `--port N` after
## Godot's `--` separator.  When the project is run straight from the editor
## there is no port, so we start `python3 -m picosim.server` ourselves.

signal connected
signal disconnected
signal state_changed(state: Dictionary)
signal program_loaded(program: Dictionary)
signal console_output(text: String, source: String)
## Step back: the console's whole transcript up to the new point.
signal console_reset(note: String, text: String)
signal error_received(message: String)
signal load_failed(path: String, message: String, errors: Array)
signal serial_ports_listed(ports: Array)
signal flash_finished(ok: bool)
signal pipe_walkthrough_received(data: Dictionary)
signal walkthrough_received(data: Dictionary)
signal perf_received(data: Dictionary)
signal heat_received(data: Dictionary)          # heat map data (picosim/heat.py)
signal heat_exported(data: Dictionary)
signal heat_baseline_changed(exists: bool)
signal sources_received(sources: Dictionary)
signal settings_error(message: String)

var tcp := StreamPeerTCP.new()
var port := 0
var buf := PackedByteArray()
var state: Dictionary = {}
var program: Dictionary = {}
var online := false
var _server_pid := -1
var _retry := 0.0
var _connect_attempts := 0


func _ready() -> void:
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i] == "--port" and i + 1 < args.size():
			port = int(args[i + 1])
	if port == 0:
		_spawn_server()
	_try_connect()


func _spawn_server() -> void:
	port = 47000 + randi() % 2000
	var repo := ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	var python := OS.get_environment("PICOSIM_PYTHON")
	if python == "":
		python = "python3"
	_server_pid = OS.create_process("/usr/bin/env", ["PYTHONPATH=" + repo, python,
		"-m", "picosim.server", "--port", str(port)])


func _try_connect() -> void:
	tcp = StreamPeerTCP.new()
	tcp.connect_to_host("127.0.0.1", port)
	_connect_attempts += 1


func _process(delta: float) -> void:
	tcp.poll()
	var st := tcp.get_status()
	if st == StreamPeerTCP.STATUS_CONNECTED:
		if not online:
			online = true
			connected.emit()
		_read()
	elif st == StreamPeerTCP.STATUS_ERROR or st == StreamPeerTCP.STATUS_NONE:
		if online:
			online = false
			disconnected.emit()
		elif _connect_attempts < 100:
			_retry -= delta
			if _retry <= 0.0:
				_retry = 0.1
				_try_connect()


func _read() -> void:
	var n := tcp.get_available_bytes()
	if n <= 0:
		return
	var res: Array = tcp.get_data(n)
	if res[0] != OK:
		return
	buf.append_array(res[1])
	while true:
		var nl := buf.find(10)
		if nl < 0:
			break
		var line := buf.slice(0, nl).get_string_from_utf8()
		buf = buf.slice(nl + 1)
		if line.strip_edges() != "":
			_handle(JSON.parse_string(line))


func _handle(msg: Variant) -> void:
	if typeof(msg) != TYPE_DICTIONARY:
		return
	match msg.get("type", ""):
		"state":
			state = msg
			state_changed.emit(msg)
		"program":
			program = msg
			program_loaded.emit(msg)
		"console":
			console_output.emit(msg["text"], msg["source"])
		"console_reset":
			console_reset.emit(msg.get("note", ""), msg.get("text", ""))
		"error":
			error_received.emit(msg["message"])
		"load_error":
			load_failed.emit(msg["path"], msg["message"], msg.get("errors", []))
		"serial_ports":
			serial_ports_listed.emit(msg["ports"])
		"flash_done":
			flash_finished.emit(msg["ok"])
		"walkthrough":
			walkthrough_received.emit(msg)
		"pipe_walkthrough":
			pipe_walkthrough_received.emit(msg)
		"perf":
			perf_received.emit(msg)
		"heat":
			heat_received.emit(msg)
		"heat_exported":
			heat_exported.emit(msg)
		"heat_baseline":
			heat_baseline_changed.emit(bool(msg.get("exists", false)))
		"sources":
			sources_received.emit(msg["sources"])
		"settings_error":
			settings_error.emit(msg["message"])


func send(cmd: String, args: Dictionary = {}) -> void:
	if not online:
		return
	var d := args.duplicate()
	d["cmd"] = cmd
	tcp.put_data((JSON.stringify(d) + "\n").to_utf8_buffer())


func _exit_tree() -> void:
	if _server_pid > 0:
		OS.kill(_server_pid)


# ── helpers shared by the panels ────────────────────────────────────────────

## Set a Label's, LineEdit's or Button's text only when it differs: setting
## it reshapes the text and lays the panel out again, even to the same value,
## and states arrive up to 30 times a second while a program runs.
static func set_text(c: Control, s: String) -> void:
	if c.text != s:
		c.text = s


## Override a theme colour only when it differs (an override change makes the
## control and its container lay themselves out again).
static func set_color(c: Control, item: StringName, col: Color) -> void:
	if not c.has_theme_color_override(item) or c.get_theme_color(item) != col:
		c.add_theme_color_override(item, col)


static func hex32(v: Variant) -> String:
	return "0x%08X" % int(v)


static func hex16(v: Variant) -> String:
	return "0x%04X" % int(v)


static func parse_number(text: String) -> Variant:
	var t := text.strip_edges().to_lower().replace("_", "")
	if t.begins_with("0x"):
		var h := t.substr(2)
		return h.hex_to_int() if h.is_valid_hex_number() and h.length() <= 8 else null
	if t.begins_with("0b"):
		var bits := t.substr(2)
		if bits == "" or bits.length() > 32 or not bits.is_valid_int() or bits.lstrip("01") != "":
			return null
		return bits.bin_to_int()
	if t.begins_with("#"):
		t = t.substr(1)
	if t.is_valid_int():
		if t.length() > 11 or int(t) > 0xFFFFFFFF or int(t) < -0x80000000:
			return null    # does not fit in a 32-bit register
		return int(t) & 0xFFFFFFFF
	if t.is_valid_hex_number() and t.length() <= 8:
		return t.hex_to_int()
	return null


static func mono_font() -> Font:
	var f := SystemFont.new()
	f.font_names = PackedStringArray(["Menlo", "SF Mono", "Monaco", "Consolas",
		"DejaVu Sans Mono", "monospace"])
	return f
