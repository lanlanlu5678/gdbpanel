## Motivation
1. 充分利用 terminal 显示区域
2. 记录 debug 过程中的重要信息，在需要的时候再现
3. 在 debug 过程中遍历 stl 容器

## Prerequistie
1. gdb with python >= 3.10
2. python package [pygments](https://pygments.org/) for code highlighting
3. have `libstdcxx` module in gdb's python search path
    - `libstdcxx` can be obtained from [this site](https://github.com/gcc-mirror/gcc/tree/master/libstdc%2B%2B-v3/python/libstdcxx) or the gcc directory

## Usage
0. 确认所使用的 gdb 链接了一个版本 3.10 及以上的 python 解释器，且该解释器可以正确执行以下命令
    ```python
    import pygments
    import libstdcxx.v6.printers
    ```
1. 下载/复制 **gdbpanel.py**, **container_iter.py** 两个文件，并在 .gdbinit 中读入
    ```gdb
    source /path/to/gdbpanel.py
    source /path/to/container_iter.py
    ```
2. 在 gdb 中执行命令
    ```gdb
    python panel.start()
    ```

## Features
- **gdbpanel.py** 提供划分显示区域、信息记录/再现功能：
- **container_iter.py** 提供遍历 stl 容器元素的 python api
### 自定义布局
- 在 GdbPanel 中，terminal 显示区域将被划分为若干个矩形，每个矩形记为一个 **slot**
- terminal 会有多种不同的矩形划分方式，记为不同的 **layout**
- GdbPanel 允许自定义 **layout**，并可以在运行时切换不同的 **layout**
### 信息记录/再现
- 在原始的 terminal gdb debug 过程中，随着新的信息的不断打印，想要回顾之前的信息只能通过往回翻页（可能已彻底丢失）或者查询额外 log 文件
- 使用 GdbPanel 插件后，debug 过程中产生/需要的信息将被不同的 **pane** 记录，并可以在任意时候重新打印
- **pane** 只负责信息的记录，如果需要显示其内容，需要将 **pane** 分配到一个 **slot** 中
- **pane** 与 **slot** 的映射关系可以在运行时动态绑定
- GdbPanel 提供以下 **panes**
    1. *Log*
        - 记录着来自 inferior 的最近 500 行 log 内容
        - 必须使用命令 `panel run` 代替 `run` 来运行 inferior 才能正确记录；因为 gdb 并不截取 inferior 的输出，需要将其重定向
        - log 中的制表符 "\t" 将被替换为 4 个空格
        - **注意**：由于输出被重定向到 fifo 中，换行符 "\n" 可能不会清空 output stream buffer，确认所需要的 inferior log 都及时地被写出；如果在 C/C++ 程序中使用 `printf()` 而没有添加 `fflush(stdout)` 操作，可以尝试命令 `panel flush`
        - **注意**：inferior 暂停时会自动停止记录，如果 inferior 是 C/C++ 程序且在 gdb 中调用了有打印输出的函数，需要执行命令 `panel flush` 手动获取
        - **注意**：如果 gdb crash，将泄漏一个未关闭的 fifo 文件在 */tmp* 目录下
    2. *ValueHistory*
        - 记录着通过终端中手动执行的 `print` 命令打印的信息
        - 每手动执行一次 `print $expression`，记录 **$expression** 和 gdb 打印的结果字符串
        - 如果某个 `print` 结果有多行，最多保留前 4 行
    3. *Watch*
        - 记录着通过命令 `panel watch` 添加的表达式，并在每次需要显示时更新它们的当前值，并打印
        - 如果在上次更新-打印之后，gdb 当前选中的栈帧没有改变（inferior 运行或者通过 `frame`/`thread` 命令），那么不更新表达式值
    4. *Source*
        - 记录着源文件片段（默认只支持 C/C++）
        - 显示当前栈帧位置的源代码，以及 breakpoint 创建时显示它所在的源代码段
        - 使用 [pygments](https://pygments.org/) 进行 C/C++ 语法高亮
        - 源文件中的制表符 "\t" 将被替换为 4 个空格
    5. *Breakpoints*
        - 记录 breakpoint/watchpoint 的序号、源文件名、行号、函数名、条件（若存在）、触发次数
    6. *Stack*
        - 记录每一层栈帧的序号、源文件名、行号、函数名
- 若需要拓展自定义的 **pane**，只需要继承 `Panel.Pane` 类并重载 `refresh_content()` 方法
### 遍历 STL 容器
- **container_iter.py** 提供 `list_iter`, `map_iter` 方法，它们统一接受两个参数
    1. `expression`：类型为 `str`，内容为一个 gdb 表达式，必须保证表达式的值的类型与所调用的函数一致（`std::list`, `std::map`）
    2. `callback`：回调函数，它必须接受一个类型为 `gdb.Value` 的参数，返回值类型为 `bool`
- `list_iter`, `map_iter` 将遍历 `expression` 指向的容器对象，并将获得的每个元素传给 `callback`；如果 `callback` 返回值为 `True`，停止遍历
    - `map_iter` 传入的 `gdb.Value` 是一个 `std::pair`，通过访问其 `first`,`second` 成员来获得 `std::map` 的 key 和 value
- `gdb.Value` 代表了 inferior 中的一个变量，参考[manual](https://sourceware.org/gdb/onlinedocs/gdb/Values-From-Inferior.html#Values-From-Inferior)

## Documents
### Config
- GdbPanel 的配置直接写在 python 源代码中，进行定制可以直接修改源文件，也可以另写语句为 `Panel.config` 这个属性赋新值
- **layout**：
    - **slots**
        1. 此项配置实际上是描述如何用矩形铺满 terminal 显示区域，需要明确定义 slot 的序号、宽高、位置
        2. `[id, width, height]` 这样一个 `list` 定义了一个 slot：
            - id 可以是任意整数，但不能重复，用于指定与 **pane** 的映射关系
            - width/height 是两个 [1, 10] 之间的整数，单位分别是 terminal 显示区域宽/高的 1/10
        3. slot 之间的位置关系通过一棵二叉树来表示，假设 *S* 是一个 slot，树的构建规则如下：
            - 每个 slot 都是树中的一个节点，左上角的 slot 是 root
            - *S*.*left_child* 表示左侧边与 *S* 对齐，顶边与 *S* 底边重合的另一个 slot
            - *S*.*right_child* 表示顶边与 *S* 对齐，左侧边与 *S* 右侧边重合的另一个 slot
        4. 上述二叉树在 config 中用一个 `list` 来表示，假设 *S* 是一个 slot：
            - slot[0] 是 root
            - 假设 *S* 是第 i 个元素，那么第 i+1 个元素必须是 *S* 的 *right_child*，如果 *S*.*right_child* 不存在，那么填 `None`
            - 假设按照上述规则完成对 *S*.*right_child* 的定义后，最后一个元素下标为 j，那么第 j+1 个元素必须是 *S* 的 *left_child*，若不存在填 `None`
        5. **注意**：一个合法的 slots config，最后所有矩形拼起来必须是一个 10x10 的正方形
    - **panes**
        1. 一个 `dict`，键值对为 "pane name: slot id"
        2. 没有指定 slot 的 pane 将不会显示
        3. **注意**：必须为每个 slot 分配一个 pane
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
        Panel.config['layout'] = {
            'slots': [[0, 6, 8], [1, 4, 6], None, [2, 4, 4], None, None, [3, 6, 2], None, None],

            'panes': {'Source': 0, 'Watch': 1, 'Stack': 2, 'Breakpoints': 3}
        }
        ```
- **style**
    - 外观配置项，参考源代码注释
- **auto-render**
    - 若设为 `True`，除了 `panel silent` 命令或出现 error，gdb 命令执行完毕后将自动显示 panel
### Commands
0. 以下命令在 gdb 输入
1. panel
    - 显示 GdbPanel
1. panel view *PANE* *SLOT*
    - *PANE*, str, pane 名字.
    - *SLOT*, int, config 中定义的 slot id.
    - 将 *PANE* 分配到 *SLOT* 显示
    - 如果 *PANE* 已经在一个 slot 中，交换两个 slot 的 pane.
    - 如果 *PANE* 是隐藏的，那么目标 slot 原来的 pane 将被隐藏.
3. panel silent *COMMAND*
    - *COMMAND*, str
    - 调用 gdb 执行命令 *COMMAND*，暂停显示 panel 一次
4. panel layout *CONFIG_KEY*
    - *CONFIG_KEY*，类型由实际配置决定
    - 使用 `panel.layout_configs[CONFIG_KEY]` 作为当前 layout 配置
    - 要使用快速切换 layout 的功能，必须先为 **panel** 添加一项属性 **layout_configs**，可以是 `list`, `dict` 或者任意支持索引操作的数据类型
5. panel run *ARGS*
	- launch an inferior process with *ARGS*, with GdbPanel's internal logger enabled
6. panel watch *EXPRESSION*
	- add *EXPRESSION* to panel's watch list
7. panel unwatch *EXPRESSION*
	- delete *EXPRESSION* from panel's watch list
8. panel flush
	- try flush inferior (c/c++) process's output stream buffer & read new logs
