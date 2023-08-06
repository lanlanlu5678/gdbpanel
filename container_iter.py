from libstdcxx.v6.printers import find_type, lookup_node_type, get_value_from_list_node, RbtreeIterator, get_value_from_Rb_tree_node
from typing import Callable, Union, Any
import gdb
import re

handler = Callable[[gdb.Value], bool]

def get_val(expression: str, name: str) -> gdb.Value:
    try:
        val = gdb.parse_and_eval(expression)
    except gdb.error:
        print(f'Error: undefined {name} expression {expression}.')
        val = None
    if val != None and name not in val.type.tag:
        print(f'Error: expression {expression} is not a {name}.')
        val = None
    return val

def list_iter(list_expr: str, func: handler) -> None:
    list_val = get_val(list_expr, 'list')
    if not list_val:
        return

    node_type = lookup_node_type('_List_node', list_val.type).pointer()
    head = list_val['_M_impl']['_M_node'].address
    node = head.dereference()['_M_next']

    while node != head:
        node = node.cast(node_type).dereference()
        if func(get_value_from_list_node(node)):
            break
        node = node['_M_next']

def map_iter(map_expr: str, func: handler) -> None:
    map_val = get_val(map_expr, 'map')
    if not map_val:
        return

    node_type = lookup_node_type('_Rb_tree_node', map_val.type).pointer()
    map_iter = RbtreeIterator(map_val)

    for pair in map_iter:
        pair = pair.cast(node_type).dereference()
        if func(get_value_from_Rb_tree_node(pair)):
            break