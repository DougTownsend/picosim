extends RefCounted
## The control unit's (FSM's) states on the multi-cycle datapath: what each
## one does, the control signals it asserts and which instructions use it.
## The user guide's "FSM states" topic is built from this file, and the cycle
## inspector shows each state's summary when you hover it.
##
## The facts come from picosim_core.cpp (the build_* functions that lay out
## each instruction's states); `picosim --cycle-table` / docs/cycles.md list
## every sequence and count.

## The six phases of the instruction cycle, in order, with the cycle
## inspector's chip for each and the states that belong to it.
const PHASES := [
	["FETCH", "F", "Read the instruction from memory into IR (and IR2).",
		["FETCH_ADDR", "FETCH_MEMORY", "FETCH_IR", "FETCH2_ADDR", "FETCH2_MEMORY", "FETCH2_IR"]],
	["DECODE", "D", "Work out what the instruction is and which states come next.", ["DECODE"]],
	["EVALUATE ADDRESS", "EA", "Compute a memory address with the address adder.", ["EVALUATE_ADDRESS"]],
	["FETCH OPERANDS", "FO", "Get the values the instruction works on: registers into the ALU, or memory into MDR.", ["FETCH_OPERANDS"]],
	["EXECUTE", "EX", "Do the work: the ALU computes, or the PC changes, or the OS is called.",
		["EXECUTE_COMMIT", "EXECUTE_PC", "SVC_CALL", "HALT"]],
	["STORE RESULT", "SR", "Put the result where it belongs: a register, memory, LR or the base register.",
		["STORE_RESULT", "LINK", "WRITEBACK"]],
]

const ORDER := ["FETCH_ADDR", "FETCH_MEMORY", "FETCH_IR", "FETCH2_ADDR", "FETCH2_MEMORY", "FETCH2_IR",
	"DECODE", "EVALUATE_ADDRESS", "FETCH_OPERANDS", "EXECUTE_COMMIT", "EXECUTE_PC", "STORE_RESULT",
	"LINK", "WRITEBACK", "SVC_CALL", "HALT"]

## name → {phase, summary (one line), rtl (what is loaded at the clock edge),
## signals, uses: [[instructions, what the state does for them], …], note}
const STATES := {
	"FETCH_ADDR": {
		"phase": "FETCH",
		"summary": "MAR ← PC, and the PC steps on to the next halfword.",
		"rtl": "MAR ← PC\nPC ← PC + 2",
		"signals": "GatePC  LD.MAR  PCINC=+2  PCMUX=PC+2  LD.PC",
		"uses": [["every instruction", "its first clock cycle"]],
		"note": "The PC drives the bus through GatePC so MAR can capture the address. In the same cycle the +2 incrementer feeds PC + 2 through PCMUX, so after this cycle the PC already points past the halfword being fetched.",
	},
	"FETCH_MEMORY": {
		"phase": "FETCH",
		"summary": "Memory is read at MAR: the instruction halfword goes into MDR.",
		"rtl": "MDR ← M[MAR]   (16 bits)",
		"signals": "MEM.EN  R/W=READ  LD.MDR",
		"uses": [["every instruction", "its second clock cycle"]],
		"note": "Nothing drives the bus: memory's data output goes straight into MDR.",
	},
	"FETCH_IR": {
		"phase": "FETCH",
		"summary": "IR ← MDR: the instruction is now where the FSM can read it.",
		"rtl": "IR ← MDR",
		"signals": "GateMDR  LD.IR",
		"uses": [["every instruction", "its third clock cycle"]],
		"note": "If IR's top five bits are 11101, 11110 or 11111 the instruction is 32 bits long, so the FSM goes on to FETCH2_ADDR for its second halfword; otherwise the next state is DECODE.",
	},
	"FETCH2_ADDR": {
		"phase": "FETCH",
		"summary": "Second fetch of a 32-bit instruction: MAR ← PC, PC ← PC + 2.",
		"rtl": "MAR ← PC\nPC ← PC + 2",
		"signals": "GatePC  LD.MAR  PCINC=+2  PCMUX=PC+2  LD.PC",
		"uses": [["BL, DMB, DSB, ISB, MRS, MSR", "the 32-bit instructions of the Cortex-M0+"]],
		"note": "The same as FETCH_ADDR, for the instruction's second halfword.",
	},
	"FETCH2_MEMORY": {
		"phase": "FETCH",
		"summary": "Memory is read at MAR: the second halfword goes into MDR.",
		"rtl": "MDR ← M[MAR]   (16 bits)",
		"signals": "MEM.EN  R/W=READ  LD.MDR",
		"uses": [["BL, DMB, DSB, ISB, MRS, MSR", ""]],
		"note": "",
	},
	"FETCH2_IR": {
		"phase": "FETCH",
		"summary": "IR2 ← MDR: IR and IR2 now hold the whole 32-bit instruction.",
		"rtl": "IR2 ← MDR",
		"signals": "GateMDR  LD.IR2",
		"uses": [["BL, DMB, DSB, ISB, MRS, MSR", ""]],
		"note": "The next state is DECODE.",
	},
	"DECODE": {
		"phase": "DECODE",
		"summary": "The FSM reads IR (and IR2) and sets up the states and selects that follow; nothing is stored.",
		"rtl": "(no register is loaded)",
		"signals": "the selects for the rest of the instruction, for example DR=R2  SR1=R0  SR2=R1  ALUK=ADD  SIZE=word  COND=EQ",
		"uses": [
			["every instruction", "its fourth cycle (seventh for a 32-bit one)"],
			["NOP, YIELD, WFE, WFI, SEV, DMB, DSB, ISB", "the last state: there is nothing more to do (picosim has no interrupts or events, so WFI and WFE do not wait)"],
			["CPSID i, CPSIE i", "the last state: PRIMASK is set or cleared here (it is not part of the datapath)"],
			["an undefined instruction", "the CPU stops here with a fault, as the Pico would"],
		],
		"note": "The signals listed in DECODE are what the FSM [i]prepares[/i]: register numbers (DR destination, SR1 / SR2 sources), mux selects, the ALU operation and the access size. It holds DR and SIZE for the rest of the instruction. No tri-state buffer is on, so the bus is idle.",
	},
	"EVALUATE_ADDRESS": {
		"phase": "EVALUATE ADDRESS",
		"summary": "The address adder computes base + offset; MAR ← the address.",
		"rtl": "MAR ← ADDR1MUX + ADDR2MUX",
		"signals": "ADDR1MUX=…  ADDR2MUX=…  GateADDR  LD.MAR  (SR1=… SR2=…)",
		"uses": [
			["LDR, STR, LDRB, STRB, LDRH, STRH, LDRSB, LDRSH", "base register (ADDR1MUX=SR1) or SP (ADDR1MUX=SP) plus an immediate (ZEXT(IR[…])×4, ×2 or ×1) or a second register (ADDR2MUX=SR2)"],
			["LDR Rd, =constant (literal)", "Align(PC,4) + ZEXT(IR[7:0])×4: the literal pool word"],
			["ADR", "Align(PC,4) + ZEXT(IR[7:0])×4; the sum is not loaded into MAR yet, it goes to the register in STORE_RESULT"],
			["PUSH", "SP − 4 × (number of registers): the lowest address written (ADDR2MUX=-4n)"],
			["POP, LDM, STM", "the base register itself (ADDR2MUX=0)"],
		],
		"note": "The address adder is separate from the ALU, so computing an address never disturbs the ALU's inputs.",
	},
	"FETCH_OPERANDS": {
		"phase": "FETCH OPERANDS",
		"summary": "The instruction's operands are fetched: registers into the ALU's A / B latches, memory into MDR, or store data into MDR.",
		"rtl": "ALU_A ← SR1;  ALU_B ← SR2 or immediate\n— or —  MDR ← M[MAR]\n— or —  MDR ← register (through the ALU)",
		"signals": "depends on the instruction (below)",
		"uses": [
			["ADDS, SUBS, MOVS, CMP, ANDS, LSLS, MULS, MOV, ADD SP … (operate)", "ALU_A ← SR1 and ALU_B ← SR2, or the immediate through SR2MUX (SR2MUX=IMM): LD.ALUA LD.ALUB. MOV, MVN, NEG, SXTB … use only B"],
			["LDR … (loads), and POP / LDM once per register", "MDR ← M[MAR] in the access size: MEM.EN R/W=READ LD.MDR (and IOSEL=IO for an I/O register at 0x10000 and up)"],
			["STR … (stores), and PUSH / STM once per register", "MDR ← the register being stored, passed through the ALU: SR1=… ALUK=PASS GateALU LD.MDR (STORE ALIGN keeps only the byte or halfword for STRB / STRH)"],
			["BX, BLX", "ALU_A ← the register holding the target address: SR1=… LD.ALUA"],
		],
		"note": "",
	},
	"EXECUTE_COMMIT": {
		"phase": "EXECUTE",
		"summary": "The ALU computes, and the result is committed: DR ← result, flags ← N Z C V.",
		"rtl": "DR ← ALU(ALU_A, ALU_B)\nNZCV ← the ALU's flags   (if the instruction sets them)",
		"signals": "ALUK=…  GateALU  DR=…  LD.REG  LD.CC",
		"uses": [
			["ADDS, SUBS, ANDS, LSLS, MOVS … (operate)", "the result goes over the bus into the destination register, and LD.CC loads the flags for the S forms"],
			["CMP, CMN, TST", "only the flags change (LD.CC): no GateALU, no register is written"],
			["MOV, ADD (high registers), ADD SP, SXTB, REV …", "the register is written; the flags are not"],
			["MOV PC, Rm / ADD PC, Rm", "the result goes to the PC instead (CLR THUMB BIT, PCMUX=BUS, LD.PC): a branch"],
			["MRS Rd, <special register>", "GateSYS puts APSR, PRIMASK, CONTROL, MSP or PSP on the bus and Rd loads it. These registers are not drawn on the diagram"],
			["MSR <special register>, Rn", "Rn passes through the ALU onto the bus and the special register loads it (LD.SYS); MSR APSR sets the flags"],
		],
		"note": "This is where an operate instruction does its real work, in a single cycle: compute and write back together.",
	},
	"EXECUTE_PC": {
		"phase": "EXECUTE",
		"summary": "The PC changes: a branch, call or return goes to its target (or a branch not taken leaves the PC alone).",
		"rtl": "PC ← target",
		"signals": "PCMUX=ADDER or PCMUX=BUS, LD.PC (BranchTaken=0/1 for B<cond>)",
		"uses": [
			["B label", "PC ← PC + offset: the address adder (ADDR1MUX=PC, ADDR2MUX=SEXT(offset)×2) feeds PCMUX=ADDER. Here PC means the branch's own address + 4"],
			["B<cond> label (BEQ, BNE, BLT …)", "COND EVAL compares the flags with the condition and tells the FSM BranchTaken. 1: as B. 0: no LD.PC, so the PC keeps the address of the next instruction"],
			["BL label", "PC ← PC + offset, after LINK has saved the return address"],
			["BX Rm, BLX Rm", "PC ← ALU_A (passed through the ALU) with bit 0 cleared by CLR THUMB (PCMUX=BUS). Bit 0 must be 1 (Thumb), or the CPU faults as the Pico would"],
			["POP {…, PC}", "PC ← MDR (the popped return address) with bit 0 cleared; MAR ← MAR + 4"],
		],
		"note": "",
	},
	"STORE_RESULT": {
		"phase": "STORE RESULT",
		"summary": "The result is stored: a loaded value into its register, or store data into memory.",
		"rtl": "DR ← LOAD EXT(MDR)\n— or —  M[MAR] ← MDR",
		"signals": "depends on the instruction (below)",
		"uses": [
			["LDR, LDRB, LDRH, LDRSB, LDRSH, LDR =constant", "DR ← MDR through LOAD EXT, which zero- or sign-extends a byte or halfword to 32 bits: LOAD EXT=… GateMDR DR=… LD.REG"],
			["STR, STRB, STRH", "M[MAR] ← MDR: MEM.EN R/W=WRITE (IOSEL=IO for an I/O register)"],
			["ADR", "DR ← the address adder's sum: GateADDR LD.REG"],
			["POP, LDM (once per register)", "the register ← MDR, and MAR ← MAR + 4 for the next word"],
			["PUSH, STM (once per register)", "M[MAR] ← MDR, and MAR ← MAR + 4 for the next word"],
		],
		"note": "",
	},
	"LINK": {
		"phase": "STORE RESULT",
		"summary": "LR ← the return address with bit 0 set, so the called function can come back.",
		"rtl": "LR ← PC | 1",
		"signals": "GatePC  SET THUMB BIT  LD.LR",
		"uses": [["BL, BLX", "before EXECUTE_PC changes the PC"]],
		"note": "The PC already points at the instruction after the call (it stepped on during fetch), which is exactly where the function must return to. Bit 0 = 1 marks it as Thumb code, so BX LR and POP {PC} can use it.",
	},
	"WRITEBACK": {
		"phase": "STORE RESULT",
		"summary": "The base register moves past the words transferred: SP ← SP ∓ 4n (PUSH / POP) or Rn ← Rn + 4n (STM / LDM).",
		"rtl": "SP ← SP − 4n   (PUSH)\nSP ← SP + 4n   (POP)\nRn ← Rn + 4n   (STM, LDM)",
		"signals": "ADDR1MUX=SP or SR1  ADDR2MUX=-4n or +4n  GateADDR  LD.SP or LD.REG",
		"uses": [["PUSH, POP, STM, LDM", "the last state. LDM skips it when the base register is in the list (it was just loaded)"]],
		"note": "n is the number of registers in the list. The address adder computes the new value, so the ALU is not needed.",
	},
	"SVC_CALL": {
		"phase": "EXECUTE",
		"summary": "Supervisor call: GateVEC drives the SVCall vector address 0x0000002C, and picosim performs the OS service.",
		"rtl": "bus ← 0x0000002C (SVCall vector)\nthe OS service runs",
		"signals": "GateVEC  EXCEPTION",
		"uses": [["SVC #n", "#2 putchar, #3 getchar, #1 exit, #0 write"]],
		"note": "On a real Cortex-M0+ an SVC starts exception entry: the CPU saves registers on the stack and jumps to the handler whose address is in the vector table at 0x2C (exception 11 × 4). picosim shows the first step, then runs the service directly instead of simulating a handler. A getchar with no key typed waits in this state.",
	},
	"HALT": {
		"phase": "EXECUTE",
		"summary": "BKPT: the processor stops.",
		"rtl": "(the CPU halts)",
		"signals": "HALT",
		"uses": [["BKPT", "the built-in OS runs BKPT when your main returns, so this is how every program ends"]],
		"note": "After this the Registers panel shows Next state: HALTED. Press Reload to start again. On a real Pico, BKPT stops the CPU only when a debugger is attached; without one it causes a HardFault.",
	},
}

## Each kind of instruction's states after the shared fetch, and its total.
const SEQUENCES := [
	["Operate: ADDS, SUBS, MOVS, CMP, ANDS, LSLS, MULS, MOV, ADD SP, SXTB, REV …", "DECODE → FETCH_OPERANDS → EXECUTE_COMMIT", 6],
	["Load: LDR, LDRB, LDRH, LDRSB, LDRSH, LDR =constant", "DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT", 7],
	["Store: STR, STRB, STRH", "DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT", 7],
	["ADR", "DECODE → EVALUATE_ADDRESS → STORE_RESULT", 6],
	["B, B<cond>", "DECODE → EXECUTE_PC", 5],
	["BX", "DECODE → FETCH_OPERANDS → EXECUTE_PC", 6],
	["BLX", "DECODE → FETCH_OPERANDS → LINK → EXECUTE_PC", 7],
	["BL (32-bit)", "FETCH2_ADDR → FETCH2_MEMORY → FETCH2_IR → DECODE → LINK → EXECUTE_PC", 9],
	["PUSH, STM (n registers)", "DECODE → EVALUATE_ADDRESS → (FETCH_OPERANDS → STORE_RESULT) × n → WRITEBACK", "6 + 2n"],
	["POP, LDM (n registers)", "DECODE → EVALUATE_ADDRESS → (FETCH_OPERANDS → STORE_RESULT) × n → WRITEBACK; for PC the last pair is FETCH_OPERANDS → EXECUTE_PC; an LDM whose base register is in the list has no WRITEBACK (5 + 2n)", "6 + 2n"],
	["SVC", "DECODE → SVC_CALL", 5],
	["BKPT", "DECODE → HALT", 5],
	["NOP, YIELD, WFE, WFI, SEV, CPSID, CPSIE", "DECODE", 4],
	["DMB, DSB, ISB (32-bit)", "FETCH2_ADDR → FETCH2_MEMORY → FETCH2_IR → DECODE", 7],
	["MRS, MSR (32-bit)", "FETCH2_ADDR → FETCH2_MEMORY → FETCH2_IR → DECODE → EXECUTE_COMMIT", 8],
]


static func phase_of(state: String) -> String:
	return STATES[state]["phase"] if STATES.has(state) else ""


## One line for a tooltip: "DECODE (Decode phase) — …".
static func tooltip(state: String) -> String:
	if not STATES.has(state):
		return ""
	var e: Dictionary = STATES[state]
	return "%s (%s phase): %s" % [state, str(e["phase"]).capitalize(), e["summary"]]


## BBCode for one state, in the style of the component cards.
static func entry_bbcode(state: String, title_size := 17) -> String:
	var e: Dictionary = STATES[state]
	var dim := Palette.hex("dim")
	var head := Palette.hex("accent")
	var sig := Palette.hex("purple")
	var t := "[font_size=%d][b]%s[/b][/font_size]\n[color=%s]%s phase[/color]\n\n" % [
		title_size, state, dim, str(e["phase"]).capitalize()]
	t += "%s\n\n" % e["summary"]
	t += "[color=%s][b]At the clock edge[/b][/color]\n[code]%s[/code]\n\n" % [head, e["rtl"]]
	t += "[color=%s][b]Control signals[/b][/color]\n[color=%s][code]%s[/code][/color]\n\n" % [head, sig, e["signals"]]
	t += "[color=%s][b]Used by[/b][/color]\n" % head
	for u in e["uses"]:
		t += "• [b]%s[/b]" % u[0] + ("  [color=%s]— %s[/color]" % [Palette.hex("muted"), u[1]] if u[1] != "" else "") + "\n"
	if e["note"] != "":
		t += "\n" + str(e["note"]) + "\n"
	return t
