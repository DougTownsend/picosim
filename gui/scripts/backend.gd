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
signal error_received(message: String)
signal load_failed(path: String, message: String)
signal serial_ports_listed(ports: Array)
signal flash_finished(ok: bool)
signal walkthrough_received(data: Dictionary)

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
		"error":
			error_received.emit(msg["message"])
		"load_error":
			load_failed.emit(msg["path"], msg["message"])
		"serial_ports":
			serial_ports_listed.emit(msg["ports"])
		"flash_done":
			flash_finished.emit(msg["ok"])
		"walkthrough":
			walkthrough_received.emit(msg)


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

static func hex32(v: Variant) -> String:
	return "0x%08X" % int(v)


static func hex16(v: Variant) -> String:
	return "0x%04X" % int(v)


static func parse_number(text: String) -> Variant:
	var t := text.strip_edges().to_lower().replace("_", "")
	if t.begins_with("0x"):
		return t.substr(2).hex_to_int() if t.substr(2).is_valid_hex_number() else null
	if t.begins_with("#"):
		t = t.substr(1)
	if t.is_valid_int():
		return int(t) & 0xFFFFFFFF
	if t.is_valid_hex_number():
		return t.hex_to_int()
	return null


static func mono_font() -> Font:
	var f := SystemFont.new()
	f.font_names = PackedStringArray(["Menlo", "SF Mono", "Monaco", "Consolas",
		"DejaVu Sans Mono", "monospace"])
	return f
