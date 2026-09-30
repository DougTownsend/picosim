want this to be gui that is implemented in godot 
have godot installed already in command line
rough idea with what we have with registers in top left, memory in bottom left, and on the right, there will be text I/O from USB serial and the option to show cpu block diagrams with per cycle visuals.

have it so the memory follows the program counter so it shows as the instructions are running
make sure it cna simulate a multi-step per instruction architecture

let it go over the github repo with the textbook covering ece information. explains how we want each instruction to be multiple cycles, similar to LC3

on the right side of the GUI, have a button to pull up the diagram and cycle through each step/cycle and what it does for each given instruction. Show what it would do in the given context and display what is actually being changed. for example, changes to register, branches, etc.

on the CPU view, there is a diagram on the textbook. It's not great, but it does have a text-based description. Do not use it; create a native godot diagram that uses arrows to point to each component and what it does
