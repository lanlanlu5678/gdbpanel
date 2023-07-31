from typing import Callable, Union, Any
import selectors
import threading
import pygments
import inspect
import math
import copy
import time
import sys
import gdb
import os
import re

# forward declaration for type hint
class Panel(gdb.Command):
    class Slot: pass
    class ANSIstr: pass
    class Pane: pass
class Console: pass


class Console:
    def start(self) -> None:
        self.reset_flags()
        self.connect_handlers()
        
    def end(self) -> None:
        self.reset_flags()
        self.disconnect_handlers()

    def reset_flags(self) -> None:
        self.bp_hit = False            # for source pane
        self.bp_create = False         # for source pane
        if gdb.objfiles():             # for source pane
            self.objfile_build_time = os.path.getmtime(gdb.objfiles()[0].filename)
        else:
            self.objfile_build_time = None
        self.sal_outdated = False      # for source + stack pane
        self.bps = []                  # for breakpoint pane
        self.bp_change = False         # for breakpoint pane
        self.history_count = 0         # for value history pane
        self.refresh_watch_val = False # for watch pane
        self.logging = False           # for background logger thread
        self.inferior_running = False  # for background logger thread

    def connect_handlers(self) -> None:
        gdb.events.stop.connect(self.stop_handler)
        gdb.events.cont.connect(self.running_handler)
        gdb.events.breakpoint_created.connect(self.breakpoint_created_handler)
        gdb.events.breakpoint_deleted.connect(self.breakpoint_deleted_handler)
    
    def disconnect_handlers(self) -> None:
        gdb.events.stop.disconnect(self.stop_handler)
        gdb.events.cont.disconnect(self.running_handler)
        gdb.events.breakpoint_created.disconnect(self.breakpoint_created_handler)
        gdb.events.breakpoint_deleted.disconnect(self.breakpoint_deleted_handler)

    def running_handler(self, event: gdb.ContinueEvent) -> None:
        if self.inferior_running:
            # detachment of any thread of inferior will trigger this event
            # keep redirect logger thread unique
            return
        if self.logging:
            self.inferior_running = True
            thread = threading.Thread(target=self.logger.redirect, args=[self])
            thread.start()

    def stop_handler(self, event: gdb.StopEvent) -> None:
        self.inferior_running = False
        self.sal_outdated = True
        self.refresh_watch_val = True
        if isinstance(event, gdb.BreakpointEvent):
            self.bp_hit = True

    def breakpoint_created_handler(self, bp: gdb.Breakpoint) -> None:
        if bp.location != None:
            # not watch point, source pane shows breakpoint's location
            self.bp_create = True
        self.bp_change = True
        self.bps.append(bp)

    def breakpoint_deleted_handler(self, bp: gdb.Breakpoint) -> None:
        self.bps.sort(key=lambda i: i == bp)
        self.bps.pop()
        self.bp_change = True



    ''' ------------------------------------------ api for panel --------------------------------------------- '''
    def refresh_stack(self) -> list:
        stack = []
        f = gdb.selected_frame()
        while f:
            if f.type() == gdb.DUMMY_FRAME:
                stack.append('<Gdb Function Call>')
            elif f.type() == gdb.SIGTRAMP_FRAME:
                stack.append('<OS Signal Handler>')
            else:
                sal = f.find_sal()
                if sal.symtab != None:
                    stack.append([f.level(), sal.symtab.filename, sal.line, f.name()])
                # else: seems from libc.so
            
            f = f.older()
        
        return stack

    @staticmethod
    def lookup_function_name(func: str, block: gdb.Block = None) -> str:
        if block:
            return gdb.lookup_symbol(func, block=block, domain=gdb.SYMBOL_VAR_DOMAIN).name
        else:
            return gdb.lookup_global_symbol(func, gdb.SYMBOL_VAR_DOMAIN).name

    def get_last_cmd_val(self) -> list:
        cmd = gdb.execute('show commands', False, True).split('\n')[-2]
        first = re.search(r'\s+\d+\s+', cmd).span()[1]
        first_char = cmd[first]
        if first_char == 'f' or first_char == 't':
            self.sal_outdated = True

        count = gdb.history_count()
        val = gdb.history(0) if count > self.history_count else None
        self.history_count = count

        return count, cmd[first:], val


    ''' ------------------------------------------ logger --------------------------------------------- '''
    class Logger:
        @staticmethod
        def fifo_opener(path: str, flags: int) -> int:
            return os.open(path, os.O_RDONLY | os.O_NONBLOCK)

        @staticmethod
        def create_fifo() -> str:
            path = ''
            while True:
                time_stamp = str(time.time()).split('.')[1]
                path = f'/tmp/gdb_{time_stamp}.log'
                if not os.path.exists(path):
                    os.mkfifo(path)
                    break
            return path

        def __init__(self):
            self.path = self.create_fifo()

            self.logs = ['~'] * 500
            self.cursor = 0

        def start(self):
            self.fifo = open(self.path, opener=self.fifo_opener)
            self.sel = selectors.DefaultSelector()
            self.sel.register(self.fifo, selectors.EVENT_READ)

        def end(self) -> None:
            self.sel.unregister(self.fifo)
            self.sel.close()
            self.fifo.close()

        def redirect(self, console: Console) -> None:
            print('\x1b[H\x1b[2J', file=sys.__stdout__)
            while True:
                if not console.inferior_running:
                    break

                # although open the pipe in non_blocking mode, once inferior keep writing (no EOF?), "select()" still hangs, must set timeout
                #   "Any stdio output stream is by default line buffered if output is going to a terminal and block buffered (typically 4KB blocks) otherwise. \
                #       See the manpages for stdio(3) and setbuf(3)."
                #       refer to https://www.linuxquestions.org/questions/programming-9/c-printf-hangs-linux-pipes-4175428396/
                for key, _ in self.sel.select(timeout=1):
                    log = key.fileobj.read()
                    print(log, file=sys.__stdout__)
                    for line in log.split('\n'):
                        self.logs[self.cursor] = line.replace('\t', '    ')
                        if self.cursor == 499:
                            self.cursor = 0
                        else:
                            self.cursor += 1

        def redirect_once(self) -> None:
            print('Trying to get inferior\'s log ...')
            read_success = False
            for key, _ in self.sel.select(timeout=1):
                read_success = True
                log = key.fileobj.read()
                print(log, file=sys.__stdout__)
                for line in log.split('\n'):
                    self.logs[self.cursor] = line
                    if self.cursor == 499:
                        self.cursor = 0
                    else:
                        self.cursor += 1
            if not read_success:
                print('Failed to get new log.')

    # redirect inferior logs, keep through current inferior process
    def start_logger(self) -> None:
        self.logging = True
        self.logger.start()

    # if logging not enable, prevent create fifo for this gdb session
    def init_logger(self):
        self.logger = Console.Logger()

        # stop logging when current process stops
        # to enable logging, start each time running a new process of inferior, by "panel run"
        def inf_exit_logger_handler(e: gdb.ExitedEvent):
            if self.logging:
                # wait for logger redirect thread exit before close fifo
                self.inferior_running = False
                time.sleep(0.1)

                self.logger.end()
                self.logging = False
        gdb.events.exited.connect(inf_exit_logger_handler)

        # remove fifo when current gdb session exit (may leak when gdb crash)
        gdb.events.gdb_exiting.connect(lambda e: os.unlink(self.logger.path))


console = Console()




class Panel(gdb.Command):
    ''' ------------------------------------------ config --------------------------------------------- '''
    config = {
        'layout': {
            #   each slot is defined by a list of int [id, width, height]
            #   [0, 6, 8] means the slot with 6/10 of terminal's width, 8/10 of terminal's height
            #   width/height of any slot cannot exceed 10
            #   id can be any int but must be unique
            #
            #   Let S is a slot
            #   positions of the slots are represented by a binary tree:
            #       1. root locates at top-left of the terminal
            #       2. S.left_child is below S, and they are aligned on left edge
            #       3. S.right_child is on the right of S, and they are aligned on top edge
            #
            #   the binary tree is written in the "slots" list with following rules:
            #       1. each slot must define both left/right children, use "None" represent the absent of child
            #       2. let S = slots[i], then slots[i+1] must be S.right_child (can be another slot or None)
            #       3. S.left_child must be defined, just next to the last element under S.right_child (i.e. the subtree)
            #
            #   the layout of following config (number is slots' id):
            #      -----------------
            #      | 0        |  1 |
            #      |          |    |
            #      |          |    |
            #      |          |----|
            #      |----------|  2 |
            #      | 3        |    |
            #      -----------------
            'slots': [[0, 6, 8], [1, 4, 6], None, [2, 4, 4], None, None, [3, 6, 2], None, None],

            #   assign panes to slots here, by writing "pane name: slot id"
            #   panes not assigned here will be hidden
            'panes': {'Source': 0, 'ValueHistory': 1, 'Stack': 2, 'Breakpoints': 3}
        },

        'style': {
            # color code can refers https://talyian.github.io/ansicolors/

            # character that split slots
            'delimiter-horizontal': '-',
            'delimiter-vertical': '|',
            'delimiter-color': 220,

            # breakpoint indicator
            'breakpoint-hit': '\u25b6', # ▶
            'breakpoint-idle': '\u25b7', # ▷
            'breakpoint-color': 196,

            'source-highlight-style': 'monokai',

            # decorating positions listed in "Breakpoints" & "Stack" panes
            'filename-color': 35,
            'function-color': 214,

            # currently are frames of "gdb call" & "os signal handler"
            'abnormal-frame-color': 20,

            # strike through line
            'disabled-breakpoint': 9
        },

        # in most case (except specify by cmd or error occur), print panel when a gdb command finished
        'auto-render': True,

        # if discard scrollback buffer, impossible to review previous logs
        # if keep scrollback buffer, panel content will quickly flush out the logs, also hardly to review
        # enable this option, redirect/save the logs from inferior in logger, can review by command "panel view Log"
        'redirect-inferior-logs': True,

        # wild mode: prevent showing gdb's raw output, keep only panel content refreshing in terminal
        # for focusing infomations, capture them in panes
        # for special case (long message pane cannot contain), use command "panel print $gdb_command"
        'discard-gdb-logs': True,

        # crazy mode: keep terminal fix on panel (cannot scroll back for previous content which are usually redundant)
        'discard-scrollback-buffer': False
    }

    @staticmethod
    def check_layout_config(config: dict) -> None:
        slots = config['slots']
        panes = config['panes']
        mapping = {}

        if type(slots) != list:
            raise Panel.PanelConfigError('Layout', 'slots config must be a list')
        for slot in slots:
            if slot == None:
                continue
            mapping[slot[0]] = None
            for i in slot[1:]:
                if type(i) != int or i <= 0 or i > 10:
                    raise Panel.PanelConfigError('Layout', f'Invalid slot config {slot}, width/height must in range (0, 10]')

        if type(panes) != dict:
            raise Panel.PanelConfigError('Layout', 'panes config must be a dict')
        for pane, slot_id in panes.items():
            if pane not in Panel.__dict__:
                raise Panel.PanelConfigError('Layout', f'pane {pane} not defined.')
            if slot_id not in mapping:
                raise Panel.PanelConfigError('Layout', f'pane {pane} with invalid slot index {slot_id}.')
            if mapping[slot_id] != None:
                raise Panel.PanelConfigError('Layout', f'pane {pane} and {mapping[slot_id]} with conflict slot index {slot_id}.')
            mapping[slot_id] = pane

        for idx, pane in mapping.items():
            if pane == None:
                raise Panel.PanelConfigError('Layout', f'slot {idx} has no pane assigned.')

    style = None
    class Style:
        def __init__(self, conf: dict):
            # pane border lines
            self.deli_h = self.borderline_repeater(conf['delimiter-color'], conf['delimiter-horizontal'])
            self.deli_v = self.ansi_color_wrapper(conf['delimiter-color'], conf['delimiter-vertical']) + ' ' # space on right side

            # breakpoint
            self.bp_hit = self.ansi_color_wrapper(conf['breakpoint-color'], conf['breakpoint-hit'])
            self.bp_idle = self.ansi_color_wrapper(conf['breakpoint-color'], conf['breakpoint-idle'])
            self.bp_disabled_wrapper = self.ansi_wrapper(conf['disabled-breakpoint'], '{}')

            # for breakpoint & stack, source locations
            self.filename_wrapper = self.ansi_color_wrapper(conf['filename-color'], '{}')
            self.function_wrapper = self.ansi_color_wrapper(conf['function-color'], '{}')

            # for stack
            self.abnormal_frame_wrapper = self.ansi_color_wrapper(conf['abnormal-frame-color'], '{}')

        @staticmethod
        def ansi_wrapper(code: int, content: str) -> str:
            return f'\x1b[{code}m{content}\x1b[m'

        @staticmethod
        def ansi_color_wrapper(code: int, content: str) -> str:
            return f'\x1b[38;5;{code}m{content}\x1b[m'

        @staticmethod
        def borderline_repeater(code: int, content: str) -> Callable[[int], str]:
            color_str = f'\x1b[38;5;{code}m'
            def repeater(times: int) -> str:
                return color_str + (content * times) + '\x1b[m'
            return repeater

        @staticmethod
        def strip_filename(filename: str) -> str:
            return '/'.join(filename.split('/')[-2:])


    def load_config(self) -> None:
        # behavior flag
        self.auto_render = Panel.config['auto-render']
        self.clear = Panel.config['discard-scrollback-buffer']
        self.discard_gdb = Panel.config['discard-gdb-logs']

        Panel.style = Panel.Style(Panel.config['style'])

        self.refresh_layout(Panel.config['layout'])




    ''' ------------------------------------------ initialization --------------------------------------------- '''
    def __init__(self):
        gdb.Command.__init__(self, 'panel', gdb.COMMAND_USER, gdb.COMPLETE_NONE, True)

        # state flags not from config
        self.enabled = False
        self.err = False
        self.render_once = False
        self.skip_render_once = False
        self.layout_valid = False

    def start(self) -> None:
        self.enabled = True
        sys.excepthook = self.excepthook
        self.panes = {}
        for name, obj in Panel.__dict__.items():
            try:
                if name != 'Pane' and issubclass(obj, Panel.Pane):
                    self.panes[name] = obj()
            except TypeError:
                pass
        self.load_config()
        gdb.execute('set logging file /dev/null')
        gdb.execute('set logging redirect on')
        Panel.set_discard_gdb(self.discard_gdb)
        gdb.events.before_prompt.connect(self.render_handler)
        global console
        console.start()
        if Panel.config['redirect-inferior-logs']:
            console.init_logger()

    def end(self) -> None:
        self.enabled = False
        sys.excepthook = sys.__excepthook__
        Panel.set_discard_gdb(False)
        gdb.events.before_prompt.disconnect(self.render_handler)
        global console
        console.end()


    ''' ------------------------------------------ layout --------------------------------------------- '''
    class Slot:
        def __init__(self):
            self.xl = 0
            self.width = 0
            self.yt = 0
            self.height = 0
            self.pane = None
            self.padding = True


        def render(self) -> list:
            if not self.pane:
                raise Panel.PanelError('slot without pane.')
            
            content = self.pane.render(self.width, self.height, self.padding)

            top_right = False   # indicate whether lines in below_content should concate with extra lines from right_content
            if self.right != None:
                right_content = self.right.render()
                top_right = len(right_content) > len(content)
                for i in range(len(content)):
                    right_content[i] = content[i] + Panel.style.deli_v + right_content[i]
                content = right_content

            if self.below != None:
                below_content = self.below.render()
                deli_h = Panel.style.deli_h(self.width)
                if not top_right:
                    content.append(deli_h)
                    content += below_content    # content already aligned
                else:
                    end = (self.below.height * -1) - 1
                    content[end] = Panel.style.deli_h(self.below.width) + Panel.style.deli_v + content[end]
                    for i in range(-1, end, -1):
                        content[i] = below_content[i] + Panel.style.deli_v + content[i]
            
            return content

    class LayoutChecker:
        def __init__(self):
            self.ranges = [[0, 10, 0]]   # range is right open : [)
            self.min_ = 0
            self.max_ = 10

        def add(self, left: int, right: int, val: int) -> None:
            new_ranges = []
            for R in self.ranges:
                if R[0] >= right or R[1] <= left:
                    new_ranges.append(R)
                    continue

                l, r, v = R

                # R is wider on left side, remain origin value
                if l < left:
                    new_ranges.append([l, left, v])

                # intersect part of newly added range and current R
                # keep newly added range never has left < R.l
                new_ranges.append([left, min(r, right), v + val])

                # R is wider on right side, remain origin value
                if r > right:
                    new_ranges.append([right, r, v])

                # newly added range has un-merge value
                if r < right:
                    left = r
            self.ranges = new_ranges


    class Layout:
        def __init__(self, config: list, width: int, height: int):
            self.slots = {}
            slot_bounds = {}
            def build_tree(xl: int, yt: int) -> Panel.Slot:
                try:
                    slot_config = config.pop(0)
                except IndexError:
                    raise Panel.PanelConfigError('Layout', 'Invalid "slots", missing element (probably None)')
                if slot_config == None:
                    return None
                else:
                    i, w, h = slot_config
                    xr = xl + w
                    yb = yt + h
                    slot_bounds[i] = [xl, xr, yt, yb]

                    slot = Panel.Slot()
                    slot.padding = xr < 10
                    slot.right = build_tree(xr, yt)     # right child's top  edge must align to current slot
                    slot.below = build_tree(xl, yb)     # below child's left edge must align to current slot
                    self.slots[i] = slot
                    return slot
            self.root = build_tree(0, 0)

            self.sanity_check(slot_bounds)

            real_xs, real_ys = self.get_real_coords(slot_bounds, width / 10, height / 10)
            real_xs[0] = -1                             # slots on left don't count extra space
            real_xs[10] = width + 1                     # slots on right don't count border line
            real_ys[10] = height + 1                    # slots on bottom don't count border line
            for i, slot in self.slots.items():
                xl, xr, yt, yb = slot_bounds[i]
                slot.xl = real_xs[xl]
                slot.width = real_xs[xr] - slot.xl - 2  # -2 for left extra space & right border line
                slot.xl = max(0, slot.xl)               # correct the hack for left slots' xl
                slot.yt = real_ys[yt]
                slot.height = real_ys[yb] - slot.yt - 1 # -1 for bottom boader line

        @staticmethod
        def sanity_check(bounds: dict[int, list[int]]) -> None:
            w_checker = Panel.LayoutChecker()
            h_checker = Panel.LayoutChecker()
            for i, bound in bounds.items():
                xl, xr, yt, yb = bound
                w_checker.add(yt, yb, xr - xl)
                h_checker.add(xl, xr, yb - yt)
            for l, r, v in w_checker.ranges:
                if v != 10:
                    raise Panel.PanelConfigError('Layout', f'height range (starts from terminal top) [{l}, {r}] with width {v}')
            for l, r, v in h_checker.ranges:
                if v != 10:
                    raise Panel.PanelConfigError('Layout', f'width range (starts from terminal left) [{l}, {r}] with height {v}')

        # ensure slots still align after ceil
        @staticmethod
        def get_real_coords(bounds: dict[int, list[int]], unit_w: float, unit_h: float) -> list[dict[int]]:
            coords = [bound for i, bound in bounds.items()]
            values = [i for i in zip(*coords)]
            xs = set(values[0] + values[1])
            ys = set(values[2] + values[3])

            def get_reals(coords: set, unit: float) -> dict[int, int]:
                coords = sorted(list(coords))
                reals = {0: 0}
                for i in range(1, len(coords) - 1):
                    ofsa = coords[i - 1]
                    ofsb = coords[i]
                    reals[ofsb] = reals[ofsa] + math.ceil((ofsb - ofsa) * unit)
                return reals
            
            return get_reals(xs, unit_w), get_reals(ys, unit_h)


    def refresh_layout(self, new_config: dict = None) -> None:
        termw, termh = os.get_terminal_size()
        if new_config == None and termw == self.width and termh == self.height + 2:
            return
        self.width = termw
        self.height = termh - 2 # for bottom border line + prompt line

        self.layout_valid = False
        if new_config != None:
            self.check_layout_config(new_config)
            self.current_layout_config = new_config

        slots_config = self.current_layout_config['slots']
        self.layout = Panel.Layout(copy.deepcopy(slots_config), self.width, self.height)
        self.layout_valid = True

        panes_config = self.current_layout_config['panes']
        for name, pane in self.panes.items():
            if name in panes_config:
                slot = self.layout.slots[panes_config[name]]
                pane.slot = slot
                slot.pane = pane
            else:
                pane.slot = None

    def render(self) -> None:
        self.refresh_layout()

        content = self.layout.root.render()
        content.append(self.style.deli_h(self.width))

        Panel.clear(self.clear)

        print('\n'.join(content), file=sys.__stdout__)



    ''' ------------------------------------------ status/behaviour --------------------------------------------- '''
    def invoke(self, arg: str, from_tty: bool) -> None:
        if not self.enabled:
            return

        argv = gdb.string_to_argv(arg)
        if len(argv) == 0:
            self.render_once = True
            return

        self.dont_repeat()

        global console

        # launch an inferior process while redirect its output to logger fifo
        if argv[0] == 'run':
            if len(argv) > 1:
                ori_argv = ' '.join(argv[1:])
            else:
                ori_argv = ''
            console.start_logger()
            gdb.execute(f'run {ori_argv} > {console.logger.path}')

        # panel view PANE SLOT
        # if PANE already been assigned to a slot, swap slots with pane in SLOT
        # if PANE is hidden, pane in SLOT turned to hidden
        elif argv[0] == 'view':
            try:
                pane_name, idx = self.format_args([str, int], argv)
            except TypeError:
                raise Panel.PanelSyntaxError(arg, argv[0])

            if pane_name not in self.panes:
                raise Panel.PanelError(f'Invalid pane name {pane_name}')
            else:
                pane_a = self.panes[pane_name]
            if idx not in self.layout.slots:
                raise Panel.PanelError(f'Invalid slot index {idx}')
            else:
                slot_b = self.layout.slots[idx]

            slot_a = pane_a.slot
            pane_b = slot_b.pane
            pane_a.slot, pane_b.slot = pane_b.slot, pane_a.slot
            slot_b.pane = pane_a
            if slot_a:
                slot_a.pane = pane_b
            
        # call gdb command "print EXPRESSION", panel won't show after this print to avoid flushing the result away
        # temporary override the config "discard-gdb-logs" to false
        elif argv[0] == 'print':
            if len(argv) == 1:
                raise Panel.PanelSyntaxError(arg, argv[0])
            self.skip_render_once = True
            self.set_discard_gdb(False)
            gdb.execute(' '.join(argv))
            self.set_discard_gdb(self.discard_gdb)

        # call gdb to execute COMMAND, panel won't show after this command to avoid flushing the result away
        # temporary override the config "discard-gdb-logs" to false
        elif argv[0] == 'silent':
            if len(argv) == 1:
                raise Panel.PanelSyntaxError(arg, argv[0])
            self.skip_render_once = True
            self.set_discard_gdb(False)
            gdb.execute(' '.join(argv[1:]))
            self.set_discard_gdb(self.discard_gdb)

        # change panel's layout
        # layout configs for quick changing must be stored as a list in attribute "panel.layout_configs"
        elif argv[0] == 'layout':
            try:
                idx = self.format_args([int], argv)[0]
            except TypeError:
                raise Panel.PanelSyntaxError(arg, argv[0])
            if not hasattr(self, 'layout_configs') or idx >= len(self.layout_configs):
                raise Panel.PanelError(f'No panel.layout_configs defined or {idx} out of range.')
            self.refresh_layout(self.layout_configs[idx])

        # add EXPRESSION to panel's watch list
        # latest value of each EXPRESSION in watch list will be shown
        elif argv[0] == 'watch':
            try:
                expression = self.format_args([str], argv)[0]
            except TypeError:
                raise Panel.PanelSyntaxError(arg, argv[0])
            self.panes['Watch'].expressions.append(expression)
            console.refresh_watch_val = True

        # delete EXPRESSION from panel's watch list
        elif argv[0] == 'unwatch':
            try:
                idx = self.format_args([int], argv)[0]
            except TypeError:
                raise Panel.PanelSyntaxError(arg, argv[0])
            if idx >= len(self.panes['Watch']):
                raise Panel.PanelError(f'{idx} out of watch list range.')
            self.panes['Watch'].expressions.pop(idx)

        # try flush inferior process's output stream buffer & read new logs
        elif argv[0] == 'flush':
            self.skip_render_once = True
            self.set_discard_gdb(False)
            gdb.execute('call (int) fflush(stdout)')
            console.logger.redirect_once()
            self.set_discard_gdb(self.discard_gdb)

        elif argv[0] == 'scroll':
            pass

        else:
            self.syntax_err(0, 'panel')

    @staticmethod
    def format_args(types: list[type], args: list[str]) -> list:
        try:
            legal = []
            for i in range(len(types)):
                legal.append(types[i](args[i + 1]))  # +1 for avoid exception raise in caller, pass whole argv
            return legal
        except (IndexError, ValueError):
            return None

    @staticmethod
    def clear(dsc: bool = False):
        # ANSI: move the cursor to top-left corner + clear the screen
        print('\x1b[H\x1b[2J', file=sys.__stdout__)
        # erase content in scrollback buffer
        if dsc:
            print('\x1b[3J', file=sys.__stdout__)

    @staticmethod
    def set_discard_gdb(dsc: bool = False):
        gdb.execute('set logging enabled {}'.format('on' if dsc else 'off'), False, True)

    def render_handler(self) -> None:
        global console
        # if prev cmd is "f"/"t", console.sal_outdated shound be refreshed before Source & Stack render
        idx, cmd, val = console.get_last_cmd_val()
        if self.panes['ValueHistory'] != None:
            # "ValueHistory" pane should update content even hidden
            self.panes['ValueHistory'].record_cmd_value(idx, cmd, val)
            

        def skip_render() -> bool:
            if not self.layout_valid:
                return True

            # flag set by command "panel"
            if self.render_once:
                self.render_once = False
                return False

            if not self.auto_render:
                return True

            # flag set by "panel" cmd syntax error or panel class internal class
            if self.err:
                self.err = False
                return True

            # flag set by command "panel"
            #   1. "panel print", show content that panes cannot contian
            #   2. "panel silent"
            #   3. "panel flush", try flush inferior's output stream buffer and print new log
            if self.skip_render_once:
                self.skip_render_once = False
                return True

        if not skip_render():
            self.render()

        console.sal_outdated = False
        console.refresh_watch_val = False



    ''' ------------------------------------------ err handling --------------------------------------------- '''
    syntax_doc = {
        'view': [
            'panel view PANE SLOT',
            'PANE, str, name of a pane.',
            'SLOT, int, index of a slot, defined in layout config\n',
            'Assign the PANE to SLOT.',
            'if PANE already been assigned to a slot, swap panes in the two slot.',
            'if PANE is hidden, pane in SLOT turned to hidden.'
        ],
        'print': [
            'panel print EXPRESSION',
            'EXPRESSION, str\n',
            'Call gdb command "print EXPRESSION".',
            'Panel won\'t show after this print to avoid flushing the result away.',
            'Temporary override the config "discard-gdb-logs" to false.'
        ],
        'silent': [
            'panel silent COMMAND',
            'COMMAND, str\n',
            'Call gdb to execute COMMAND.',
            'Panel won\'t show after this command to avoid flushing the result away.',
            'Temporary override the config "discard-gdb-logs" to false.'
        ],
        'layout': [
            'panel layout CONFIG_INDEX',
            'CONFIG_INDEX, int, starts from 0, the index of the selected config in "panel.layout_configs"\n',
            'Change panel\'s layout with the selected config.',
            'Layout configs for quick changing must be defined as a list in attribute "panel.layout_configs".'
        ]
    }
    class PanelError(Exception):
        def __init__(self, msg: str):
            self.msg = msg
    class PanelConfigError(PanelError):
        def __init__(self, domain: str, cause: str):
            self.msg = f'Invalid {domain} config: {cause}'
    class PanelSyntaxError(PanelError):
        def __init__(self, cmd: str, doc_code: str):
            self.msg = 'Invalid syntax of "{}"\n\n{}\n'.format(cmd, '\n\t'.join(Panel.syntax_doc[doc_code]))

    def excepthook(self, except_type, value, traceback) -> None:
        if not issubclass(except_type, KeyboardInterrupt):
            self.err = True
        if issubclass(except_type, Panel.PanelError):
            print(f'  Panel Error: {value.msg}\n', file=sys.__stdout__)
        else:
            sys.__excepthook__(except_type, value, traceback)





    ''' ------------------------------------------ panes --------------------------------------------- '''
    class ANSIstr:
        def __init__(self, encoded: str = None):
            self.seq = [] # elem: [len_of_raw_substr, raw_str, wrapping_str]
            self.fix = [] # elem: [prefix, suffix]
            if encoded != None:
                self.decode(encoded)

        def style_underline(self) -> None:
            self.fix.append(['\x1b[4m', '\x1b[m'])

        def decode(self, encoded: str) -> None:
            raw_len = 0
            prev_end = 0 # idx next to previous match substr
            for m in re.finditer(r'\x1b\[38.+?m(.+?)\x1b\[39.*?m', encoded):
                first, end = m.span()
                if first != prev_end:
                    # has pure str
                    raw_len += (first - prev_end)
                    self.seq.append([raw_len, encoded[prev_end:first], None])

                wrapping_pre = encoded[first:m.start(1)]
                raw_str = encoded[m.start(1):m.end(1)]
                wrapping_suf = encoded[m.end(1):end]
                raw_len += len(raw_str)
                self.seq.append([raw_len, raw_str, f'{wrapping_pre}{{}}{wrapping_suf}'])

                prev_end = end

            if prev_end != len(encoded):
                raw_str = encoded[prev_end:]
                raw_len += len(raw_str)
                self.seq.append([raw_len, raw_str, None])

        def match(self, width: int, padding: bool) -> str:
            diff = width - self.seq[-1][0]
            if diff < 0:
                line = self.truncate(width)
            elif diff > 0 and padding:
                line = self.printf() + ' ' * diff
            else:
                line = self.printf()
            
            # one time decoration (Source)
            if self.fix:
                for i in self.fix:
                    line = i[0] + line + i[1]
            self.fix = []

            return line
        
        def truncate(self, length: int) -> str:
            for i in range(len(self.seq)):
                if self.seq[i][0] >= length:
                    break
            last = self.seq[i]
            offset = length - (last[0] - len(last[1]))
            new_raw = last[1][:offset]
            seq = self.seq[:i]
            seq.append([length, new_raw, last[2]])

            return self.printf(seq)
        
        def printf(self, seq: list = None) -> str:
            if not seq:
                seq = self.seq
            
            line = ''
            for i in seq:
                if not i[2]:
                    line += i[1]
                else:
                    line += i[2].format(i[1])
            
            return line


    class Pane:
        @staticmethod
        def match_pure_str(line: str, width: int, padding: bool) -> str:
            diff = width - len(line)
            if diff < 0:
                return line[:width]
            elif diff > 0 and padding:
                return line + ' ' * diff
            else:
                return line

        @staticmethod
        def ListSizeWorker_call_(list_obj: gdb.Value) -> int:
            begin_node = list_obj['_M_impl']['_M_node']['_M_next']
            end_node = list_obj['_M_impl']['_M_node'].address
            size = 0
            while begin_node != end_node:
                begin_node = begin_node['_M_next']
                size += 1
            return size

        @staticmethod
        def shrink_value_string(v: gdb.Value) -> list[str]:
            head = '    '
            fstr = v.format_string()
            if '\n' not in fstr:
                return [head + fstr]
            else:
                lines = fstr.strip().split('\n')
                try:
                    typetag = v.type.target().tag if not v.type.tag else v.type.tag
                except gdb.error:
                    typetag = ''
                if re.match('^std::(__\d+::)?(__cxx11::)?list<.*>$', typetag) != None:
                    size = Panel.Pane.ListSizeWorker_call_(v)
                    rep = ' with {} element{} :'.format(size, 's' if size > 1 else '')
                else:
                    rep = ' :'
                sstr = [head + lines[0].replace(' = {', rep)]

                for line in lines[1:4]:
                    if line != '}':
                        sstr.append(head + line)
                if sstr[-1][-1] == ',':
                    sstr[-1] = sstr[-1][:-1] + ' ...'

                return sstr


        def render(self, width: int, height: int, padding: bool) -> list[str]:
            raw = self.refresh_content(height)

            content = []
            for line in raw:
                if isinstance(line, Panel.ANSIstr):
                    content.append(line.match(width, padding))
                else:
                    content.append(Panel.Pane.match_pure_str(line, width, padding))

            diff = height - len(content)
            if diff > 0:
                empty_line = (' ' * width) if padding else ''
                content += [empty_line for _ in range(diff)]

            return content


    class Source(Pane):
        def __init__(self):
            self.curr_line_num = 0
            self.frame_line_num = 0
            self.file = None
            self.cache = {}
            self.highlighted = {}

            from pygments.formatters import Terminal256Formatter
            self.fmter = Terminal256Formatter(style=Panel.config['style']['source-highlight-style'])

            from pygments.lexers import get_lexer_for_filename
            self.lexer = get_lexer_for_filename('foo.cpp')

            self.low_performance = 'low-performance' in Panel.config

            self.warning = None


        def get_file_line(self) -> list:
            filename = center_idx = None
            global console
            if console.bp_create:
                console.bp_create = False
                loc = console.bps[-1].locations[0]
                filename = loc.fullname
                center_idx = loc.source[1] - 1
            else:
                if console.sal_outdated:
                    try:
                        frame = gdb.selected_frame()
                        sal = frame.find_sal()
                        self.curr_line_num = self.frame_line_num = sal.line
                        self.file = sal.symtab.fullname()
                    except:
                        self.file = None
                filename = self.file
                center_idx = self.curr_line_num - 1
            return filename, center_idx


        def refresh_content(self, height: int) -> list[str]:
            file, center = self.get_file_line()

            if file == None:
                return ['No source file/line found in current frame.']

            if (file not in self.cache) and (not self.cache_file(file)):
                return [f'Cannot open file: {file}.']

            if self.warning:
                height -= 1

            half = (height - 1) // 2
            first = max(0, center - half)
            last = center + half + (2 - height % 2) # beyond the actual last line
            if self.low_performance:
                self.highlight_segments(file, first, last)
            self.cache[file][center].style_underline()
            content = self.cache[file][first:last]

            if self.warning:
                content = [self.warning] + content
                self.warning = None

            return content


        def cache_file(self, filename: str) -> bool:
            try:
                if console.objfile_build_time:
                    edit_time = os.path.getmtime(filename)
                    if edit_time > console.objfile_build_time:
                        self.warning = f'Warning: source file {filename} edited after build.\n'

                with open(filename, 'r') as f:
                    source = f.read()
            except IOError:
                return False

            if self.low_performance:
                self.cache[filename] = source.strip().split('\n')
                self.highlighted[filename] = set()
                return True

            source = pygments.highlight(source, self.lexer, self.fmter).strip().split('\n')
            lines = []
            for idx in range(len(source)):
                source_line = source[idx].replace('\t', '    ')
                lines.append(Panel.ANSIstr(encoded=f'{idx + 1:>5} {source_line}'))
            self.cache[filename] = lines

            return True


        def highlight_segments(self, filename: str, first: int, last: int) -> None:
            first = first // 100
            last = (last - 1) // 100
            segment = []
            for i in range(first, last + 1):
                if i in self.highlighted[filename]:
                    continue
                self.highlighted[filename].add(i)
                segment.append(i)
            if len(segment) == 0:
                return

            cache = self.cache[filename]
            first = segment[0] * 100
            last = min(len(cache), segment[-1] * 100 + 100)
            source = pygments.highlight('\n'.join(cache[first:last]), self.lexer, self.fmter).split('\n')
            for idx in range(first, last):
                source_line = source[idx - first].replace('\t', '    ')
                cache[idx] = Panel.ANSIstr(encoded=f'{idx + 1:>5} {source_line}')


    class Breakpoints(Pane):
        def __init__(self):
            self.bp_lines = {} # ANSIstr or str

        def update_bps(self) -> None:
            global console
            if not console.bp_change:
                return
            console.bp_change = False

            for bp in list(self.bp_lines.keys()):
                if bp not in console.bps:
                    del self.bp_lines[bp]

            for bp in console.bps:
                if bp not in self.bp_lines:
                    self.init_bp_line(bp)

        def init_bp_line(self, bp: gdb.Breakpoint) -> None:
            if not bp.location:
                # watch point
                line = f'{bp.number:>3} watch "{bp.expression}" hit {{}} times'
            else:
                # break point
                line = self.init_break_line(bp)
            self.bp_lines[bp] = line

        @staticmethod
        def update_bp_line(bp: gdb.Breakpoint, line: Union[Panel.ANSIstr, str]) -> Union[Panel.ANSIstr, str]:
            if isinstance(line, str):
                # python string
                if not bp.enabled:
                    line = Panel.style.bp_disabled_wrapper.format(line)
                # update hit count
                return line.format(bp.hit_count)
            else:
                # ANSIstr
                if not bp.enabled:
                    line.fix.append(Panel.style.bp_disabled_wrapper.split('{}'))
                line.seq[-1][1] = f'hit {bp.hit_count:>2} times'
                return line
                

        def refresh_content(self, height: int) -> list:
            self.update_bps()

            content = []
            for bp, line in self.bp_lines.items():
                # for existing bp, update hit counts
                content.append(self.update_bp_line(bp, line))

            return content
            
        def init_break_line(self, bp: gdb.Breakpoint) -> Panel.ANSIstr:
            line = Panel.ANSIstr()

            raw_len = 10
            line.seq.append([raw_len, f'{bp.number:>3} break ', None])

            loc = bp.locations[0]
            filename, line_num = loc.source
            # source
            filename = Panel.Style.strip_filename(filename)
            raw_len += len(filename)
            line.seq.append([raw_len, filename, Panel.style.filename_wrapper])
            # line number
            line_num = f':{line_num} '
            raw_len += len(line_num)
            line.seq.append([raw_len, line_num, None])

            # function
            raw_len += 3
            line.seq.append([raw_len, 'in ', None])
            function = Console.lookup_function_name(loc.function).split('(')[0]
            raw_len += len(function)
            line.seq.append([raw_len, function, Panel.style.function_wrapper])
            raw_len += 3
            line.seq.append([raw_len, '() ', None])

            # condition
            if bp.condition:
                raw_len += (len(bp.condition) + 6)
                line.seq.append([raw_len, f'[if {bp.condition}] ', None])
            
            # preserve 12 width for hit count
            line.seq.append([raw_len + 12, None, None])

            return line


    class Watch(Pane):
        def __init__(self):
            self.expressions = []
            self.content = []

        def refresh_content(self, height: int) -> list[str]:
            global console
            if console.refresh_watch_val:
                self.content = []
                for i in range(len(self.expressions)):
                    e = self.expressions[i]
                    self.content.append(f'{i:<3} {e} :')
                    try:
                        v = gdb.parse_and_eval(e)
                        self.content += self.shrink_value_string(v)
                    except gdb.error:
                        self.content.append(f'    No symbol "{e}" in current context.')

            return self.content[:height]


    
    class ValueHistory(Pane):
        def __init__(self):
            # mix commands & values, since value may have multiple lines
            #   value stores formatted string, since temp value return from function (e.g. std::string) will corrupt after a few commands
            #   encounter this problem with gdb13.1 debugging txx on Axxic's server, therefore access in time
            self.cnv = []

        @staticmethod
        def is_print_cmd(cmd: str) -> bool:
            cmd_filter = [
                # length, pattern
                [2, 'p '],
                [3, 'pp '],
                [6, 'print '],
                [12, 'panel print ']
            ]
            for length, pattern in cmd_filter:
                if cmd[:length] == pattern:
                    return True
            return False

        def record_cmd_value(self, idx: int, cmd: str, value: gdb.Value) -> None:
            if not self.is_print_cmd(cmd) or value == None:
                return

            self.cnv.append(f'{idx:<3} {cmd}')
            self.cnv += self.shrink_value_string(value)

        def refresh_content(self, height: int) -> list[str]:
            first = len(self.cnv) - height
            if first > 0:
                return self.cnv[first:]
            else:
                return self.cnv



    class Stack(Pane):
        def __init__(self):
            self.content = []

        def refresh_content(self, height: int) -> list[Panel.ANSIstr]:
            global console
            if console.sal_outdated:
                self.content = []
                stack = console.refresh_stack()
                for f in stack:
                    line = Panel.ANSIstr()

                    if isinstance(f, str):
                        line.seq.append([len(f), f, Panel.style.abnormal_frame_wrapper])
                    else:
                        level, filename, line_num, function = f
                        raw_len = 3
                        line.seq.append([raw_len, f'{level:>2} ', None])
                        filename = Panel.Style.strip_filename(filename)
                        raw_len += len(filename)
                        line.seq.append([raw_len, filename, Panel.style.filename_wrapper])
                        line_part = f':{line_num} in '
                        raw_len += len(line_part)
                        line.seq.append([raw_len, line_part, None])
                        raw_len += len(function)
                        line.seq.append([raw_len, function, Panel.style.function_wrapper])

                    self.content.append(line)
            
            return self.content


    class Log(Pane):
        def refresh_content(self, height: int) -> list[str]:
            global console
            if not console.logging:
                return ['Panel logger is not enabled.']
            cursor = console.logger.cursor
            logs = console.logger.logs

            first = cursor - height
            if logs[first] == '~':
                first = 0
            if first == cursor:
                content = []
            elif first < 0:
                content = logs[first:] + logs[:cursor]
            else:
                content = logs[first:cursor]
            
            return content


    class Threads(Pane):
        def refresh_content(self, height: int) -> list[str]:
            return ['to be implemented.']

    class Locals(Pane):
        def refresh_content(self, height: int) -> list[str]:
            return ['to be implemented.']





panel = Panel()
# panel.start()
