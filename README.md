- GdbPanel is a plugin for more efficient terminal debugging, based on gdb's python api
- [中文读我](./DOWO.md)

## Motivation
1. exploit full screen by custom layout
2. record informations & restore in need
3. iterate STL containers during debugging

## Prerequisite
1. gdb with python >= 3.10
2. python package [pygments](https://pygments.org/) for code highlighting
3. have `libstdcxx` module in gdb's python search path
    - `libstdcxx` can be obtained from [this site](https://github.com/gcc-mirror/gcc/tree/master/libstdc%2B%2B-v3/python/libstdcxx) or the gcc directory

## Usage
0. ensure using a gdb configured with a python interpreter 3.10 or later, and the interpreter must able to find the following packages
    ```python
    import pygments
    import libstdcxx.v6.printers
    ```
1. get **gdbpanel.py**, **container_iter.py**, source them in *.gdbinit*
    ```gdb
    source /path/to/gdbpanel.py
    source /path/to/container_iter.py
    ```
2. execute following cmd inside gdb
    ```gdb
    python panel.start()
    ```

## Features
- **gdbpanel.py** offers functionality to divide terminal's display area, and record/restore informations
- **container_iter.py** offers python api to iterate STL containers from gdb
### Custom Layout
- with GdbPanel enabled, the display area of terminal will be divided into serveral rectangle *slots*
- a **layout** is a set of slots that can be tiled to cover the display area
- different **layout** can be reloaded during debugging
### Information Record/Restore
- when debugging with gdb in terminal, important information may be flushed out of screen by those redundant
- GdbPanel use serval **panes** to record information that with values, and resotre them in need
- a **pane** must be assigned to a **slot**, to show the information it captured
- mapping of **pane** and **slot** can be rebinded during debugging
- GdbPanel currently provides following **panes**:
    1. *Log*
        - records the log from inferior
        - can only be enabled when running an inferior process by gdb command `panel run` instead of `run` to the output, since there is no api from gdb to capture inferior logs
        - "\t" in the log will be replaced by 4 spaces
        - **NOTICE**: inferior's output is redirected to a fifo, therefore "\n" may not flush the output stream; in c/c++ if a `printf()` is not flushed in time, try `panel flush`
        - **NOTICE**: logging stops as inferior stops, the output of a function called from gdb should be obtained by command `panel flush`
        - **NOTICE**: an opened fifo under */tmp* will be leaked if gdb crashes
    2. *ValueHistory*
        - records the string pairs of expression & result of each manually `print` command
        - if a result of `print` has multiple lines, record at most 4 lines
    3. *Watch*
        - records the expressions added by command `panel watch`, maintain their latest value
        - value won't refresh if gdb.selected_frame() doesn't change
    4. *Source*
        - record the c/c++ source codes
        - show codes in locations of selected frame, or locations of breakpoints when they created
        - "\t" will be replaced by 4 spaces
    5. *Breakpoints*
        - shows breakpoints and watch points
        - shows their index, source file name, line number, function name, condition if exists and hit times
    6. *Stack*
        - shows index, source file name, line number and function name of stack frames
- to extend custom **pane**, just inherient `Panel.Pane` class and override `refresh_content()` method

### Iterate STL Containers
- **container_iter.py** provides method `list_iter`, `map_iter`, which accept 2 arguments:
    1. `expression: str`: a expression in gdb, whose type must be consistant with the called method (`std::list` or `std::map`)
    2. `callback: Callable[[gdb.Value], bool]`
- `list_iter` and `map_iter` will iterate the contianer object referenced by `expression`, and pass the elements to `callback`; if `callback` return `True`, stop iteration
    - element from `map_iter` is a `std::pair`, the key and value can be obtained by accessing the `first` and `second` member
- documentaion of `gdb.Value` refers to [manual](https://sourceware.org/gdb/onlinedocs/gdb/Values-From-Inferior.html#Values-From-Inferior)

## Documents
### Config
- GdbPanel's config is defined directly in python code, which can be customized by editing source code, or assigning new value to attribute `Panel.config` by other python statements
- **layout**
    - **slots** config
        - each slot is defined as a list of int `[id, width, height]`
            1. id can be any int but must unique, used to define mappings to **panes**
            2. width/height must in range [1, 10], the unit is 1/10 of display area's width/height
            3. **NOTICE**: in a legal config, the slots must form a 10x10 square
        - positions of the slots are represented by a binary tree, let *S* is a slot:
            1. each slot is a node, top-left slot is the root
            2. *S*.*left_child* is below *S*, and they are aligned along left edge
            3. *S*.*right_child* is on the right of *S*, and they are aligned along top edge
        - the binary tree is written as a list in config, with following rules:
            1. slots[0] is the root
            3. let *S* = slots[i], then slots[i+1] must be *S*.*right_child*, place `None` if *S* has no *right_child*
            4. after recursive definition of *S*.*right_child*, *S*.*left_child* must be defined next, place `None` if *S* has no *left_child*
    - **panes** config
        - a dict with k-v as "pane name: slot id"
        - panes not assigned here will be hidden
        - **NOTICE**: each slot must have a pane
    - example
        ```python
        '''
        the layout of following config (number is slots' id):
        -----------------
        | 0        |  1 |            0
        |          |    |           / \
        |          |    |    --->  3   1
        |          |----|             /
        |----------|  2 |            2
        | 3        |    |
        -----------------
        '''
        config = {
            'slots': [[0, 6, 8], [1, 4, 6], None, [2, 4, 4], None, None, [3, 6, 2], None, None],

            'panes': {'Source': 0, 'Watch': 1, 'Stack': 2, 'Breakpoints': 3}
        }
        ```
- **style**
    - apperence settings like color, refer to comments in source code
- **auto-render**
    - bool option, if set `True`, panel will show once gdb command finished, except `panel silent` or error occurs
### Commands
0. following are GDB commands
1. panel
    - show panel once
2. panel view *PANE* *SLOT*
    - *PANE*, str, name of a pane.
    - *SLOT*, int, index of a slot, defined in layout config
    - assign the *PANE* to *SLOT*
    - if *PANE* already been assigned to a slot, swap panes in the two slot.
    - if *PANE* is hidden, pane in *SLOT* turned into hidden
3. panel silent [*COMMAND*]
    - *COMMAND*, str, gdb command, optional
    - stop showing panel once, if *COMMAND* specified, call gdb to execute it (temprory set "discard-gdb-logs" to `False`)
4. panel layout *CONFIG_KEY*
    - *CONFIG_KEY*, type depends on actual setting
    - set `panel.layout_configs[CONFIG_KEY]` as current layout config
    - to enable layout config reloading, attribute **panel.layout_configs** must be defined, it should support subscription
5. panel run *ARGS*
    - launch an inferior process with *ARGS*, with GdbPanel's internal logger enabled
6. panel watch *EXPRESSION*
    - add *EXPRESSION* to panel's watch list
7. panel unwatch *EXPRESSION*
    - delete *EXPRESSION* from panel's watch list
8. panel flush
    - try flush inferior (c/c++) process's output stream buffer & read new logs
