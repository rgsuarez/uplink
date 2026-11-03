"""Deterministic source mutations; expected behaviors come from independent case specs."""

import ast
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class Mutation:
    function: str
    kind: str
    original: str
    planted: str


def _offsets(source: str) -> list[int]:
    result = [0]
    for line in source.splitlines(keepends=True):
        result.append(result[-1] + len(line.encode()))
    return result


def _span(node: ast.expr | ast.stmt, offsets: list[int]) -> tuple[int, int]:
    if node.end_lineno is None or node.end_col_offset is None:
        raise ValueError("AST source span missing")
    return offsets[node.lineno - 1] + node.col_offset, offsets[
        node.end_lineno - 1
    ] + node.end_col_offset


def generate_mutations(source: str, functions: frozenset[str]) -> Iterator[Mutation]:
    raw = source.encode()
    offsets = _offsets(source)
    tree = ast.parse(source)
    seen: set[str] = set()
    for top in tree.body:
        if isinstance(top, ast.FunctionDef):
            members = [(top.name, top)]
        elif isinstance(top, ast.ClassDef):
            members = [
                (f"{top.name}.{m.name}", m) for m in top.body if isinstance(m, ast.FunctionDef)
            ]
        else:
            continue
        for name, function in members:
            if name not in functions:
                continue
            start, end = _span(function, offsets)
            original = raw[start:end].decode()
            if source.count(original) != 1:
                continue
            nodes = sorted(
                (n for n in ast.walk(function) if isinstance(n, ast.expr)),
                key=lambda n: (n.lineno, n.col_offset, type(n).__name__),
            )
            for node in nodes:
                replacements: list[tuple[str, str]] = []
                text = ast.get_source_segment(source, node)
                if text is None:
                    continue
                if isinstance(node, ast.BinOp):
                    for symbol, op in (("+", ast.Add), ("-", ast.Sub), ("*", ast.Mult)):
                        if not isinstance(node.op, op):
                            lhs = ast.get_source_segment(source, node.left)
                            rhs = ast.get_source_segment(source, node.right)
                            replacements.append(
                                ("arithmetic_operator", f"({lhs}) {symbol} ({rhs})")
                            )
                elif isinstance(node, ast.Compare) and len(node.ops) == 1:
                    lhs = ast.get_source_segment(source, node.left)
                    rhs = ast.get_source_segment(source, node.comparators[0])
                    for symbol in ("<", "<=", ">", ">=", "==", "!="):
                        replacements.append(("comparison_boundary", f"({lhs}) {symbol} ({rhs})"))
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    swaps = {
                        "min": "max",
                        "max": "min",
                        "abs": "int",
                        "_ceil": "_floor",
                        "_floor": "_ceil",
                    }
                    other = swaps.get(node.func.id)
                    if other:
                        replacements.append(("builtin_swap", other + text[len(node.func.id) :]))
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    swaps = {
                        "lower": "upper",
                        "upper": "lower",
                        "strip": "lstrip",
                        "lstrip": "rstrip",
                        "capitalize": "lower",
                    }
                    other = swaps.get(node.func.attr)
                    if other:
                        replacements.append(
                            (
                                "string_method",
                                text.replace("." + node.func.attr + "(", "." + other + "(", 1),
                            )
                        )
                elif isinstance(node, ast.Constant) and type(node.value) is int:
                    for delta in (-1, 1):
                        replacements.append(("integer_boundary", str(node.value + delta)))
                if any(
                    isinstance(parent, ast.Return) and parent.value is node
                    for parent in ast.walk(function)
                ):
                    replacements += [("return_value", value) for value in ("None", "0", "''", "[]")]
                nstart, nend = _span(node, offsets)
                for kind, replacement in replacements:
                    changed = (raw[start:nstart] + replacement.encode() + raw[nend:end]).decode()
                    if changed == original or changed in seen:
                        continue
                    seen.add(changed)
                    broken = source.replace(original, changed, 1)
                    if broken.count(changed) != 1:
                        continue
                    try:
                        ast.parse(broken)
                    except SyntaxError:
                        continue
                    yield Mutation(name, kind, original, changed)
