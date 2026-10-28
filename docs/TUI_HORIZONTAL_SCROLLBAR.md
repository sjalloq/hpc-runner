# Preventing Horizontal Scrollbars in Textual DataTables

This guide documents the technique used in `hpc monitor` to prevent horizontal
scrollbars from appearing in a `DataTable` widget. The approach is
**content-aware** rather than CSS-based—the table actively measures available
space and adjusts column widths to fit, rather than simply hiding overflow.

## The Problem

Textual's `DataTable` will display a horizontal scrollbar when the total width
of all columns exceeds the available widget width. This creates a poor UX:

- Users must scroll horizontally to see all data
- The table feels cramped and hard to navigate
- Terminal resizing can cause layout thrashing

## The Solution: Dynamic Column Width Management

The key insight is to designate **one flexible column** that absorbs width
variations while keeping all other columns at fixed widths.

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Fixed   │  FLEXIBLE (absorbs remaining space)  │  Fixed   │
│  10 ch   │  calculated dynamically              │  12 ch   │
└─────────────────────────────────────────────────────────────┘
           ▲
           │
    Recalculated on every resize event
```

## Implementation

### Step 1: Define Fixed and Flexible Columns

Identify which columns have predictable content widths and which should flex:

```python
class MyTable(DataTable[str]):
    # Fixed columns: (key, label, width)
    FIXED_COLUMNS = [
        ("id", "ID", 10),
        ("status", "Status", 10),
        ("date", "Date", 12),
    ]

    # The flexible column gets remaining space
    FLEX_COL_MIN = 15  # Minimum width for the flexible column

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._flex_col_width = 20  # Default, recalculated on mount/resize
```

### Step 2: Calculate Flexible Column Width

The flexible column width is: `available_width - fixed_columns - spacing`

```python
def _get_table_width(self) -> int:
    """Return the width available for columns within the table."""
    content_size = getattr(self, "content_size", None)
    if content_size is not None:
        return int(content_size.width)
    return self.size.width or self.app.console.size.width

def _calculate_flex_width(self, table_width: int) -> int:
    """Calculate the flexible column width to avoid horizontal overflow."""
    fixed_total = sum(w for _, _, w in self.FIXED_COLUMNS)
    # Account for spacing between columns (2 chars per column boundary)
    column_spacing = (len(self.FIXED_COLUMNS) + 1) * 2
    flex_width = table_width - fixed_total - column_spacing
    return max(self.FLEX_COL_MIN, flex_width)
```

### Step 3: Sync Columns on Mount and Resize

Hook into mount and resize events to recalculate:

```python
def on_mount(self) -> None:
    """Set up columns on mount using the best available width."""
    self._setup_columns()
    # Defer width sync until after initial layout
    self.call_after_refresh(self._sync_columns_to_current_width)

def on_resize(self, event: Resize) -> None:
    """Handle resize events by syncing column widths to the new size."""
    del event  # Unused, we read current size directly
    self.call_after_refresh(self._sync_columns_to_current_width)

def _sync_columns_to_current_width(self) -> None:
    """Sync the flexible column width after layout updates size information."""
    table_width = self._get_table_width()
    if table_width <= 0:
        return

    desired_width = self._calculate_flex_width(table_width)
    if desired_width != self._flex_col_width:
        self._set_flex_column_width(desired_width)

    # Schedule post-layout adjustment to catch any remaining overflow
    self.call_after_refresh(self._post_layout_adjust, table_width)
```

### Step 4: Post-Layout Overflow Detection

After layout completes, check if there's still overflow and trim further:

```python
def _post_layout_adjust(self, table_width: int) -> None:
    """Trim flex width if virtual width still overflows after layout."""
    if table_width <= 0:
        return

    # Account for vertical scrollbar if present
    scrollbar_width = 1 if self.show_vertical_scrollbar else 0
    effective_width = table_width - scrollbar_width

    # Check for overflow
    overflow = self.virtual_size.width - effective_width
    if overflow <= 0 or self._flex_col_width <= self.FLEX_COL_MIN:
        return

    # Reduce flex column width to eliminate overflow
    adjusted_width = max(self.FLEX_COL_MIN, self._flex_col_width - overflow)
    if adjusted_width != self._flex_col_width:
        self._set_flex_column_width(adjusted_width)
```

### Step 5: Apply Width and Refresh Content

When the width changes, update the column and refresh rows:

```python
def _set_flex_column_width(self, width: int) -> None:
    """Apply flexible column width and refresh rows for correct truncation."""
    self._flex_col_width = width

    # Update the column width in the DataTable
    flex_column = self.columns.get("name")  # Use your flex column key
    if flex_column is not None:
        flex_column.width = width

    # Refresh rows to apply truncation
    if self._data:
        self._refresh_rows()
```

### Step 6: Truncate Content to Fit

Truncate text in the flexible column to prevent overflow:

```python
def _truncate_text(self, text: str) -> str:
    """Truncate text to fit in the flexible column."""
    if len(text) <= self._flex_col_width:
        return text
    # Truncate and add ellipsis
    return text[: self._flex_col_width - 1] + "…"
```

Use this when adding rows:

```python
def add_item(self, item: MyItem) -> None:
    self.add_row(
        item.id,
        self._truncate_text(item.name),  # Flexible column
        item.status,
        item.date,
        key=item.id,
    )
```

## Complete Example

Here's a minimal complete implementation:

```python
from textual.events import Resize
from textual.widgets import DataTable


class ResponsiveTable(DataTable[str]):
    """A DataTable that prevents horizontal scrollbars."""

    FIXED_COLUMNS = [
        ("id", "ID", 8),
        ("status", "Status", 10),
    ]
    FLEX_COL_MIN = 15

    def __init__(self, **kwargs) -> None:
        super().__init__(cursor_type="row", **kwargs)
        self._flex_col_width = 20
        self._items: dict[str, dict] = {}

    def on_mount(self) -> None:
        self._setup_columns()
        self.call_after_refresh(self._sync_columns_to_current_width)

    def on_resize(self, event: Resize) -> None:
        del event
        self.call_after_refresh(self._sync_columns_to_current_width)

    def _setup_columns(self) -> None:
        self.add_column("ID", key="id", width=8)
        self.add_column("Name", key="name", width=self._flex_col_width)
        self.add_column("Status", key="status", width=10)

    def _get_table_width(self) -> int:
        content_size = getattr(self, "content_size", None)
        if content_size is not None:
            return int(content_size.width)
        return self.size.width or self.app.console.size.width

    def _calculate_flex_width(self, table_width: int) -> int:
        fixed_total = sum(w for _, _, w in self.FIXED_COLUMNS)
        column_spacing = (len(self.FIXED_COLUMNS) + 1) * 2
        flex_width = table_width - fixed_total - column_spacing
        return max(self.FLEX_COL_MIN, flex_width)

    def _sync_columns_to_current_width(self) -> None:
        table_width = self._get_table_width()
        if table_width <= 0:
            return
        desired_width = self._calculate_flex_width(table_width)
        if desired_width != self._flex_col_width:
            self._set_flex_column_width(desired_width)
        self.call_after_refresh(self._post_layout_adjust, table_width)

    def _post_layout_adjust(self, table_width: int) -> None:
        if table_width <= 0:
            return
        scrollbar_width = 1 if self.show_vertical_scrollbar else 0
        effective_width = table_width - scrollbar_width
        overflow = self.virtual_size.width - effective_width
        if overflow <= 0 or self._flex_col_width <= self.FLEX_COL_MIN:
            return
        adjusted_width = max(self.FLEX_COL_MIN, self._flex_col_width - overflow)
        if adjusted_width != self._flex_col_width:
            self._set_flex_column_width(adjusted_width)

    def _set_flex_column_width(self, width: int) -> None:
        self._flex_col_width = width
        flex_column = self.columns.get("name")
        if flex_column is not None:
            flex_column.width = width
        if self._items:
            self._refresh_rows()

    def _refresh_rows(self) -> None:
        """Rebuild rows with current truncation width."""
        items = list(self._items.values())
        self.clear()
        for item in items:
            self._add_item_row(item)

    def _truncate_text(self, text: str) -> str:
        if len(text) <= self._flex_col_width:
            return text
        return text[: self._flex_col_width - 1] + "…"

    def _add_item_row(self, item: dict) -> None:
        self._items[item["id"]] = item
        self.add_row(
            item["id"],
            self._truncate_text(item["name"]),
            item["status"],
            key=item["id"],
        )

    def add_item(self, item: dict) -> None:
        self._add_item_row(item)
```

## Key Points

1. **Use `call_after_refresh()`**: Width calculations must happen after Textual
   has completed layout. Using `call_after_refresh()` defers execution until
   the next refresh cycle.

2. **Two-phase adjustment**: The initial calculation may not account for all
   factors (like vertical scrollbar appearance). The `_post_layout_adjust()`
   method catches remaining overflow after the first layout pass.

3. **Minimum width enforcement**: Always enforce a minimum width for the
   flexible column to prevent it from becoming unusably small.

4. **Content truncation**: Truncating text with an ellipsis (`…`) provides
   visual feedback that content was shortened.

5. **Preserve row order**: When refreshing rows after a width change, maintain
   the original order and selection state.

## CSS Considerations

While the primary prevention is Python-based, CSS can provide additional safety:

```css
/* In your .tcss file */
MyTable {
    /* Standard table styling */
    background: transparent;
    border: round $border-blurred;
}

/* For container widgets that should never overflow */
StatusLine {
    overflow: hidden;
    width: 100%;
}
```

## Debugging Tips

If horizontal scrollbars still appear:

1. **Check virtual_size**: Log `self.virtual_size.width` vs `table_width` to
   see the overflow amount

2. **Verify column spacing**: The `column_spacing` calculation assumes 2 chars
   per column boundary—adjust if your theme differs

3. **Account for borders/padding**: If your table has borders or padding,
   subtract those from the available width

4. **Check for off-by-one**: Scrollbar width detection may vary; try adjusting
   the scrollbar_width value

## Reference Implementation

See `src/hpc_runner/tui/components/job_table.py` for the full production
implementation used in `hpc monitor`.
