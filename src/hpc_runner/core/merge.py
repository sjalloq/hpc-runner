"""The list merge rule (D1), implemented once.

Every list-valued job setting (``modules``, ``modules_path``,
``modules_path_append``, ``env_unset``, ``resources`` and each scheduler's
``scheduler_args`` list) merges across layers the same way, whether the
layer is a config section, a later config file or the ``JobSpec``
(``scheduler_args`` lists are argv and skip the dedupe):

- a plain list appends to what the earlier layers built and dedupes,
  first-seen order kept;
- :class:`Set` replaces the accumulated list outright;
- :class:`Edit` removes items, then appends ``add`` with the same dedupe.

In TOML the verbs are tables: ``modules = { set = [...] }`` and
``modules = { remove = [...], add = [...] }`` (either key optional).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

KeyFunc = Callable[[Any], Any]


@dataclass(frozen=True, init=False)
class Set:
    """Replace the accumulated list with *items* (TOML: ``{ set = [...] }``)."""

    items: tuple[Any, ...]

    def __init__(self, items: Iterable[Any] = ()) -> None:
        object.__setattr__(self, "items", tuple(_check_iterable(items)))


@dataclass(frozen=True, init=False)
class Edit:
    """Remove *remove*, then append *add* (TOML: ``{ remove = [...], add = [...] }``).

    Items are removed by value, or by key for keyed lists (``resources``
    are matched by ``name``).
    """

    remove: tuple[Any, ...]
    add: tuple[Any, ...]

    def __init__(self, remove: Iterable[Any] = (), add: Iterable[Any] = ()) -> None:
        object.__setattr__(self, "remove", tuple(_check_iterable(remove)))
        object.__setattr__(self, "add", tuple(_check_iterable(add)))


#: A layer's value for a list setting: append, replace, or edit.
ListValue = Sequence[Any] | Set | Edit


def _check_iterable(items: Iterable[Any]) -> Iterable[Any]:
    if isinstance(items, str):
        raise TypeError(f"expected a list, got the string {items!r}")
    return items


def _identity(item: Any) -> Any:
    return item


def resource_key(item: Any) -> Any:
    """Dedupe key for ``resources`` entries: the resource name."""
    return item["name"]


def key_for(field: str) -> KeyFunc | None:
    """The dedupe key for list setting *field* (None = by value)."""
    return resource_key if field == "resources" else None


def _append(
    acc: list[Any], items: Iterable[Any], key: KeyFunc | None, dedupe: bool = True
) -> list[Any]:
    """Append *items* to *acc*, deduping unless *dedupe* is false.

    Without *key*, an item equal to one already present is dropped (first
    seen wins). With *key*, an item whose key is already present replaces
    that entry in place (later wins the value, first position kept).
    """
    if not dedupe:
        return [*acc, *items]
    k = key or _identity
    result = list(acc)
    for item in items:
        for i, existing in enumerate(result):
            if k(existing) == k(item):
                if key is not None:
                    result[i] = item
                break
        else:
            result.append(item)
    return result


def merge_list(
    acc: Sequence[Any],
    layer_value: Any,
    *,
    key: KeyFunc | None = None,
    dedupe: bool = True,
) -> list[Any]:
    """Apply one layer's *layer_value* to the accumulated list *acc*.

    Args:
        acc: The list built by the earlier layers.
        layer_value: A plain list (append), :class:`Set` or :class:`Edit`.
        key: Dedupe key for lists of named tables; None dedupes by value.
        dedupe: False for argv-like lists (``scheduler_args``), where a
            repeated flag such as ``-l`` is meaningful. ``Edit.remove``
            then drops every occurrence.

    Raises:
        TypeError: *layer_value* is a string (it would iterate as characters).
    """
    if isinstance(layer_value, Set):
        return _append([], layer_value.items, key, dedupe)
    if isinstance(layer_value, Edit):
        k = key or _identity
        removed = [k(item) for item in layer_value.remove]
        kept = [item for item in acc if k(item) not in removed]
        return _append(kept, layer_value.add, key, dedupe)
    return _append(list(acc), _check_iterable(layer_value), key, dedupe)


def compose(earlier: Any, later: Any, *, key: KeyFunc | None = None, dedupe: bool = True) -> Any:
    """Combine two layer values into one with the same effect as applying both.

    Used when two config files set the same list in the same section: the
    result is applied to the earlier sections exactly as the two layers
    would have been, one after the other.
    """
    if isinstance(later, Set):
        return later
    if isinstance(earlier, Set):
        return Set(merge_list(earlier.items, later, key=key, dedupe=dedupe))
    k = key or _identity
    if isinstance(earlier, Edit):
        if isinstance(later, Edit):
            dropped = [k(item) for item in later.remove]
            add = [item for item in earlier.add if k(item) not in dropped]
            return Edit(
                remove=_append(list(earlier.remove), later.remove, key),
                add=_append(add, later.add, key, dedupe),
            )
        return Edit(
            remove=earlier.remove, add=merge_list(earlier.add, later, key=key, dedupe=dedupe)
        )
    # earlier is a plain list (append).
    if isinstance(later, Edit):
        dropped = [k(item) for item in later.remove]
        add = [item for item in earlier if k(item) not in dropped]
        return Edit(remove=later.remove, add=_append(add, later.add, key, dedupe))
    return merge_list(earlier, later, key=key, dedupe=dedupe)


def is_list_value(value: Any) -> bool:
    """True for a value :func:`merge_list` accepts as a layer value."""
    return isinstance(value, Set | Edit | list | tuple)
