extends Node
## Learning level: how much of picosim a student sees.  Each level adds to
## the one before; level 4 is the whole app.  Remembered in settings.cfg
## ([ui] level); --level=N on the command line overrides it for one run.

signal changed

const SETTINGS := "user://settings.cfg"
const NAMES := ["Basics", "Memory & I/O", "Inside the CPU", "Pipeline & performance"]
const ABOUT := [
	"Run programs one instruction at a time and watch the registers and the serial console",
	"Adds the Memory tab, the stack and the Raspberry Pi Pico board with its pins",
	"Adds the CPU diagram: step through every clock cycle on the datapath",
	"Adds the Pico's 2-stage pipeline and the Performance window",
]
const MAX := 4

var current := 1


func _ready() -> void:
	var cfg := ConfigFile.new()
	if cfg.load(SETTINGS) == OK:
		current = clampi(int(cfg.get_value("ui", "level", 1)), 1, MAX)
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--level="):
			current = clampi(int(arg.substr(8)), 1, MAX)


func at_least(n: int) -> bool:
	return current >= n


func set_level(n: int) -> void:
	n = clampi(n, 1, MAX)
	if n == current:
		return
	current = n
	var cfg := ConfigFile.new()
	cfg.load(SETTINGS)
	cfg.set_value("ui", "level", n)
	cfg.save(SETTINGS)
	changed.emit()


## "2 · Memory & I/O"
static func title(n: int) -> String:
	return "%d · %s" % [n, NAMES[n - 1]]
