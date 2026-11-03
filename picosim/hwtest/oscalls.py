"""
What a call into the built-in OS does in a Testing run, for the Testing
window: what it does, how picosim and the chip each do it, why it is done
that way, and when it happens in the program.

os.s (picosim) and os_hw.s (the chip) have the same four routines at the
same offsets, so a bl putchar or bl bench_start is the same machine code on
both; only what each routine does to get its work done differs.  Each
explanation is {'name', 'what', 'how', 'why', 'when'}, built from the run's
own values.
"""

from . import image as img

CONSOLE_WHY = ("Under the debugger the chip has no console, so the test runner does the I/O for it. "
               "A real SVC would also start an exception on the chip: it pushes 8 registers on the "
               "stack and jumps through the vector table at 0x2C, which picosim does not model (it "
               "runs the service directly), so the stack and the path through the code would differ "
               "from here on. A BKPT makes the chip do nothing at all, so both machines come back "
               "from the call with the same registers and the comparison carries on.")


def _char(c):
    if c == 10:
        return "a new line ('\\n')"
    if 32 <= c < 127:
        return f"'{chr(c)}'"
    return f"the byte 0x{c:02X}"


def _site(line, src):
    return f"line {line}: {src}" if line else "the program"


def trace_call(name, k, line, src, addr, ret, os_insns, r0_in, r0_out):
    """A call in a step-by-step test.  k counts this routine's calls (from
    1), line/src are the bl's, addr is the routine's picosim address, ret
    where it returns to, os_insns the OS instructions picosim ran."""
    ran = ", ".join(os_insns) if os_insns else "nothing"
    back = (f"The chip: its {name} at 0x{img.to_hw(addr):08X} is a BKPT, so the core stops before "
            f"running anything there. The test runner")
    when = f"Call {k} to {name} in this run, from {_site(line, src)}. It returns to {ret:04X}."
    if name == 'putchar':
        c = r0_in & 0xFF
        return {'name': name,
                'what': f"Prints {_char(c)}, the character in R0 (0x{c:02X}). Everything a test "
                        "prints is also compared when it ends.",
                'how': f"picosim: bl putchar jumps into the OS at {addr:04X} and runs {ran}. "
                       "svc #2 asks picosim to print the character in R0, and bx lr returns. "
                       f"{back} reads R0 over the debug port, adds the character to the chip's "
                       "output, and sets PC to LR, as if putchar had returned.",
                'why': CONSOLE_WHY,
                'when': when}
    if name == 'getchar':
        got = ("the input has run out, so R0 = 0xFFFFFFFF (-1, end of input)"
               if r0_out == 0xFFFFFFFF else f"R0 = 0x{r0_out:02X}, {_char(r0_out)}")
        return {'name': name,
                'what': f"Reads the next character of the test's input into R0: {got}.",
                'how': f"picosim: bl getchar jumps into the OS at {addr:04X} and runs {ran}. "
                       "svc #3 puts the next character of the test's input (its @ Input line, or "
                       f"the Input box) in R0, and bx lr returns. {back} writes the same character "
                       "into R0 over the debug port and sets PC to LR.",
                'why': CONSOLE_WHY + " Both get the same character, so the program takes the same "
                                     "path on both.",
                'when': when}
    which = "start" if name == 'bench_start' else "end"
    return {'name': name,
            'what': f"Marks the {which} of a region to time. A step-by-step test times nothing, so "
                    "here it does nothing on either machine.",
            'how': f"picosim: {name} at {addr:04X} is just bx lr ({ran}). The chip: the test runner "
                   "sets PC to LR without running os_hw.s's routine, so its SysTick read is "
                   "skipped and no register, flag or byte changes.",
            'why': "Stepping one instruction at a time through the debugger takes thousands of "
                   "times longer than the program itself, so a cycle count measured that way means "
                   "nothing. A Timing test runs the same program at full speed instead.",
            'when': when}


def bench_region(row, n_regions):
    """bench_start and bench_stop around one region of a Timing test.  row
    is bench.py's region row, with 'systick' = [start, stop] when the chip
    reported the region."""
    k = row['k']
    name = row.get('name', f"region {k}")
    st = row.get('systick')
    stop_site = row.get('stop_site')
    over = row.get('over')
    sim = row.get('sim')
    slot = img.to_hw(img.BENCH_TABLE + 8 * k)
    how = ("picosim: bench_start and bench_stop are just bx lr. picosim adds up the Table 81 "
           "cycles of each instruction from the return from bench_start to the bl bench_stop")
    how += f": {sim} cycles. " if sim is not None else ". "
    if st:
        how += (f"The chip: bench_start reads the SysTick counter, which counts down by one every "
                f"processor clock cycle, and keeps the value ({st[0]}) at "
                f"0x{img.to_hw(img.BENCH_TABLE + 4):08X}. bench_stop reads it again ({st[1]}) "
                f"and writes the pair to region {k}'s slot in the table, at 0x{slot:08X}. When the "
                f"program has ended, the test runner reads the table over the debug port: "
                f"{st[0]} − {st[1]} = {row.get('raw')} cycles")
        if over is not None and row.get('hw') is not None:
            align = "word-aligned" if stop_site is not None and stop_site % 4 == 0 else "halfword-aligned"
            how += (f", minus {over} for the measuring itself (what the calibration's empty region "
                    f"took with a {align} bl bench_stop) = {row['hw']} cycles for the region.")
        else:
            how += "."
    else:
        how += "The chip did not report this region (the program did not reach its bench_stop)."
    when = f"Region {k} of {n_regions}"
    if row.get('line'):
        when += f": bench_start on line {row['line']}"
        if row.get('stop_line'):
            when += f", bench_stop on line {row['stop_line']}"
    when += (". The chip runs the whole program without stopping, so both SysTick reads happen at "
             "full speed")
    when += (", except that it stops at every putchar or getchar, which is why a region that "
             "calls them is not timed meaningfully." if row.get('calls') else ".")
    return {'name': 'bench_start / bench_stop',
            'what': f"They time region {k} ({name}): the {row.get('insns', 0)} instructions "
                    "between them.",
            'how': how,
            'why': "The debugger cannot time anything: halting the core to read it takes "
                   "milliseconds and stops the clock being measured. So the chip times itself, at "
                   "full speed, with its own cycle counter. Both routines leave every register and "
                   "flag as they found them (bench_stop saves and restores APSR), so the region "
                   "sees the same values it would without them. They sit at the same offsets as in "
                   "os.s, so the region's machine code is byte for byte the same on both machines.",
            'when': when}


def bench_stretch(row, n_regions):
    """The OS calls (or the start and end of main) around one stretch of a
    Timing test of a program with no bench_start / bench_stop.  row is
    bench.py's region row, with 'systick' = [start, stop] when the chip
    reported it."""
    k = row['k']
    st = row.get('systick')
    over = row.get('over')
    sim = row.get('sim')
    start = {'main': "hw_go, which then calls main", 'putchar': "putchar, after its BKPT",
             'getchar': "getchar, after its BKPT"}.get(row.get('after'), "the OS")
    stop = {'putchar': "putchar, before its BKPT", 'getchar': "getchar, before its BKPT",
            'return': "the code main returns to, before its final BKPT"}.get(row.get('until'), "the OS")
    how = ("picosim: putchar and getchar are svc and bx lr. picosim adds up the Table 81 cycles of "
           "the program's own instructions in the stretch, the call or return at its end included")
    how += f": {sim} cycles. " if sim is not None else ". "
    if st:
        how += (f"The chip runs a timed copy of os_hw.s. {start[0].upper() + start[1:]} reads the "
                f"SysTick counter, which counts down by one every processor clock cycle ({st[0]}); "
                f"{stop} reads it again ({st[1]}) and writes the pair to slot {k} of the table at "
                f"0x{img.to_hw(img.BENCH_TABLE + 8 * k):08X}. When the program has ended, the test "
                f"runner reads the table over the debug port: {st[0]} − {st[1]} = {row.get('raw')} "
                "cycles")
        if over is not None and row.get('hw') is not None:
            how += (f", minus {over} for the measuring itself (what the calibration's stretches "
                    f"took beyond picosim's count) = {row['hw']} cycles for the stretch.")
        else:
            how += "."
    else:
        how += "The chip did not report this stretch (it stopped before reaching its end)."
    when = f"Stretch {k} of {n_regions}"
    if row.get('line'):
        when += f": from line {row['line']}"
        if row.get('stop_line'):
            when += f" to line {row['stop_line']}"
    when += (". The chip stops at every putchar and getchar for the test runner to do the I/O, "
             "but both SysTick reads are on the program's side of that stop, so the time the "
             "chip spends stopped is not counted.")
    return {'name': 'putchar / getchar as timers',
            'what': f"With no bench_start / bench_stop in the program, every stretch between OS "
                    f"calls is timed. This is stretch {k} ({row.get('name', '')}): "
                    f"{row.get('insns', 0)} instructions.",
            'how': how,
            'why': "The debugger cannot time anything: halting the core to read it takes "
                   "milliseconds and stops the clock being measured. So the chip times itself, at "
                   "full speed, with its own cycle counter, from the moment the OS hands control "
                   "back to the program until the program next calls it. The timed routines leave "
                   "every register and flag as the program would see them, and putchar and getchar "
                   "stay at the same offsets as in os.s, so the program's machine code is byte for "
                   "byte the same on both machines.",
            'when': when}
