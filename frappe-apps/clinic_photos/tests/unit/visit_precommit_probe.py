"""Offline source-location probe only; never imports Frappe or contacts a site."""

import ast
import json
import pathlib


def find_boundary(source):
    tree = ast.parse(source)
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_publish_prepared_visit"
    ]
    if len(functions) != 1:
        raise ValueError("visit publication function missing or ambiguous")
    names = (
        "capture.insert_prepared_slot_link",
        "journal.publish",
        "writer.arm_visit_final_commit",
        "writer.finish_visit_transaction",
    )
    calls = {}
    for node in ast.walk(functions[0]):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in names:
                calls.setdefault(name, []).append(node)
    if any(len(calls.get(name, [])) != 1 for name in names):
        raise ValueError("visit publication boundary missing or ambiguous")
    selected = [calls[name][0] for name in names]
    if (sorted(node.lineno for node in selected) != [node.lineno for node in selected]
        or len({node.lineno for node in selected}) != len(selected)
        or len(selected[-1].args) != 3
        or not isinstance(selected[-1].args[2], ast.Constant)
        or selected[-1].args[2].value != "commit"):
        raise ValueError("visit publication boundary changed")
    return {
        "slot_line": selected[0].lineno,
        "publish_line": selected[1].lineno,
        "arm_line": selected[2].lineno,
        "commit_line": selected[3].lineno,
    }


if __name__ == "__main__":
    source_path = pathlib.Path(__file__).parents[2] / "clinic_photos/publication/coordinator.py"
    print(json.dumps(find_boundary(source_path.read_text()), sort_keys=True))
