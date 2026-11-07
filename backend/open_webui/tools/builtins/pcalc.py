'''"""Open Web Vibes builtin: Calculator

Generated from the WebUI tool export `pcalc` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: Calculator
Exported functions: calculate, evaluate_batch, solve_linear, solve_quadratic

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
import ast
import math
import operator
from typing import List, Optional, Union
from pydantic import BaseModel, Field
_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_COMPARE_OPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
_NAMES = {'pi': math.pi, 'e': math.e, 'tau': math.tau, 'inf': math.inf, 'infinity': math.inf, 'nan': math.nan, 'abs': abs, 'round': round, 'min': min, 'max': max, 'sum': sum, 'pow': pow, 'sqrt': math.sqrt, 'cbrt': math.cbrt, 'exp': math.exp, 'log': math.log, 'log2': math.log2, 'log10': math.log10, 'sin': math.sin, 'cos': math.cos, 'tan': math.tan, 'asin': math.asin, 'acos': math.acos, 'atan': math.atan, 'atan2': math.atan2, 'degrees': math.degrees, 'radians': math.radians, 'factorial': math.factorial, 'gcd': math.gcd, 'floor': math.floor, 'ceil': math.ceil}
_MAX_INT_EXP = 10000

class Tools:

    def __init__(self):
        """Initialize the Tool."""
        self.valves = self.Valves()

    class Valves(BaseModel):
        float_digits: int = Field(12, description='Significant digits used when formatting floating-point results.')

    async def calculate(self, expression: str, precision: Optional[int]=None) -> str:
        """
        Safely evaluate a mathematical expression and return the result as a string.

        Supports + - * / // % ** with correct precedence, parentheses, comparisons
        (=, !=, <, <=, >, >=), and the functions/constants listed in @description.

        Examples:
            "2 + 2"                 -> "4"
            "(3 + 5) * 2 ** 3"      -> "64"
            "sqrt(2)"               -> "1.41421356237"
            "10 % 3"                -> "1"
            "2 * pi"                -> "6.28318530718"
            "factorial(5)"          -> "120"
            "sin(radians(90))"      -> "1.0"
            "max(3, 7, min(1, 2))"  -> "7"

        :param expression: The math expression to evaluate.
        :param precision: Optional number of significant digits for float results
                          (overrides the tool's float_digits setting for this call).
        :return: A human-readable string with the result, or an error message.
        """
        digits = precision if precision is not None else self.valves.float_digits
        try:
            tree = ast.parse(expression.strip(), mode='eval')
            value = self._eval_node(tree.body)
        except ZeroDivisionError:
            return 'Error: division by zero.'
        except OverflowError:
            return 'Error: numeric overflow (result is too large).'
        except MemoryError:
            return 'Error: computation too large to perform.'
        except ValueError as exc:
            return f'Error: {exc}'
        except Exception as exc:
            return f'Error: {type(exc).__name__}: {exc}'
        return self._format(value, digits)

    async def evaluate_batch(self, expressions: List[str]) -> List[dict]:
        """
        Evaluate a list of math expressions at once.

        :param expressions: A list of math expressions to evaluate.
        :return: A list of {expression, result} objects, one per input.
        """
        out = []
        for expr in expressions:
            out.append({'expression': expr, 'result': await self.calculate(expr)})
        return out

    async def solve_linear(self, a: Union[int, float], b: Union[int, float], c: Union[int, float]) -> str:
        """
        Solve the linear equation a*x + b = c for x.

        :param a: Coefficient of x (must not be 0).
        :param b: Constant term on the left.
        :param c: Right-hand side value.
        :return: The value of x, or an error if a is 0 (no/infinitely many solutions).
        """
        if a == 0:
            if b == c:
                return 'Infinite solutions (0 = 0, identity).'
            return 'No solution (0 = ' + str(c - b) + ').'
        x = (c - b) / a
        return f'x = {self._format(x, self.valves.float_digits)}'

    async def solve_quadratic(self, a: Union[int, float], b: Union[int, float], c: Union[int, float]) -> str:
        """
        Solve a*x^2 + b*x + c = 0 using the quadratic formula.

        Returns both roots; if the discriminant is negative the roots are complex.

        :param a: Leading coefficient (must not be 0).
        :param b: Middle coefficient.
        :param c: Constant term.
        :return: The two roots, or an error if a is 0.
        """
        if a == 0:
            return 'Error: not a quadratic (a must not be 0).'
        disc = b * b - 4 * a * c
        if disc < 0:
            root = math.sqrt(-disc) / (2 * a)
            real = -b / (2 * a)
            r1 = complex(real, root)
            r2 = complex(real, -root)
        else:
            s = math.sqrt(disc)
            r1 = (-b + s) / (2 * a)
            r2 = (-b - s) / (2 * a)
        d = self.valves.float_digits
        return f'x1 = {self._format(r1, d)}\nx2 = {self._format(r2, d)}'

    def _safe_pow(self, base, exp):
        if isinstance(base, int) and isinstance(exp, int) and (exp > _MAX_INT_EXP):
            raise ValueError('Exponent too large.')
        return base ** exp

    def _eval_node(self, node):
        if isinstance(node, ast.Expression):
            return self._eval_node(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError('Unsupported constant: %r' % (node.value,))
        if isinstance(node, ast.Name):
            if node.id in _NAMES:
                return _NAMES[node.id]
            raise ValueError('Unknown name: %s' % (node.id,))
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in _BIN_OPS:
                raise ValueError('Unsupported operator: %s' % op_type.__name__)
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            if op_type is ast.Pow:
                return self._safe_pow(left, right)
            return _BIN_OPS[op_type](left, right)
        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in _UNARY_OPS:
                raise ValueError('Unsupported unary operator: %s' % op_type.__name__)
            return _UNARY_OPS[op_type](self._eval_node(node.operand))
        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                op_type = type(op)
                if op_type not in _COMPARE_OPS:
                    raise ValueError('Unsupported comparison: %s' % op_type.__name__)
                right = self._eval_node(comparator)
                if not _COMPARE_OPS[op_type](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result = True
                for value in node.values:
                    result = self._eval_node(value)
                    if not result:
                        return result
                return result
            if isinstance(node.op, ast.Or):
                result = False
                for value in node.values:
                    result = self._eval_node(value)
                    if result:
                        return result
                return result
            raise ValueError('Unsupported boolean operator.')
        if isinstance(node, ast.List):
            return [self._eval_node(el) for el in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple((self._eval_node(el) for el in node.elts))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError('Only direct function calls are allowed.')
            fname = node.func.id
            if fname not in _NAMES or not callable(_NAMES[fname]):
                raise ValueError('Unknown function: %s' % (fname,))
            args = [self._eval_node(a) for a in node.args]
            if node.keywords:
                raise ValueError('Keyword arguments are not supported.')
            return _NAMES[fname](*args)
        raise ValueError('Unsupported expression element: %s' % type(node).__name__)

    def _format(self, value, digits: int=12) -> str:
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, complex):
            return self._format_complex(value, digits)
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if math.isnan(value):
                return 'NaN'
            if math.isinf(value):
                return 'Infinity' if value > 0 else '-Infinity'
            if value.is_integer() and abs(value) < 1000000000000000.0:
                return str(int(value))
            return f'{value:.{digits}g}'
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return str(value)
        return repr(value)

    def _format_complex(self, z: complex, digits: int=12) -> str:
        real = self._format(z.real, digits)
        imag = self._format(z.imag, digits)
        sign = '+' if z.imag >= 0 else '-'
        imag_abs = self._format(abs(z.imag), digits)
        return f'({real} {sign} {imag_abs}i)'
_builtin_tools = Tools()

async def calculate(self, expression: str, precision: Optional[int]=None) -> str:
    """
        Safely evaluate a mathematical expression and return the result as a string.

        Supports + - * / // % ** with correct precedence, parentheses, comparisons
        (=, !=, <, <=, >, >=), and the functions/constants listed in @description.

        Examples:
            "2 + 2"                 -> "4"
            "(3 + 5) * 2 ** 3"      -> "64"
            "sqrt(2)"               -> "1.41421356237"
            "10 % 3"                -> "1"
            "2 * pi"                -> "6.28318530718"
            "factorial(5)"          -> "120"
            "sin(radians(90))"      -> "1.0"
            "max(3, 7, min(1, 2))"  -> "7"

        :param expression: The math expression to evaluate.
        :param precision: Optional number of significant digits for float results
                          (overrides the tool's float_digits setting for this call).
        :return: A human-readable string with the result, or an error message.
        """
    return await _builtin_tools.calculate(expression=expression, precision=precision)

async def evaluate_batch(self, expressions: List[str]) -> List[dict]:
    """
        Evaluate a list of math expressions at once.

        :param expressions: A list of math expressions to evaluate.
        :return: A list of {expression, result} objects, one per input.
        """
    return await _builtin_tools.evaluate_batch(expressions=expressions)

async def solve_linear(self, a: Union[int, float], b: Union[int, float], c: Union[int, float]) -> str:
    """
        Solve the linear equation a*x + b = c for x.

        :param a: Coefficient of x (must not be 0).
        :param b: Constant term on the left.
        :param c: Right-hand side value.
        :return: The value of x, or an error if a is 0 (no/infinitely many solutions).
        """
    return await _builtin_tools.solve_linear(a=a, b=b, c=c)

async def solve_quadratic(self, a: Union[int, float], b: Union[int, float], c: Union[int, float]) -> str:
    """
        Solve a*x^2 + b*x + c = 0 using the quadratic formula.

        Returns both roots; if the discriminant is negative the roots are complex.

        :param a: Leading coefficient (must not be 0).
        :param b: Middle coefficient.
        :param c: Constant term.
        :return: The two roots, or an error if a is 0.
        """
    return await _builtin_tools.solve_quadratic(a=a, b=b, c=c)
