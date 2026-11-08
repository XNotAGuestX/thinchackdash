"""Datadog widget formulas: a 4-operator grammar over query names and numbers (plan 05).

    expr   := term (("+" | "-") term)*
    term   := factor (("*" | "/") factor)*
    factor := number | ident | "abs(" expr ")" | "(" expr ")"

Spaces are allowed between tokens; the full input must be consumed. Anything else
(other functions such as ``top(...)``, unary minus, exponents) is rejected.

``abs(expr)`` (#119) is Datadog's arithmetic function "Graph the absolute value of the
metric." (https://docs.datadoghq.com/dashboards/functions/arithmetic/#absolute); arithmetic
functions "apply changes to the returned results of the metric query"
(https://docs.datadoghq.com/dashboards/functions/). PromQL ``abs(v instant-vector)``
"returns a vector containing all float samples in the input vector converted to their
absolute value" (https://prometheus.io/docs/prometheus/latest/querying/functions/#abs).
Both are pointwise per series, so the translation is exact. The argument must reference a
query: PromQL ``abs()`` rejects a scalar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Op = Literal["+", "-", "*", "/"]


@dataclass(frozen=True)
class Num:
    value: str


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Bin:
    op: Op
    left: Formula
    right: Formula


@dataclass(frozen=True)
class Abs:
    arg: Formula


Formula = Num | Ref | Bin | Abs


@dataclass(frozen=True)
class FormulaOk:
    formula: Formula
    ok: Literal[True] = True


@dataclass(frozen=True)
class FormulaError:
    offset: int
    ok: Literal[False] = False


@dataclass(frozen=True)
class RenderedQuery:
    promql: str
    group_labels: list[str]


@dataclass(frozen=True)
class RenderOk:
    promql: str
    group_labels: list[str]
    ok: Literal[True] = True


@dataclass(frozen=True)
class RenderError:
    reason: str
    ok: Literal[False] = False


class _Fail(Exception):
    def __init__(self, pos: int) -> None:
        self.pos = pos


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def skip_spaces(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] == " ":
            self.pos += 1

    def peek(self) -> str:
        self.skip_spaces()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def expr(self) -> Formula:
        node = self.term()
        while self.peek() in ("+", "-"):
            op: Op = self.text[self.pos]  # type: ignore[assignment]
            self.pos += 1
            node = Bin(op, node, self.term())
        return node

    def term(self) -> Formula:
        node = self.factor()
        while self.peek() in ("*", "/"):
            op: Op = self.text[self.pos]  # type: ignore[assignment]
            self.pos += 1
            node = Bin(op, node, self.factor())
        return node

    def factor(self) -> Formula:
        ch = self.peek()
        if ch == "(":
            self.pos += 1
            node = self.expr()
            if self.peek() != ")":
                raise _Fail(self.pos)
            self.pos += 1
            return node
        start = self.pos
        if ch.isascii() and ch.isdigit():
            while (
                self.pos < len(self.text)
                and self.text[self.pos].isascii()
                and (self.text[self.pos].isdigit())
            ):
                self.pos += 1
            if self.pos < len(self.text) and self.text[self.pos] == ".":
                self.pos += 1
                digits = self.pos
                while (
                    self.pos < len(self.text)
                    and self.text[self.pos].isascii()
                    and (self.text[self.pos].isdigit())
                ):
                    self.pos += 1
                if self.pos == digits:
                    raise _Fail(self.pos)
            return Num(self.text[start : self.pos])
        if ch == "_" or (ch.isascii() and ch.isalpha()):
            while self.pos < len(self.text) and (
                self.text[self.pos] == "_"
                or (self.text[self.pos].isascii() and self.text[self.pos].isalnum())
            ):
                self.pos += 1
            name = self.text[start : self.pos]
            if name == "abs" and self.pos < len(self.text) and self.text[self.pos] == "(":
                self.pos += 1
                arg = self.expr()
                if self.peek() != ")" or not references(arg):
                    raise _Fail(self.pos)
                self.pos += 1
                return Abs(arg)
            return Ref(name)
        raise _Fail(self.pos)


def parse_formula(text: str) -> FormulaOk | FormulaError:
    parser = _Parser(text)
    try:
        node = parser.expr()
        if parser.peek():
            raise _Fail(parser.pos)
    except _Fail as fail:
        return FormulaError(offset=fail.pos)
    return FormulaOk(formula=node)


def references(f: Formula) -> list[str]:
    """Referenced query names, first-occurrence order, without duplicates."""
    out: list[str] = []

    def walk(node: Formula) -> None:
        if isinstance(node, Ref):
            if node.name not in out:
                out.append(node.name)
        elif isinstance(node, Bin):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, Abs):
            walk(node.arg)

    walk(f)
    return out


_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}


def _render(f: Formula, compiled: dict[str, RenderedQuery]) -> str:
    if isinstance(f, Num):
        return f.value
    if isinstance(f, Ref):
        return f"({compiled[f.name].promql})"
    if isinstance(f, Abs):
        arg = compiled[f.arg.name].promql if isinstance(f.arg, Ref) else _render(f.arg, compiled)
        return f"abs({arg})"
    prec = _PRECEDENCE[f.op]
    left = _render(f.left, compiled)
    if isinstance(f.left, Bin) and _PRECEDENCE[f.left.op] < prec:
        left = f"({left})"
    right = _render(f.right, compiled)
    # Right operands of equal precedence need parentheses: a - (b - c), a / (b * c).
    if isinstance(f.right, Bin) and _PRECEDENCE[f.right.op] <= prec:
        right = f"({right})"
    return f"{left} {f.op} {right}"


def render_formula(f: Formula, compiled: dict[str, RenderedQuery]) -> RenderOk | RenderError:
    refs = references(f)
    missing = [name for name in refs if name not in compiled]
    if missing:
        return RenderError(reason=f"unknown query '{missing[0]}'")
    label_sets = {frozenset(compiled[name].group_labels) for name in refs}
    if len(label_sets) > 1:
        return RenderError(reason="grouping mismatch")
    group_labels = compiled[refs[0]].group_labels if refs else []
    # A bare query name is the identity formula: emit the query unwrapped.
    promql = compiled[f.name].promql if isinstance(f, Ref) else _render(f, compiled)
    return RenderOk(promql=promql, group_labels=list(group_labels))
