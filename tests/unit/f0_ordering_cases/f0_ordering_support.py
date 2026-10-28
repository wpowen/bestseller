"""Component-only SELECT adapter; no engine, database, grade or adoption proof.

The adapter executes the small, ordinary filter/order/limit subset used by these
readers. Assertions are on returned domain values, never SQL strings or AST
shapes. Ties retain input arrival order, as an unordered database result can.
Unsupported expressions fail instead of being treated as a successful read.
"""

from contextlib import asynccontextmanager, nullcontext
from functools import cmp_to_key
from uuid import UUID

from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    False_,
    Null,
    True_,
    UnaryExpression,
)


def uid(number):
    """Fixed, distinct version-4 UUIDs reused across both arrival orders."""
    return UUID(f"00000000-0000-4000-8000-{number:012x}")


def _value(expression, row):
    if isinstance(expression, BindParameter):
        return expression.value
    if isinstance(expression, Null):
        return None
    if isinstance(expression, True_):
        return True
    if isinstance(expression, False_):
        return False
    if isinstance(expression, BooleanClauseList):
        values = [_value(clause, row) for clause in expression.clauses]
        if expression.operator is operators.and_:
            return all(values)
        if expression.operator is operators.or_:
            return any(values)
        raise AssertionError(f"unsupported boolean operator: {expression.operator}")
    if isinstance(expression, BinaryExpression):
        left, right = _value(expression.left, row), _value(expression.right, row)
        operation = expression.operator
        if operation is operators.json_getitem_op:
            return left.get(right) if isinstance(left, dict) else None
        if operation is operators.is_:
            return left is right
        if operation is operators.is_not:
            return left is not right
        if operation is operators.in_op:
            return left in right
        if operation is operators.not_in_op:
            return left not in right
        # SQL comparisons involving NULL are not qualifying WHERE results.
        if left is None or right is None:
            return False
        if operation in {operators.eq, operators.ne, operators.lt, operators.le,
                         operators.gt, operators.ge}:
            return operation(left, right)
        raise AssertionError(f"unsupported comparison: {operation}")
    key = getattr(expression, "key", None)
    if key is not None:
        return getattr(row, "metadata_json" if key == "metadata" else key)
    raise AssertionError(f"unsupported SELECT expression: {type(expression).__name__}")


def _ordered(rows, clauses):
    result = list(rows)
    for clause in reversed(tuple(clauses)):
        descending, nulls_first = False, None
        while isinstance(clause, UnaryExpression):
            if clause.modifier is operators.desc_op:
                descending = True
            elif clause.modifier is operators.asc_op:
                descending = False
            elif clause.modifier is operators.nulls_first_op:
                nulls_first = True
            elif clause.modifier is operators.nulls_last_op:
                nulls_first = False
            else:
                raise AssertionError(f"unsupported ORDER BY modifier: {clause.modifier}")
            clause = clause.element
        if nulls_first is None:
            nulls_first = descending  # PostgreSQL's default NULL placement.

        def compare(a, b):
            left, right = _value(clause, a), _value(clause, b)
            if left is None or right is None:
                if left is right:
                    return 0
                return (-1 if nulls_first else 1) if left is None else (1 if nulls_first else -1)
            order = (left > right) - (left < right)
            return -order if descending else order

        result.sort(key=cmp_to_key(compare))
    return result


class ScalarRows(list):
    def all(self):
        return list(self)

    def first(self):
        return self[0] if self else None

    def one_or_none(self):
        assert len(self) <= 1
        return self.first()


class Result:
    def __init__(self, rows):
        self.rows = ScalarRows(rows)

    def scalars(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows.one_or_none()


class RowsSession:
    """Rows have real model fields/UUIDs, but are never persisted by this fake."""

    no_autoflush = nullcontext()

    def __init__(self, tables):
        self.tables = {model: list(rows) for model, rows in tables.items()}
        self.info = {}
        self.new, self.dirty, self.deleted = set(), set(), set()

    async def scalars(self, statement):
        descriptions = statement.column_descriptions
        model = descriptions[0].get("entity") if descriptions else None
        rows = list(self.tables.get(model, ()))
        if rows:
            rows = [row for row in rows if all(_value(c, row) for c in statement._where_criteria)]
            rows = _ordered(rows, statement._order_by_clauses)
        offset = statement._offset_clause
        if offset is not None:
            rows = rows[offset.value:]
        limit = statement._limit_clause
        if limit is not None:
            rows = rows[:limit.value]
        return ScalarRows(rows)

    async def scalar(self, statement):
        rows = await self.scalars(statement)
        return rows.first()

    async def execute(self, statement):
        return Result(await self.scalars(statement))

    async def get(self, model, key, **kwargs):
        return next((row for row in self.tables.get(model, ()) if row.id == key), None)

    @asynccontextmanager
    async def begin_nested(self):
        yield self

    def add(self, row):
        raise AssertionError("F0 reader component must not add rows")

    async def flush(self, *args, **kwargs):
        raise AssertionError("F0 reader component must not flush")

    async def commit(self):
        raise AssertionError("F0 reader component must not commit")


def source_rows(case):
    from bestseller.infra.db import models as m

    p = case.project
    arcs = [m.PlotArcModel(id=uid(1100+i), project_id=p.id, arc_code=f'arc-{i}', name=f'arc-{i}',
                          arc_type='main', promise='A promise', core_question='A question', status='active', metadata_json={}) for i in range(1, 4)]
    beats = [m.ArcBeatModel(id=uid(1200+i), project_id=p.id, plot_arc_id=arcs[(i-1)%3].id,
                            beat_order=i, scope_level='chapter', scope_chapter_number=2,
                            beat_kind='turn', summary=f'beat-{i}', status='planned',
                            metadata_json={'structure_beat':f'structure-{i}'}) for i in range(1, 8)]
    events = [m.TimelineEventModel(id=uid(1300+i), project_id=p.id, chapter_id=case.previous.id,
                                   event_name=f'event-{i}', event_type='observation', story_time_label='earlier',
                                   story_order=1.1, consequences=['second', 'first'], metadata_json={})
              for i in range(1, 6)]
    clues = [m.ClueModel(id=uid(1400+i), project_id=p.id, clue_code=f'clue-{i}', label=f'clue-{i}',
                          description='ordinary clue', status='active', planted_in_chapter_number=1,
                          metadata_json={}) for i in range(1, 10)]
    payoffs = [m.PayoffModel(id=uid(1500+i), project_id=p.id, payoff_code=f'payoff-{i}', label=f'payoff-{i}',
                              description='ordinary payoff', target_chapter_number=2, metadata_json={})
               for i in range(1, 8)]
    return arcs, beats, events, clues, payoffs

