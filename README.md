- GdbPanel is a plugin for more efficient terminal debugging, based on gdb's python api

## Motivation
1. obtain/restore messages (log, printed values) in need
2. exploit full screen by custom layout
3. light weight & available in terminal

## Prerequisite
1. gdb with python >= 3.10
2. python package pygments for code highlighting
3. libstdcxx printer module in your gdb's python search path *(ongoing)*

## Features
### Custom Layout
2. According to your configuration, GdbPanel divides the terminal in serveral **slots**, and render content from different **panes** in them.
    - **slot**: a rectangle area in terminal
    - **pane**: an messages provider whose content will be written inside one slot
3. Design the layout for yourself: number, area ratio, positions of slots are configurable
4. Print messages you need: panes can be reassigned to any slot during debugging
5. Load different layout quickly and on-the-fly

### Restore Inferior Log
1. It's bothering when the debug logs from inferior is flushed away by content from gdb, particularly they even been dropped from terminal's scroll back buffer.
2. To restore the flushed away logs, GdbPanel provide's a logger to record & relay the output from inferior
    - to enable logger, an inferior process must be started by command "panel run $args" instead of "run" directly, for redirecting the log
    - as the logger is implemented by fifo, the inferior must ensure the output stream is flushed in time (e.g. `printf()` followed by `fflush(stdout)` in c/c++), otherwise the logger recieve nothing
    - once a flush operation is missed in a c/c++ inferior, "panel flush" may worth a try
3. Print as less content as possible can also keep the logs. For this application, GdbPanel provides a switch, that when it turned off, the panel will never shows except is manully called.
4. "\t" in log will be replaced by 4 spaces to keep content inside slot

### Scroll
1. to be supported *(patrially refresh by ansi position controll code ?)*

### Available Panes
1. Source
    - shows the c/c++ source code of location of inferior when it stops
    - shows the c/c++ source code of location of breakpoint when it create
    - highlights source code using [pygments](https://pygments.org/)
2. Breakpoints
    - shows breakpoints and watch points
    - shows their index, source file name, line number, function name, condition if exists and hit times
3. Stack
    - shows index, source file name, line number and function name of stack frames
4. ValueHistory
    - shows the history of each "print" command and corresponding output string
    - the printed string of a "print" with length longer than 4 lines will be truncated
5. Watch
    - shows the latest value of expressions added in watch list
6. Log
    - shows (at most 500 lines) logs from inferior, if the panel logger is enabled

## Documents
### Layout Config
- GdbPanel's config is directly written in python code, using data like *dict*, *list*, *int*, *str*
- To customize GdbPanel, just rewrite the **config** attribute of the panel
- Layout config consists of 2 parts: **slots** and **panes**
- **slots** config
    - each slot is defined as a list of int `[id, width, height]`
    - [0, 6, 8] means the slot with 6/10 of terminal's width, 8/10 of terminal's height
    - width/height of any slot cannot exceed 10
    - id can be any int but must be unique
    - Let *S* is a slot
    - positions of the slots are represented by a binary tree:
        1. root locates at top-left of the terminal
        2. *S*.*left_child* is below *S*, and they are aligned on left edge
        3. *S*.*right_child* is on the right of *S*, and they are aligned on top edge
    - the binary tree is written in the "slots" list with following rules:
        1. each slot must define both left/right children, use "None" represent the bsent of child
        2. let *S* = slots[i], then slots[i+1] must be *S*.*right_child* (can be another lot or None)
        3. *S*.*left_child* must be defined, just next to the last element under *S*.*right_child* (i.e. the subtree)
- **panes** config
    - assign panes to slots here, by writing "pane name: slot id"
    - panes not assigned here will be hidden
- example
    ```python
    '''
    the layout of following config (number is slots' id):
       -----------------
       | 0        |  1 |
       |          |    |
       |          |    |
       |          |----|
       |----------|  2 |
       | 3        |    |
       -----------------
    '''
    config = {
        'slots': [[0, 6, 8], [1, 4, 6], None, [2, 4, 4], None, None, [3, 6, 2], None, None],

        'panes': {'Source': 0, 'Watch': 1, 'Stack': 2, 'Breakpoints': 3}
    }
    ```
### Commands
- GdbPanel provides following GDB commands
    1. panel view *PANE* *SLOT* --- assign the *PANE* to *SLOT*.
        - *PANE*, str, name of a pane.
        - *SLOT*, int, index of a slot, defined in layout config
        - if *PANE* already been assigned to a slot, swap panes in the two slot.
        - if *PANE* is hidden, pane in *SLOT* turned to hidden
    2. panel print *EXPRESSION* --- call gdb command "print *EXPRESSION*"
        - *EXPRESSION*, str
        - Panel won't show after this print to avoid flushing the result away
    3. panel silent *COMMAND* --- call gdb to execute *COMMAND*
        - *COMMAND*, str
        - Panel won't show after this command to avoid flushing the result away
    4. panel layout *CONFIG_INDEX* --- change panel's layout with the selected config
        - *CONFIG_INDEX*, int, starts from 0, the index oin **panel.layout_configs**
        - Layout configs for quick changing must be defined as a list in attribute **panel.layout_configs**
    5. panel run *ARGS* --- launch an inferior process with *ARGS*, while redirect its output to logger
    6. panel watch *EXPRESSION* --- add *EXPRESSION* to panel's watch list
    7. panel unwatch *EXPRESSION* --- delete *EXPRESSION* from panel's watch list
    8. panel flush --- try flush inferior (c/c++) process's output stream buffer & read new logs
