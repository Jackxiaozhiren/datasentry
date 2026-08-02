"""11.10 安全表达式求值器（读操作子集，ADR-015）。

安全约束（规格 11.10）：
1. 仅读操作：表达式为 eval 模式 Python 子集，无赋值/语句；
2. AST 白名单：节点类型显式白名单 + Name/Attr/Call 三重检查，
   禁止 import/eval/exec/open/subprocess/getattr 等；
3. 超时 10s（SIGALRM，仅主线程可用）；
4. 结果缓存：表达式哈希为键（AST 编译缓存 + 行级结果缓存）。
"""

from __future__ import annotations

import ast
import hashlib
import signal
from dataclasses import dataclass
from typing import Any

# 允许的节点类型（白名单，规格约束 2）
_ALLOWED_NODE_TYPES: frozenset[type[ast.AST]] = frozenset(
    {
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.Call,
        ast.Attribute,
        ast.IfExp,
        ast.Tuple,
        ast.List,
        ast.Set,
        ast.Dict,
        # 运算符
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.And,
        ast.Or,
        ast.Not,
        ast.USub,
        ast.UAdd,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
    }
)

# 注入求值环境的白名单内置函数（读操作）
_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "len": len,
    "min": min,
    "max": max,
    "round": round,
    "int": int,
    "float": float,
    "bool": bool,
    "str": str,
}

# 禁止的标识符（规格约束 2：import/eval/exec/open/subprocess）
_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "open",
        "input",
        "compile",
        "execfile",
        "__import__",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "subprocess",
        "os",
        "sys",
        "importlib",
        "type",
        "object",
    }
)

# 允许的实例方法（str 安全读方法）
_ALLOWED_ATTRS: frozenset[str] = frozenset(
    {
        "lower",
        "upper",
        "strip",
        "lstrip",
        "rstrip",
        "isalpha",
        "isdigit",
        "isnumeric",
        "isalnum",
        "isspace",
        "startswith",
        "endswith",
        "isdecimal",
        "title",
        "capitalize",
    }
)


class ExpressionSecurityError(ValueError):
    """表达式未通过 AST 白名单校验。"""


class ExpressionTimeoutError(TimeoutError):
    """表达式求值超过时限（规格约束 3，默认 10s）。"""


@dataclass(frozen=True)
class EvalResult:
    """一次批量求值的结果。

    values 元素：True 通过 / False 违规 / None 不适用（None 参与运算、除零等）。
    """

    values: tuple[bool | None, ...]
    evaluated: int
    timed_out: bool = False


class SafeExpressionEvaluator:
    """安全求值器：validate + evaluate。

    evaluate 接受列值元组序列（行级），逐行求值；
    编译缓存与结果缓存均以表达式哈希为键（规格约束 4）。
    """

    def __init__(self, timeout_s: float = 10.0, max_cache_size: int = 1_000_000) -> None:
        self.timeout_s = timeout_s
        self.max_cache_size = max_cache_size
        self._compile_cache: dict[str, ast.Expression] = {}
        self._result_cache: dict[tuple[str, tuple[Any, ...]], bool | None] = {}

    def validate(self, expression: str) -> str:
        """AST 白名单校验；返回表达式哈希（错误抛 ExpressionSecurityError）。"""
        tree = self._compile(expression)
        for node in ast.walk(tree):
            if type(node) not in _ALLOWED_NODE_TYPES:
                raise ExpressionSecurityError(
                    f"disallowed AST node: {type(node).__name__} in {expression!r}"
                )
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
                raise ExpressionSecurityError(f"disallowed identifier: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr not in _ALLOWED_ATTRS:
                raise ExpressionSecurityError(
                    f"disallowed attribute access: .{node.attr} in {expression!r}"
                )
            if isinstance(node, ast.Call):
                self._check_call(node, expression)
        return self._hash(expression)

    def _check_call(self, node: ast.Call, expression: str) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in _FORBIDDEN_NAMES:
                raise ExpressionSecurityError(f"disallowed function: {func.id}")
            if func.id not in _SAFE_BUILTINS:
                raise ExpressionSecurityError(
                    f"function not whitelisted: {func.id} in {expression!r}"
                )
        elif isinstance(func, ast.Attribute):
            if func.attr not in _ALLOWED_ATTRS:
                raise ExpressionSecurityError(
                    f"method not whitelisted: .{func.attr} in {expression!r}"
                )
        else:
            raise ExpressionSecurityError("complex call target not allowed")

    def _compile(self, expression: str) -> ast.Expression:
        try:
            return self._compile_cache.setdefault(expression, ast.parse(expression, mode="eval"))
        except SyntaxError as exc:
            raise ExpressionSecurityError(f"invalid expression: {exc}") from exc

    def _hash(self, expression: str) -> str:
        return hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16]

    def _eval_row(
        self,
        expr_hash: str,
        tree: ast.Expression,
        row: tuple[Any, ...],
        variables: dict[str, Any],
    ) -> bool | None:
        key = (expr_hash, row)
        if key in self._result_cache:
            return self._result_cache[key]
        if len(self._result_cache) >= self.max_cache_size:
            self._result_cache.clear()
        self._result_cache[key] = self._run(tree, variables)
        return self._result_cache[key]

    def _run(self, tree: ast.Expression, variables: dict[str, Any]) -> bool | None:
        code = compile(tree, "<expr>", "eval")

        def _handler(signum: int, frame: Any) -> None:
            raise ExpressionTimeoutError(f"expression evaluation exceeded {self.timeout_s}s")

        old_handler = signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, self.timeout_s)
        try:
            result = eval(code, {"__builtins__": {}, **_SAFE_BUILTINS}, variables)
            return bool(result)
        except (TypeError, AttributeError, ZeroDivisionError):
            return None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)

    def evaluate(
        self,
        expression: str,
        columns: list[str],
        rows: list[tuple[Any, ...]],
    ) -> EvalResult:
        """批量求值；columns 显式声明变量名（与每行元组对齐）。

        表达式引用的标识符必须 ⊆ columns ∪ 白名单内置；先 validate。
        """
        expr_hash = self.validate(expression)
        tree = self._compile(expression)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        unknown = names - set(columns) - set(_SAFE_BUILTINS)
        if unknown:
            raise ExpressionSecurityError(f"unknown identifiers: {sorted(unknown)}")
        results: list[bool | None] = []
        for row in rows:
            variables = dict(zip(columns, row, strict=True))
            try:
                results.append(self._eval_row(expr_hash, tree, tuple(row), variables))
            except ExpressionTimeoutError:
                return EvalResult(tuple(results), len(results), timed_out=True)
        return EvalResult(tuple(results), len(results))
