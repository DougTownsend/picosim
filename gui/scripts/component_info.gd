extends RefCounted
## Detailed descriptions of every block on the CPU diagram, shown in the
## diagram's info card (click a block) and in the user guide.
##
## Keys match the box ids in diagram_view.gd, plus "bus".  Each entry has:
##   kind  — one-line category
##   what  — what the block is for
##   how   — how it operates, cycle by cycle
##   links — [direction, other block, control signal, note]; direction is
##           "in" (data arrives from `other`), "out" (data goes to `other`)
##           or "ctrl" (`other` drives this block's control input)
##   when  — instructions / states where you will see it working

const ORDER := ["pc", "inc", "pcmux", "clrt", "align", "addr1mux", "addr2mux", "adder",
	"ir", "ir2", "fsm", "ext", "cond", "vec", "bus", "gatePC", "gateAdder", "gateMDR",
	"gateALU", "gateVector", "mar", "marinc", "mem", "mdr",
	"loadext", "storealign", "regfile", "sett", "sr2mux", "alu", "flags"]

const INFO := {
	"pc": {
		"title": "PC — Program Counter",
		"kind": "Register (loads at the clock edge)",
		"what": "Holds the address of the next instruction halfword to fetch. Every instruction starts by copying the PC to MAR.",
		"how": "In FETCH_ADDR the PC drives the bus through gatePC (GatePC) so MAR can capture it; in the same cycle the +2 incrementer and PCMUX feed PC + 2 back and LD.PC loads it at the clock edge — so the box still shows the old PC during FETCH_ADDR and the new one from FETCH_MEMORY on. Later in the instruction a branch can load a new value through PCMUX. Because the PC register is stepped during fetch, it holds A + 2 after a 16-bit instruction at address A (A + 4 after BL); an instruction that reads the PC as an operand always gets A + 4, as ARMv6-M defines.",
		"links": [
			["out", "gatePC", "GatePC", "to the bus, then MAR"],
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
		"how": "It is an adder drawn sideways: the PC enters its long side and PC + 2 leaves its short side. PCINC (its control input, from the FSM) selects the increment; this model fetches one halfword at a time, so it is always +2 and BL simply fetches twice. The result only matters when PCMUX selects it (PCMUX=PC+2) and LD.PC is asserted, which happens in every fetch cycle FETCH_ADDR / FETCH2_ADDR.",
		"links": [
			["in", "pc", "", "current PC"],
			["out", "pcmux", "PCMUX=PC+2", "sequential next PC"],
			["ctrl", "fsm", "PCINC", "selects +2"],
		],
		"when": "FETCH_ADDR and FETCH2_ADDR.",
	},
	"pcmux": {
		"title": "PCMUX — next-PC selector",
		"kind": "Multiplexer (select lines from the FSM)",
		"what": "Chooses which value the PC loads next.",
		"how": "Its three data inputs enter the long (top) side: PC+2 (sequential fetch), ADDER (a branch target computed by the address adder) and BUS through CLR THUMB (an address held in a register: BX, BLX, POP {PC}, MOV/ADD PC). The chosen value leaves the short (bottom) side toward the PC. The select lines enter the slanted side; the FSM sets them and asserts LD.PC in the same cycle. For a conditional branch that is not taken the FSM simply does not assert LD.PC, so the PC keeps the sequential value it loaded during fetch.",
		"links": [
			["in", "inc", "PCMUX=PC+2", ""],
			["in", "adder", "PCMUX=ADDER", "branch target"],
			["in", "clrt", "PCMUX=BUS", "register target"],
			["out", "pc", "LD.PC", ""],
			["ctrl", "fsm", "PCMUX", "select: PC+2 / ADDER / BUS"],
		],
		"when": "Fetch (PC+2); EXECUTE_PC of branches.",
	},
	"clrt": {
		"title": "CLR THUMB — clear bit 0",
		"kind": "Combinational logic",
		"what": "Turns a Thumb 'interworking' address (bit 0 = 1) into a halfword-aligned fetch address.",
		"how": "Addresses stored in LR or used by BX have bit 0 set to mean 'Thumb state'. When such a value goes from the bus into the PC, this block forces bit 0 to 0 (value & ~1) before it reaches PCMUX. The bus itself still carries the value with bit 0 set.",
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
		"how": "Thumb defines the PC read value as A + 4, where A is the address of the instruction being executed. After fetch the PC register already points past the instruction (A + 2 for a 16-bit instruction, A + 4 after BL's two halfwords), so this block forms A + 4 from it. With ADDR1MUX=PC it passes A + 4 on unchanged (B, B<cond>, BL). With ADDR1MUX=Align(PC,4) it rounds A + 4 down to a multiple of 4 (clears bits 1:0) for LDR Rd, =literal / LDR Rd, [PC, #imm] and ADR, whose offsets count words from a word-aligned base.",
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
		"how": "Its inputs enter the long (top) side: PC or Align(PC,4) for PC-relative addressing, a register from the register file's SR1 port (LDR R2, [R0, #4]) or SP (LDR R2, [SP, #4], PUSH, POP). The chosen base leaves the short (bottom) side into the address adder. The select enters the slanted side; the FSM sets it in DECODE and holds it through the cycle that uses the adder.",
		"links": [
			["in", "align", "ADDR1MUX=PC / Align", ""],
			["in", "regfile", "ADDR1MUX=SR1 / SP", "shown as the 'SR1 / SP' stub"],
			["out", "adder", "", "base"],
			["ctrl", "fsm", "ADDR1MUX", "select"],
		],
		"when": "EVALUATE_ADDRESS of loads/stores; branches.",
	},
	"addr2mux": {
		"title": "ADDR2MUX — address offset selector",
		"kind": "Multiplexer",
		"what": "Chooses the offset (right) input of the address adder.",
		"how": "Its inputs enter the long (top) side: a zero-extended immediate scaled by the access size (ZEXT(IR[10:6])×4 for a word), a sign-extended branch offset ×2 (SEXT), a second register (SR2) for [Rn, Rm] addressing, −4n / +4n for multi-register transfers (n = number of registers), or zero. The immediates come from IMM / OFFSET. The chosen offset leaves the short (bottom) side; the select enters the slanted side.",
		"links": [
			["in", "ext", "ADDR2MUX=ZEXT / SEXT", "immediate or branch offset"],
			["in", "regfile", "ADDR2MUX=SR2", "index register"],
			["out", "adder", "", "offset"],
			["ctrl", "fsm", "ADDR2MUX", "select"],
		],
		"when": "Same cycles as ADDR1MUX; −4n / +4n in EVALUATE_ADDRESS and WRITEBACK of PUSH, POP, LDM, STM.",
	},
	"adder": {
		"title": "ADDRESS ADDER",
		"kind": "Combinational adder (separate from the ALU)",
		"what": "Adds base + offset to form an effective memory address or a branch target.",
		"how": "Its two inputs (base from ADDR1MUX, offset from ADDR2MUX) enter the long (top) side; the sum leaves the short (bottom) side. It has no control input: it always adds, and the FSM decides who uses the sum. Because it is its own adder, address arithmetic never occupies the ALU. The sum either drives the bus through gateAdder (GateADDR) so MAR — or a register, for ADR and the SP update of PUSH/POP — can capture it, or goes straight to PCMUX as a branch target.",
		"links": [
			["in", "addr1mux", "", "base"],
			["in", "addr2mux", "", "offset"],
			["out", "gateAdder", "GateADDR", "to the bus: MAR or a register"],
			["out", "pcmux", "PCMUX=ADDER", "branch target"],
		],
		"when": "EVALUATE_ADDRESS (loads, stores, PUSH/POP); EXECUTE_PC (B, BL); STORE_RESULT of ADR; WRITEBACK of PUSH/POP/LDM/STM (new SP or base register).",
	},
	"ir": {
		"title": "IR — Instruction Register",
		"kind": "Register (16 bits)",
		"what": "Holds the instruction being executed (or the first half of a 32-bit one) for the whole instruction.",
		"how": "Loaded from the bus at the clock edge that ends FETCH_IR (GateMDR + LD.IR), so it shows the new instruction from DECODE on. Its bits are wired to the FSM, which decodes the opcode, and to IMM / OFFSET, which pulls out immediates and register numbers. It does not change again until the next fetch.",
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
			["out", "", "", "control signals to every block: drawn as the short purple stubs into the muxes, the ALU, the +2 incrementer and the tri-state buffers; the load enables (LD.x) are not drawn"],
		],
		"when": "Every cycle — the box shows the state of the cycle on display.",
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
		"how": "On SVC entry it supplies the SVCall vector address, 0x0000002C (SVCall is exception number 11, and each vector-table entry is 4 bytes), and gateVector puts it on the bus. A complete exception entry would read the handler's address from that vector and save a stack frame (Chapter 13). picosim simplifies this: it performs the call directly — for example putchar (SVC #2) and getchar (SVC #3) — and continues with the next instruction.",
		"links": [["out", "gateVector", "GateVEC", "to the bus"]],
		"when": "SVC_CALL (putchar, getchar).",
	},
	"bus": {
		"title": "BUS — shared 32-bit bus",
		"kind": "Shared wires with tri-state gates",
		"what": "The single path most values take between blocks.",
		"how": "Five tri-state buffers (the triangles) can drive it: gatePC, gateAdder, gateALU, gateMDR and gateVector. In any cycle at most one is enabled; the others are high-impedance. Every block whose load signal is asserted (LD.MAR, LD.IR, LD.REG, …) captures the bus value at the clock edge. Because there is only one bus, moving two values needs two cycles — which is why instructions take several cycles. The value on the bus is shown above it.",
		"links": [
			["in", "gatePC", "GatePC", "the PC"], ["in", "gateAdder", "GateADDR", "address adder"],
			["in", "gateALU", "GateALU", "ALU result"], ["in", "gateMDR", "GateMDR", "memory data"],
			["in", "gateVector", "GateVEC", "exception vector"],
			["out", "mar", "LD.MAR", ""], ["out", "ir", "LD.IR", ""], ["out", "ir2", "LD.IR2", ""],
			["out", "regfile", "LD.REG", ""], ["out", "storealign", "LD.MDR", "store data"],
			["out", "clrt", "PCMUX=BUS", ""], ["out", "sett", "SET THUMB BIT", ""],
		],
		"when": "Almost every cycle.",
	},
	"gatePC": {
		"title": "gatePC — PC tri-state buffer",
		"kind": "Tri-state buffer (control: GatePC)",
		"what": "Connects the PC to the bus when GatePC is asserted, so the current fetch address (or a return address) can reach other registers.",
		"how": "A tri-state buffer has a data input, a data output and one control input. While its control signal is 1 the output copies the input, so the PC's value appears on the bus. While it is 0 the output is in the high-impedance state (Z): electrically disconnected, so it neither drives the bus high nor low and another buffer can use the bus. The FSM asserts at most one Gate signal per cycle, because two enabled buffers would fight over the same wires. The buffer stores nothing: the bus carries the value only during the cycle, and whichever registers have their LD signal asserted capture it at the clock edge. In FETCH_ADDR the bus carries the PC to MAR; in the LINK state of BL / BLX it carries the return address to SET THUMB and LR.",
		"links": [
			["in", "pc", "", "the PC's value"],
			["out", "bus", "", "drives the shared bus"],
			["ctrl", "fsm", "GatePC", "1 = drive the bus, 0 = off (Z)"],
		],
		"when": "FETCH_ADDR and FETCH2_ADDR of every instruction; LINK of BL and BLX.",
	},
	"gateAdder": {
		"title": "gateAdder — address-adder tri-state buffer",
		"kind": "Tri-state buffer (control: GateADDR)",
		"what": "Connects the address adder's sum to the bus when GateADDR is asserted, so an effective address can be loaded into MAR or a computed address into a register.",
		"how": "A tri-state buffer has a data input, a data output and one control input. While its control signal is 1 the output copies the input, so the address adder's value appears on the bus. While it is 0 the output is in the high-impedance state (Z): electrically disconnected, so it neither drives the bus high nor low and another buffer can use the bus. The FSM asserts at most one Gate signal per cycle, because two enabled buffers would fight over the same wires. The buffer stores nothing: the bus carries the value only during the cycle, and whichever registers have their LD signal asserted capture it at the clock edge. A branch target does not need it: it reaches PCMUX on its own wire.",
		"links": [
			["in", "adder", "", "base + offset"],
			["out", "bus", "", "to MAR (loads, stores) or a register (ADR, SP update)"],
			["ctrl", "fsm", "GateADDR", "1 = drive the bus, 0 = off (Z)"],
		],
		"when": "EVALUATE_ADDRESS of loads, stores, PUSH, POP, LDM, STM; STORE_RESULT of ADR; WRITEBACK of PUSH, POP, LDM, STM.",
	},
	"gateMDR": {
		"title": "gateMDR — memory-data tri-state buffer",
		"kind": "Tri-state buffer (control: GateMDR)",
		"what": "Connects the memory data (MDR, after LOAD EXT has selected and extended the right bytes) to the bus when GateMDR is asserted.",
		"how": "A tri-state buffer has a data input, a data output and one control input. While its control signal is 1 the output copies the input, so LOAD EXT's value appears on the bus. While it is 0 the output is in the high-impedance state (Z): electrically disconnected, so it neither drives the bus high nor low and another buffer can use the bus. The FSM asserts at most one Gate signal per cycle, because two enabled buffers would fight over the same wires. The buffer stores nothing: the bus carries the value only during the cycle, and whichever registers have their LD signal asserted capture it at the clock edge. It is how a fetched instruction reaches IR, and how loaded data reaches a register or the PC.",
		"links": [
			["in", "loadext", "", "MDR, extended to 32 bits"],
			["out", "bus", "", "to IR, IR2, a register or the PC"],
			["ctrl", "fsm", "GateMDR", "1 = drive the bus, 0 = off (Z)"],
		],
		"when": "FETCH_IR / FETCH2_IR of every instruction; STORE_RESULT of loads; each register of POP and LDM.",
	},
	"gateALU": {
		"title": "gateALU — ALU tri-state buffer",
		"kind": "Tri-state buffer (control: GateALU)",
		"what": "Connects the ALU's result to the bus when GateALU is asserted.",
		"how": "A tri-state buffer has a data input, a data output and one control input. While its control signal is 1 the output copies the input, so the ALU's value appears on the bus. While it is 0 the output is in the high-impedance state (Z): electrically disconnected, so it neither drives the bus high nor low and another buffer can use the bus. The FSM asserts at most one Gate signal per cycle, because two enabled buffers would fight over the same wires. The buffer stores nothing: the bus carries the value only during the cycle, and whichever registers have their LD signal asserted capture it at the clock edge. The ALU's flag outputs do not use it: they go straight to the N Z C V register, which loads them when LD.CC is asserted.",
		"links": [
			["in", "alu", "", "the ALU result"],
			["out", "bus", "", "to a register, MDR (store data) or the PC (BX)"],
			["ctrl", "fsm", "GateALU", "1 = drive the bus, 0 = off (Z)"],
		],
		"when": "EXECUTE_COMMIT of data-processing instructions that write a register; FETCH_OPERANDS of stores and PUSH/STM (ALU PASS); EXECUTE_PC of BX and BLX.",
	},
	"gateVector": {
		"title": "gateVector — exception-vector tri-state buffer",
		"kind": "Tri-state buffer (control: GateVEC)",
		"what": "Connects VECTOR ADDR to the bus when GateVEC is asserted, at the start of exception entry.",
		"how": "A tri-state buffer has a data input, a data output and one control input. While its control signal is 1 the output copies the input, so VECTOR ADDR's value appears on the bus. While it is 0 the output is in the high-impedance state (Z): electrically disconnected, so it neither drives the bus high nor low and another buffer can use the bus. The FSM asserts at most one Gate signal per cycle, because two enabled buffers would fight over the same wires. The buffer stores nothing: the bus carries the value only during the cycle, and whichever registers have their LD signal asserted capture it at the clock edge. For SVC it drives the SVCall vector address 0x0000002C.",
		"links": [
			["in", "vec", "", "vector address"],
			["out", "bus", "", "drives the shared bus"],
			["ctrl", "fsm", "GateVEC", "1 = drive the bus, 0 = off (Z)"],
		],
		"when": "SVC_CALL (putchar, getchar).",
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
		"how": "It is an adder drawn sideways: MAR's value enters its long side and MAR + 4 leaves its short side, back into MAR. In the same cycle as each word transfer of PUSH, POP, LDM and STM, the FSM also asserts MAR+4: at the clock edge MAR loads its own value + 4, ready for the next word, without using the bus or the address adder. The memory access in that cycle still uses the old MAR, because MAR only changes at the edge.",
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
		"how": "RAM covers 0x0000–0xFFFF: the OS at 0x0000, your code from 0x3000, the stack growing down from the top. Addresses 0x10000 and up are I/O registers (SIO at 0xD0000000, IO_BANK0 at 0x40014000, PADS_BANK0 at 0x4001C000); writing them changes the pins on the Pico board. An access takes a cycle of its own: with MEM.EN asserted, a read (R/W=READ, LD.MDR) puts M[MAR] into MDR at the clock edge, and a write (R/W=WRITE) stores MDR at M[MAR] at the clock edge.",
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
		"how": "On a read, memory loads MDR at the clock edge (LD.MDR); the next cycle LOAD EXT and gateMDR put it on the bus for IR or a register. On a write, the store data is first loaded into MDR from the bus (through STORE ALIGN), then memory copies it at M[MAR].",
		"links": [
			["in", "mem", "R/W=READ", ""],
			["out", "mem", "R/W=WRITE", ""],
			["out", "loadext", "", ""],
			["in", "storealign", "LD.MDR", ""],
		],
		"when": "Every fetch (instruction halfword); every load and store.",
	},
	"loadext": {
		"title": "LOAD EXT — load extender",
		"kind": "Combinational logic",
		"what": "Turns a byte or halfword read from memory into a full 32-bit value.",
		"how": "It selects the right byte lanes from MDR and zero-extends (LDRB, LDRH, and the instruction halfword in FETCH_IR) or sign-extends (LDRSB, LDRSH) them. Words pass through unchanged. Its output reaches the bus through the gateMDR buffer when GateMDR is asserted.",
		"links": [
			["in", "mdr", "", ""],
			["out", "gateMDR", "GateMDR", "to the bus"],
			["ctrl", "fsm", "LOAD EXT", "Z/S + size"],
		],
		"when": "FETCH_IR; STORE_RESULT of loads.",
	},
	"storealign": {
		"title": "STORE ALIGN",
		"kind": "Combinational logic",
		"what": "Prepares store data for memory.",
		"how": "It places the byte or halfword being stored into the correct byte lanes and sets the byte enables, so STRB / STRH only change the bytes they should. Data arrives from the register via the ALU (ALUK=PASS), gateALU and the bus, and goes into MDR (LD.MDR) at the clock edge.",
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
		"how": "Two read ports, SR1 and SR2, can read any two registers at once; they are combinational, so the selected values are available during the cycle and feed ALU A, SR2MUX and the address muxes. One write port, DR, loads the bus value into a register at the clock edge when LD.REG is asserted (LD.SP / LD.LR for SP and LR) — the new value appears from the next cycle on. The register numbers come from the instruction fields selected by the FSM. The PC is kept separately.",
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
		"how": "In the LINK state of BL and BLX the PC (already pointing at the next instruction) goes onto the bus through gatePC, this block sets bit 0 (value | 1) and LD.LR saves it in LR at the clock edge. The bus carries the PC itself; only LR gets the bit. Later BX LR or POP {PC} returns there, and CLR THUMB removes the bit again.",
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
		"how": "Both inputs enter the long (top) side: the register from read port SR2 (ADDS R2, R0, R1) or an immediate from IMM / OFFSET (ADDS R2, R0, #5). The FSM's select (slanted side) picks IMM or REG; the result leaves the short (bottom) side into the ALU's B input.",
		"links": [
			["in", "regfile", "SR2", ""],
			["in", "ext", "SR2MUX=IMM", ""],
			["out", "alu", "", "B input"],
			["ctrl", "fsm", "SR2MUX", "select: REG / IMM"],
		],
		"when": "FETCH_OPERANDS of data-processing instructions.",
	},
	"alu": {
		"title": "ALU — Arithmetic Logic Unit",
		"kind": "Combinational unit with latched inputs",
		"what": "Does the arithmetic and logic: ADD, SUB, ADC, SBC, RSB, AND, ORR, EOR, BIC, MVN, shifts (LSL, LSR, ASR, ROR), MUL, extends (SXTB, UXTH…), byte reverses (REV…), compare (CMP, CMN, TST) and PASS.",
		"how": "Its two inputs (A from SR1, B from SR2MUX) enter the long (top) side; the result and the flags leave the short (bottom) side. ALUK, the operation select, enters the slanted side. In FETCH_OPERANDS the inputs are loaded into the ALU A / ALU B latches at the clock edge (LD.ALUA, LD.ALUB); in the next cycle (EXECUTE_COMMIT) the ALU computes the ALUK function from them. gateALU (GateALU) puts the result on the bus for the register file (or MDR, or the PC); LD.CC loads the N Z C V flags from it. PASS just forwards a register's value straight onto the bus (store data); for BX it forwards the latched target in A. CMP / CMN / TST set flags without writing a register.",
		"links": [
			["in", "regfile", "SR1", "A input"],
			["in", "sr2mux", "", "B input"],
			["out", "gateALU", "GateALU", "result to the bus"],
			["out", "flags", "LD.CC", ""],
			["ctrl", "fsm", "ALUK", "operation select"],
		],
		"when": "EXECUTE_COMMIT of data-processing; FETCH_OPERANDS of stores (PASS); EXECUTE_PC of BX.",
	},
	"flags": {
		"title": "N Z C V — condition flags",
		"kind": "Register (4 bits)",
		"what": "Record facts about the last flag-setting result.",
		"how": "N = result negative (bit 31), Z = result zero, C = carry out of an add (1 = no borrow on a subtract or compare) or the last bit shifted out by a shift, V = signed overflow of an add or subtract. Logical operations (ANDS, ORRS, EORS, BICS, MVNS, MOVS #imm, MULS) change only N and Z. The flags are loaded from the ALU at the clock edge only when LD.CC is asserted — instructions ending in S and CMP, CMN, TST — so a following B<cond> sees them in its own cycles. Conditional branches read them through COND EVAL.",
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
static func bbcode(id: String, names: Dictionary, title_size := 17) -> String:
	if not INFO.has(id):
		return ""
	var e: Dictionary = INFO[id]
	var dim := Palette.hex("dim")
	var head := Palette.hex("accent")
	var t := "[font_size=%d][b]%s[/b][/font_size]\n[color=%s]%s[/color]\n\n" % [title_size, e["title"], dim, e["kind"]]
	t += "[color=%s][b]What it does[/b][/color]\n%s\n\n" % [head, e["what"]]
	t += "[color=%s][b]How it works[/b][/color]\n%s\n\n" % [head, e["how"]]
	var ins := ""
	var outs := ""
	var ctrl := ""
	for l in e["links"]:
		var other: String = names.get(l[1], l[1])
		var line := "• [b]%s[/b]" % other if other != "" else "•"
		if l[2] != "":
			line += "  [color=%s][code]%s[/code][/color]" % [Palette.hex("purple"), l[2]]
		if l[3] != "":
			line += "  [color=%s]— %s[/color]" % [Palette.hex("muted"), l[3]]
		if l[0] == "in":
			ins += line + "\n"
		elif l[0] == "ctrl":
			ctrl += line + "\n"
		else:
			outs += line + "\n"
	t += "[color=%s][b]Connects to[/b][/color]\n" % head
	if ins != "":
		t += "[color=%s]receives from[/color]\n" % dim + ins
	if outs != "":
		t += "[color=%s]sends to[/color]\n" % dim + outs
	if ctrl != "":
		t += "[color=%s]controlled by[/color]\n" % dim + ctrl
	t += "\n[color=%s][b]When it is used[/b][/color]\n%s" % [head, e["when"]]
	return t
