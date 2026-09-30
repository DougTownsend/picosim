extends RefCounted
## Detailed descriptions of every block on the CPU diagram, shown in the
## diagram's info card (click a block) and in the user guide.
##
## Keys match the box ids in diagram_view.gd, plus "bus".  Each entry has:
##   kind  — one-line category
##   what  — what the block is for
##   how   — how it operates, cycle by cycle
##   links — [direction, other block, control signal, note]; direction is
##           "in" (data arrives from `other`) or "out" (data goes to `other`)
##   when  — instructions / states where you will see it working

const ORDER := ["pc", "inc", "pcmux", "clrt", "align", "addr1mux", "addr2mux", "adder",
	"ir", "ir2", "fsm", "ext", "cond", "vec", "bus", "mar", "marinc", "mem", "mdr",
	"loadext", "storealign", "regfile", "sett", "sr2mux", "alu", "flags"]

const INFO := {
	"pc": {
		"title": "PC — Program Counter",
		"kind": "Register (loads at the clock edge)",
		"what": "Holds the address of the next instruction halfword to fetch. Every instruction starts by copying the PC to MAR.",
		"how": "In FETCH_ADDR the PC drives the bus (GatePC) so MAR can capture it; in the same cycle the +2 incrementer and PCMUX feed PC + 2 back and LD.PC loads it. Later in the instruction a branch can load a new value through PCMUX. Because the PC is stepped during fetch, reading the PC in an instruction gives the instruction's address + 4 (Thumb pipeline convention).",
		"links": [
			["out", "bus", "GatePC", "sends the fetch address to MAR"],
			["out", "inc", "PCINC=+2", "the +2 incrementer reads it"],
			["out", "align", "ADDR1MUX=PC / Align", "PC-relative base for branches and literal loads"],
			["in", "pcmux", "LD.PC", "the next PC value"],
		],
		"when": "Every FETCH_ADDR cycle; EXECUTE_PC for B, BL, BX, BLX, POP {PC}.",
	},
	"inc": {
		"title": "+2 — PC incrementer",
		"kind": "Combinational adder",
		"what": "Computes PC + 2, the address of the next sequential halfword (Thumb instructions are 2 bytes).",
		"how": "It always adds 2 to the PC; the result only matters when PCMUX selects it (PCMUX=PC+2) and LD.PC is asserted, which happens in every fetch.",
		"links": [
			["in", "pc", "", "current PC"],
			["out", "pcmux", "PCMUX=PC+2", "sequential next PC"],
		],
		"when": "FETCH_ADDR and FETCH2_ADDR.",
	},
	"pcmux": {
		"title": "PCMUX — next-PC selector",
		"kind": "Multiplexer (select lines from the FSM)",
		"what": "Chooses which value the PC loads next.",
		"how": "Three inputs: PC+2 (sequential fetch), ADDER (a branch target computed by the address adder) and BUS through CLR THUMB (an address held in a register: BX, BLX, POP {PC}, MOV/ADD PC). The FSM sets the select lines and asserts LD.PC in the same cycle. For a conditional branch that is not taken the FSM simply does not assert LD.PC.",
		"links": [
			["in", "inc", "PCMUX=PC+2", ""],
			["in", "adder", "PCMUX=ADDER", "branch target"],
			["in", "clrt", "PCMUX=BUS", "register target"],
			["out", "pc", "LD.PC", ""],
		],
		"when": "Fetch (PC+2); EXECUTE_PC of branches.",
	},
	"clrt": {
		"title": "CLR THUMB — clear bit 0",
		"kind": "Combinational logic",
		"what": "Turns a Thumb 'interworking' address (bit 0 = 1) into a halfword-aligned fetch address.",
		"how": "Addresses stored in LR or used by BX have bit 0 set to mean 'Thumb state'. When such a value goes from the bus into the PC, this block forces bit 0 to 0 (value & ~1) before it reaches PCMUX.",
		"links": [
			["in", "bus", "CLR THUMB BIT", "target address from a register"],
			["out", "pcmux", "PCMUX=BUS", ""],
		],
		"when": "EXECUTE_PC of BX, BLX, POP {PC}, MOV PC / ADD PC.",
	},
	"align": {
		"title": "PC / Align(PC,4)",
		"kind": "Combinational logic",
		"what": "Supplies the PC-relative base for the address adder.",
		"how": "It passes on the PC read value (instruction address + 4), or that value rounded down to a multiple of 4 (Align) for LDR Rd, =literal / LDR Rd, [PC, #imm] and ADR, whose offsets are counted in words from a word-aligned base.",
		"links": [
			["in", "pc", "", ""],
			["out", "addr1mux", "ADDR1MUX=PC / Align(PC,4)", ""],
		],
		"when": "B, B<cond>, BL (PC); LDR literal and ADR (Align).",
	},
	"addr1mux": {
		"title": "ADDR1MUX — address base selector",
		"kind": "Multiplexer",
		"what": "Chooses the base (left) input of the address adder.",
		"how": "Inputs: PC or Align(PC,4) for PC-relative addressing, a register from the register file's SR1 port (LDR R2, [R0, #4]) or SP (LDR R2, [SP, #4], PUSH, POP). The FSM sets the select during DECODE and holds it through EVALUATE_ADDRESS.",
		"links": [
			["in", "align", "ADDR1MUX=PC / Align", ""],
			["in", "regfile", "ADDR1MUX=SR1 / SP", "shown as the 'SR1 / SP' stub"],
			["out", "adder", "", "base"],
		],
		"when": "EVALUATE_ADDRESS of loads/stores; branches.",
	},
	"addr2mux": {
		"title": "ADDR2MUX — address offset selector",
		"kind": "Multiplexer",
		"what": "Chooses the offset (right) input of the address adder.",
		"how": "Inputs: a zero-extended immediate scaled by the access size (ZEXT(IR[10:6])×4 for a word), a sign-extended branch offset ×2 (SEXT), a second register (SR2) for [Rn, Rm] addressing, −4n / +4n for multi-register transfers, or zero. The immediates come from IMM / OFFSET.",
		"links": [
			["in", "ext", "ADDR2MUX=ZEXT / SEXT", "immediate or branch offset"],
			["in", "regfile", "ADDR2MUX=SR2", "index register"],
			["out", "adder", "", "offset"],
		],
		"when": "Same cycles as ADDR1MUX; −4n / +4n in EVALUATE_ADDRESS and WRITEBACK of PUSH, POP, LDM, STM.",
	},
	"adder": {
		"title": "ADDRESS ADDER",
		"kind": "Combinational adder (separate from the ALU)",
		"what": "Adds base + offset to form an effective memory address or a branch target.",
		"how": "Because it is its own adder, address arithmetic never occupies the ALU. Its result either drives the bus (GateADDR) so MAR (or a register, for ADR) can capture it, or goes straight to PCMUX as a branch target.",
		"links": [
			["in", "addr1mux", "", "base"],
			["in", "addr2mux", "", "offset"],
			["out", "bus", "GateADDR", "to MAR or a register"],
			["out", "pcmux", "PCMUX=ADDER", "branch target"],
		],
		"when": "EVALUATE_ADDRESS (loads, stores, PUSH/POP); EXECUTE_PC (B, BL); STORE_RESULT of ADR; WRITEBACK of PUSH/POP/LDM/STM (new SP or base register).",
	},
	"ir": {
		"title": "IR — Instruction Register",
		"kind": "Register (16 bits)",
		"what": "Holds the instruction being executed (or the first half of a 32-bit one) for the whole instruction.",
		"how": "Loaded from the bus in FETCH_IR (GateMDR + LD.IR). Its bits are wired to the FSM, which decodes the opcode, and to IMM / OFFSET, which pulls out immediates and register numbers. It does not change again until the next fetch.",
		"links": [
			["in", "bus", "LD.IR", "the fetched halfword (via MDR)"],
			["out", "fsm", "", "opcode bits for decoding"],
			["out", "ext", "", "immediate and offset fields"],
		],
		"when": "Loaded every FETCH_IR; read in DECODE.",
	},
	"ir2": {
		"title": "IR2 — second instruction halfword",
		"kind": "Register (16 bits)",
		"what": "Holds the second halfword of a 32-bit Thumb-2 encoding such as BL.",
		"how": "When DECODE finds a 32-bit prefix in IR, the FSM runs a second fetch (FETCH2_ADDR → FETCH2_MEMORY → FETCH2_IR) that loads IR2. Its bits complete the branch offset in IMM / OFFSET.",
		"links": [
			["in", "bus", "LD.IR2", ""],
			["out", "ext", "", "rest of the offset"],
		],
		"when": "BL (the only common 32-bit instruction).",
	},
	"fsm": {
		"title": "FSM — control unit",
		"kind": "Finite state machine",
		"what": "Runs the datapath. Each clock cycle it is in one state, asserts that state's control signals, and chooses the next state.",
		"how": "Every instruction begins FETCH_ADDR → FETCH_MEMORY → FETCH_IR. In DECODE it reads IR and picks the path for this instruction (e.g. EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT for a load). The signals it asserts — gates (GateX puts X on the bus), loads (LD.X makes X capture its input at the clock edge), mux selects and the ALU function (ALUK) — are listed in the cycle panel. For conditional branches it uses BranchTaken from COND EVAL.",
		"links": [
			["in", "ir", "", "opcode"],
			["in", "cond", "BranchTaken", "take the branch or not"],
			["out", "", "", "control signals to every block (not drawn as wires)"],
		],
		"when": "Every cycle — the box shows the state that runs next.",
	},
	"ext": {
		"title": "IMM / OFFSET — immediate extractor",
		"kind": "Combinational logic",
		"what": "Extracts constants from the instruction bits and widens them to 32 bits.",
		"how": "Depending on the instruction it takes a field from IR (and IR2), then zero-extends it (ZEXT) or sign-extends it (SEXT) and scales it: ×4 for word offsets, ×2 for halfword offsets and branch offsets, ×1 for bytes. The result goes to ADDR2MUX (addresses, branches) or SR2MUX (ALU immediates like ADDS R0, #5).",
		"links": [
			["in", "ir", "", ""],
			["in", "ir2", "", "32-bit encodings"],
			["out", "addr2mux", "ADDR2MUX=ZEXT / SEXT", ""],
			["out", "sr2mux", "SR2MUX=IMM", ""],
		],
		"when": "DECODE onward, whenever an instruction has an immediate.",
	},
	"cond": {
		"title": "COND EVAL — branch condition",
		"kind": "Combinational logic",
		"what": "Decides whether a conditional branch (BEQ, BNE, BLT, …) is taken.",
		"how": "It compares the condition field IR[11:8] against the N, Z, C and V flags (e.g. EQ is Z = 1, LT is N ≠ V) and sends BranchTaken to the FSM. If it is 1 the FSM selects PCMUX=ADDER and asserts LD.PC; if 0 the PC keeps its sequential value.",
		"links": [
			["in", "flags", "", "N Z C V"],
			["out", "fsm", "BranchTaken", "control (purple dashed)"],
		],
		"when": "EXECUTE_PC of B<cond>.",
	},
	"vec": {
		"title": "VECTOR ADDR",
		"kind": "Constant source",
		"what": "Provides the exception vector address for a supervisor call.",
		"how": "On SVC entry it drives the vector onto the bus (GateVEC). picosim then performs the call directly — for example putchar (SVC #2) and getchar (SVC #3) — instead of running a handler (a simplified exception model).",
		"links": [["out", "bus", "GateVEC", ""]],
		"when": "SVC_CALL (putchar, getchar).",
	},
	"bus": {
		"title": "BUS — shared 32-bit bus",
		"kind": "Shared wires with tri-state gates",
		"what": "The single path most values take between blocks.",
		"how": "In any cycle exactly one gate drives it: GatePC, GateADDR, GateALU, GateMDR or GateVEC (drawn as triangles). Every block whose load signal is asserted (LD.MAR, LD.IR, LD.REG, …) captures the bus value at the clock edge. Because there is only one bus, moving two values needs two cycles — which is why instructions take several cycles. The value on the bus is shown above it.",
		"links": [
			["in", "pc", "GatePC", ""], ["in", "adder", "GateADDR", ""],
			["in", "alu", "GateALU", ""], ["in", "loadext", "GateMDR", "memory data"],
			["in", "vec", "GateVEC", ""],
			["out", "mar", "LD.MAR", ""], ["out", "ir", "LD.IR", ""], ["out", "ir2", "LD.IR2", ""],
			["out", "regfile", "LD.REG", ""], ["out", "storealign", "LD.MDR", "store data"],
			["out", "clrt", "PCMUX=BUS", ""], ["out", "sett", "SET THUMB BIT", ""],
		],
		"when": "Almost every cycle.",
	},
	"mar": {
		"title": "MAR — Memory Address Register",
		"kind": "Register",
		"what": "Holds the address for the next memory or I/O access.",
		"how": "Loaded from the bus (LD.MAR): with the PC during fetch, or with the address adder's result in EVALUATE_ADDRESS. Memory reads and writes always use MAR's address. For PUSH/POP/LDM/STM its +4 incrementer steps it to the next word between transfers.",
		"links": [
			["in", "bus", "LD.MAR", ""],
			["out", "mem", "MEM.EN", "address"],
			["in", "marinc", "MAR+4", ""],
		],
		"when": "FETCH_ADDR; EVALUATE_ADDRESS of every load and store.",
	},
	"marinc": {
		"title": "+4 — MAR incrementer",
		"kind": "Combinational adder",
		"what": "Adds 4 to MAR so a multi-register transfer can move to the next word.",
		"how": "In the same cycle as each word transfer of PUSH, POP, LDM and STM, the FSM also asserts MAR+4: MAR loads its own value + 4, ready for the next word, without using the bus or the address adder.",
		"links": [
			["in", "mar", "", ""],
			["out", "mar", "MAR+4", ""],
		],
		"when": "PUSH, POP, LDMIA, STMIA with more than one register.",
	},
	"mem": {
		"title": "MEMORY / I-O",
		"kind": "Memory (64 KB RAM + memory-mapped I/O)",
		"what": "Stores the program and its data, and connects to the Pico's GPIO peripherals.",
		"how": "RAM covers 0x0000–0xFFFF: the OS at 0x0000, your code from 0x3000, the stack growing down from the top. Addresses 0x10000 and up are I/O registers (SIO at 0xD0000000, IO_BANK0 at 0x40014000, PADS_BANK0 at 0x4001C000); writing them changes the pins on the Pico board. An access takes a cycle of its own: with MEM.EN asserted, a read (R/W=READ) copies M[MAR] into MDR, a write (R/W=WRITE) stores MDR at M[MAR].",
		"links": [
			["in", "mar", "MEM.EN", "address"],
			["out", "mdr", "R/W=READ", "read data"],
			["in", "mdr", "R/W=WRITE", "write data"],
		],
		"when": "FETCH_MEMORY every instruction; FETCH_OPERANDS of loads; STORE_RESULT of stores.",
	},
	"mdr": {
		"title": "MDR — Memory Data Register",
		"kind": "Register",
		"what": "Buffers every value going to or coming from memory.",
		"how": "On a read, memory loads MDR; the next cycle GateMDR puts it on the bus (through LOAD EXT) for IR or a register. On a write, the store data is first loaded into MDR from the bus (through STORE ALIGN), then memory copies it at M[MAR].",
		"links": [
			["in", "mem", "R/W=READ", ""],
			["out", "mem", "R/W=WRITE", ""],
			["out", "loadext", "GateMDR", ""],
			["in", "storealign", "LD.MDR", ""],
		],
		"when": "Every fetch (instruction halfword); every load and store.",
	},
	"loadext": {
		"title": "LOAD EXT — load extender",
		"kind": "Combinational logic",
		"what": "Turns a byte or halfword read from memory into a full 32-bit value.",
		"how": "It selects the right byte lanes from MDR and zero-extends (LDRB, LDRH) or sign-extends (LDRSB, LDRSH) them. Words pass through unchanged. Its output drives the bus when GateMDR is asserted.",
		"links": [
			["in", "mdr", "GateMDR", ""],
			["out", "bus", "GateMDR", ""],
		],
		"when": "FETCH_IR; STORE_RESULT of loads.",
	},
	"storealign": {
		"title": "STORE ALIGN",
		"kind": "Combinational logic",
		"what": "Prepares store data for memory.",
		"how": "It places the byte or halfword being stored into the correct byte lanes and sets the byte enables, so STRB / STRH only change the bytes they should. Data arrives from the register via the ALU (ALUK=PASS) and the bus, and goes into MDR.",
		"links": [
			["in", "bus", "STORE ALIGN", "value from the ALU"],
			["out", "mdr", "LD.MDR", ""],
		],
		"when": "FETCH_OPERANDS of STR, STRB, STRH, PUSH, STMIA.",
	},
	"regfile": {
		"title": "REGISTER FILE",
		"kind": "Register array (R0–R12, SP, LR)",
		"what": "The registers your program uses.",
		"how": "Two read ports, SR1 and SR2, can read any two registers at once; they feed ALU A and SR2MUX (and the address muxes). One write port, DR, loads the bus value into a register at the clock edge when LD.REG is asserted (LD.SP / LD.LR for SP and LR). The register numbers come from the instruction fields selected by the FSM. The PC is kept separately.",
		"links": [
			["out", "alu", "SR1", "ALU A input"],
			["out", "sr2mux", "SR2", ""],
			["out", "addr1mux", "ADDR1MUX=SR1 / SP", ""],
			["in", "bus", "LD.REG", "result to DR"],
			["in", "sett", "LD.LR", "return address into LR"],
		],
		"when": "Most instructions read it in FETCH_OPERANDS and write it in EXECUTE_COMMIT or STORE_RESULT.",
	},
	"sett": {
		"title": "SET THUMB — set bit 0",
		"kind": "Combinational logic",
		"what": "Marks a return address as Thumb code before it is saved in LR.",
		"how": "In the LINK state of BL and BLX the PC (already pointing at the next instruction) goes onto the bus (GatePC), this block sets bit 0 (value | 1) and LD.LR saves it in LR. Later BX LR or POP {PC} returns there, and CLR THUMB removes the bit again.",
		"links": [
			["in", "bus", "SET THUMB BIT", "return address"],
			["out", "regfile", "LD.LR", "into LR"],
		],
		"when": "LINK state of BL and BLX.",
	},
	"sr2mux": {
		"title": "SR2MUX — ALU B selector",
		"kind": "Multiplexer",
		"what": "Chooses the ALU's second (B) operand.",
		"how": "Either the register from read port SR2 (ADDS R2, R0, R1) or an immediate from IMM / OFFSET (ADDS R2, R0, #5), picked with SR2MUX=IMM.",
		"links": [
			["in", "regfile", "SR2", ""],
			["in", "ext", "SR2MUX=IMM", ""],
			["out", "alu", "", "B input"],
		],
		"when": "FETCH_OPERANDS of data-processing instructions.",
	},
	"alu": {
		"title": "ALU — Arithmetic Logic Unit",
		"kind": "Combinational unit with latched inputs",
		"what": "Does the arithmetic and logic: ADD, SUB, ADC, SBC, RSB, AND, ORR, EOR, BIC, MVN, shifts (LSL, LSR, ASR, ROR), MUL, extends (SXTB, UXTH…), byte reverses (REV…), compare (CMP, CMN, TST) and PASS.",
		"how": "In FETCH_OPERANDS its inputs are latched: A from SR1, B from SR2MUX. For a data-processing instruction it computes the function selected by ALUK in the next cycle (EXECUTE_COMMIT). GateALU puts the result on the bus for the register file (or MDR, or the PC); LD.CC loads the N Z C V flags from it. PASS just forwards A, used to move a register's value onto the bus (store data, BX target). CMP / TST set flags without writing a register.",
		"links": [
			["in", "regfile", "SR1", "A input"],
			["in", "sr2mux", "", "B input"],
			["out", "bus", "GateALU", "result"],
			["out", "flags", "LD.CC", ""],
		],
		"when": "EXECUTE_COMMIT of data-processing; FETCH_OPERANDS of stores (PASS); EXECUTE_PC of BX.",
	},
	"flags": {
		"title": "N Z C V — condition flags",
		"kind": "Register (4 bits)",
		"what": "Record facts about the last flag-setting result.",
		"how": "N = result negative (bit 31), Z = result zero, C = carry out (or no borrow on subtract), V = signed overflow. They are loaded from the ALU only when LD.CC is asserted — instructions ending in S (ADDS, MOVS…) and CMP, CMN, TST. Conditional branches read them through COND EVAL.",
		"links": [
			["in", "alu", "LD.CC", ""],
			["out", "cond", "", ""],
		],
		"when": "EXECUTE_COMMIT of flag-setting instructions.",
	},
}


static func title(id: String) -> String:
	return INFO[id]["title"] if INFO.has(id) else id


## BBCode for one component; `names` maps ids to display names for the links.
static func bbcode(id: String, names: Dictionary) -> String:
	if not INFO.has(id):
		return ""
	var e: Dictionary = INFO[id]
	var t := "[font_size=17][b]%s[/b][/font_size]\n[color=#7f8794]%s[/color]\n\n" % [e["title"], e["kind"]]
	t += "[color=#61afef][b]What it does[/b][/color]\n%s\n\n" % e["what"]
	t += "[color=#61afef][b]How it works[/b][/color]\n%s\n\n" % e["how"]
	var ins := ""
	var outs := ""
	for l in e["links"]:
		var other: String = names.get(l[1], l[1])
		var line := "• [b]%s[/b]" % other if other != "" else "•"
		if l[2] != "":
			line += "  [color=#c678dd][code]%s[/code][/color]" % l[2]
		if l[3] != "":
			line += "  [color=#9da5b4]— %s[/color]" % l[3]
		if l[0] == "in":
			ins += line + "\n"
		else:
			outs += line + "\n"
	t += "[color=#61afef][b]Connects to[/b][/color]\n"
	if ins != "":
		t += "[color=#7f8794]receives from[/color]\n" + ins
	if outs != "":
		t += "[color=#7f8794]sends to[/color]\n" + outs
	t += "\n[color=#61afef][b]When it is used[/b][/color]\n%s" % e["when"]
	return t
