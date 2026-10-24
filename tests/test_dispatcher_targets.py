"""Dispatcher targets must run on the event loop (regression, 2.10.2).

Home Assistant runs a dispatcher target that is neither a coroutine function
nor decorated with ``@callback`` in the executor. v2.10.0/v2.10.1 passed a
lambda for ``SIGNAL_GATEWAY_UPDATE``, so the device registry update ran in a
worker thread and Home Assistant refused it ("calls
device_registry._async_update_device from a thread"): the gateway device
never showed the firmware read from a real USB 300.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "enocean_custom"
)


def _callback_functions(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.AsyncFunctionDef):
                names.add(node.name)
            for deco in node.decorator_list:
                if isinstance(deco, ast.Name) and deco.id == "callback":
                    names.add(node.name)
    return names


class DispatcherTargetTests(unittest.TestCase):
    def test_no_dispatcher_target_runs_in_the_executor(self):
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            safe = _callback_functions(tree)
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", getattr(node.func, "attr", None))
                    == "async_dispatcher_connect"
                    and len(node.args) >= 3
                ):
                    continue
                target = node.args[2]
                if isinstance(target, ast.Lambda):
                    offenders.append(f"{path.name}:{node.lineno} lambda")
                    continue
                name = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else getattr(target, "id", None)
                )
                if name == "packet_callback":
                    # learn.py forwards a handler; every handler passed as
                    # ``packet_callback=`` is checked below instead.
                    continue
                if name is not None and name not in safe:
                    offenders.append(f"{path.name}:{node.lineno} {name}")
        self.assertEqual(offenders, [])

    def test_learn_window_handlers_are_callbacks(self):
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            safe = _callback_functions(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "packet_callback":
                        continue
                    value = keyword.value
                    name = getattr(value, "attr", getattr(value, "id", None))
                    if isinstance(value, ast.Lambda) or name not in safe:
                        offenders.append(f"{path.name}:{node.lineno} {name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
