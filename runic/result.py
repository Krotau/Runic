from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import Field, dataclass, fields, is_dataclass
from typing import Any, ClassVar, Generic, Literal, Protocol, TypeAlias, TypeVar, cast

T = TypeVar("T", covariant=True)
E = TypeVar("E", covariant=True)

_MISMATCH = object()
_SEQUENCE_LEAF_TYPES = (str, bytes, bytearray)


class _SupportsDataclassFields(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]


def _is_dataclass_instance(value: object) -> bool:
    return is_dataclass(value) and not isinstance(value, type)


def _as_dataclass_instance(value: object) -> _SupportsDataclassFields:
    return cast(_SupportsDataclassFields, value)


def _is_comparable_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, _SEQUENCE_LEAF_TYPES)


def _compare_scalars(left: object, right: object, operator: str) -> bool:
    match operator:
        case "<":
            return cast(Any, left) < cast(Any, right)
        case "<=":
            return cast(Any, left) <= cast(Any, right)
        case ">":
            return cast(Any, left) > cast(Any, right)
        case ">=":
            return cast(Any, left) >= cast(Any, right)
        case _:
            raise ValueError(f"Unsupported comparison operator: {operator}")


def _compare_dataclasses(left: object, right: object) -> bool:
    if not (_is_dataclass_instance(left) and _is_dataclass_instance(right)):
        return False
    if type(left) is not type(right):
        return False

    left_dataclass = _as_dataclass_instance(left)
    right_dataclass = _as_dataclass_instance(right)
    return all(
        _deep_compare(getattr(left_dataclass, field.name), getattr(right_dataclass, field.name))
        for field in fields(left_dataclass)
    )


def _compare_mappings(left: Mapping[Any, Any], right: Mapping[Any, Any], *, deep: bool) -> bool:
    if left.keys() != right.keys():
        return False

    comparator = _deep_compare if deep else _shallow_equal
    return all(comparator(left[key], right[key]) for key in left)


def _compare_sequences(left: Sequence[Any], right: Sequence[Any], *, deep: bool) -> bool:
    if len(left) != len(right):
        return False

    comparator = _deep_compare if deep else _shallow_equal
    return all(comparator(left_item, right_item) for left_item, right_item in zip(left, right, strict=True))


def _deep_compare(left: object, right: object) -> bool:
    match (left, right):
        case (_ResultBase() as result, _):
            return result.compare(right)
        case (_, _ResultBase() as result):
            return result.compare(left)
        case _ if _is_dataclass_instance(left) and _is_dataclass_instance(right):
            return _compare_dataclasses(left, right)
        case (Mapping() as left_mapping, Mapping() as right_mapping):
            return _compare_mappings(left_mapping, right_mapping, deep=True)
        case _ if _is_comparable_sequence(left) and _is_comparable_sequence(right):
            return _compare_sequences(cast(Sequence[Any], left), cast(Sequence[Any], right), deep=True)
        case _:
            return left == right


def _shallow_equal(left: object, right: object) -> bool:
    match (left, right):
        case (_ResultBase() as left_result, _ResultBase() as right_result):
            if type(left_result) is not type(right_result):
                return False
            return left_result._payload() == right_result._payload()
        case (_ResultBase(), _) | (_, _ResultBase()):
            return False
        case (Mapping() as left_mapping, Mapping() as right_mapping):
            return _compare_mappings(left_mapping, right_mapping, deep=False)
        case _ if _is_comparable_sequence(left) and _is_comparable_sequence(right):
            return _compare_sequences(cast(Sequence[Any], left), cast(Sequence[Any], right), deep=False)
        case _:
            return left == right


class _ResultBase:
    _payload_name: ClassVar[str]

    def _payload(self) -> object:
        return getattr(self, self._payload_name)

    def _coerce_other(self, other: object) -> object:
        match other:
            case _ResultBase() as other_result if type(self) is not type(other_result):
                return _MISMATCH
            case _ResultBase() as other_result:
                return other_result._payload()
            case _:
                return other

    def compare(self, other: object) -> bool:
        """Recursively compare nested `Result` wrappers and container contents."""

        match other:
            case _ResultBase() as other_result if type(self) is not type(other_result):
                return False
            case _ResultBase() as other_result:
                return _deep_compare(self._payload(), other_result._payload())
            case _:
                return _deep_compare(self._payload(), other)

    def __bool__(self) -> bool:
        # `and` / `or` are not overloadable in Python. Their behavior follows
        # the wrapped payload truthiness through `__bool__`.
        return bool(self._payload())

    def __eq__(self, other: object) -> bool:
        other_payload = self._coerce_other(other)
        if other_payload is _MISMATCH:
            return False
        # Rich comparisons are shallow by design. Nested `Result` wrappers stay
        # opaque here; use `.compare()` when recursive comparison is wanted.
        return _shallow_equal(self._payload(), other_payload)

    def __lt__(self, other: object) -> bool:
        other_payload = self._coerce_other(other)
        if other_payload is _MISMATCH:
            return NotImplemented
        # Ordering is also shallow by design. Only the immediate wrapped value
        # participates in the comparison.
        return _compare_scalars(self._payload(), other_payload, "<")

    def __le__(self, other: object) -> bool:
        other_payload = self._coerce_other(other)
        if other_payload is _MISMATCH:
            return NotImplemented
        # Ordering is also shallow by design. Only the immediate wrapped value
        # participates in the comparison.
        return _compare_scalars(self._payload(), other_payload, "<=")

    def __gt__(self, other: object) -> bool:
        other_payload = self._coerce_other(other)
        if other_payload is _MISMATCH:
            return NotImplemented
        # Ordering is also shallow by design. Only the immediate wrapped value
        # participates in the comparison.
        return _compare_scalars(self._payload(), other_payload, ">")

    def __ge__(self, other: object) -> bool:
        other_payload = self._coerce_other(other)
        if other_payload is _MISMATCH:
            return NotImplemented
        # Ordering is also shallow by design. Only the immediate wrapped value
        # participates in the comparison.
        return _compare_scalars(self._payload(), other_payload, ">=")


@dataclass(slots=True, eq=False)
class Ok(_ResultBase, Generic[T]):
    """Represents a successful result payload."""

    _payload_name: ClassVar[str] = "value"

    value: T
    ok: Literal[True] = True


@dataclass(slots=True, eq=False)
class Err(_ResultBase, Generic[E]):
    """Represents a failed result payload."""

    _payload_name: ClassVar[str] = "error"

    error: E
    ok: Literal[False] = False


Result: TypeAlias = Ok[T] | Err[E]
