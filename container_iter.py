from libstdcxx.v6.printers import lookup_node_type, get_value_from_list_node
from typing import Callable
import gdb

def std_list_iter(expression: str, func: Callable[[gdb.Value], bool]) -> None:
    try:
        list_obj = gdb.parse_and_eval(expression)
    except gdb.error as e:
        print(f'ERROR: {e}.')
        return

    node_type = lookup_node_type('_List_node', list_obj.type).pointer()

    try:
        head = list_obj['_M_impl']['_M_node'].address
    except gdb.error:
        typename = str(list_obj.type) if not list_obj.type.name else list_obj.type.name
        print(f'ERROR: type of {expression} is {typename} but not std::list.')
        return

    node = head.dereference()['_M_next']
    while node != head:
        node = node.cast(node_type).dereference()
        elem = get_value_from_list_node(node)
        if func(elem):
            break
        node = node['_M_next']