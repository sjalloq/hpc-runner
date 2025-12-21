# Textual TUI Styling Cookbook

A guide to achieving a polished, modern terminal UI aesthetic based on the Rovr file explorer's design patterns.

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [CSS Variables System](#css-variables-system)
3. [Border Styling Patterns](#border-styling-patterns)
4. [Focus States & Visual Feedback](#focus-states--visual-feedback)
5. [Layout Patterns](#layout-patterns)
6. [Component Recipes](#component-recipes)
7. [Modal Dialogs](#modal-dialogs)
8. [Toast Notifications](#toast-notifications)
9. [Theming System](#theming-system)
10. [Responsive Design](#responsive-design)

---

## Design Philosophy

The aesthetic follows these core principles:

- **Transparent backgrounds** - Components use `background: transparent` to blend with the terminal
- **Rounded borders** - Consistent use of `round` border style for a softer look
- **Focus-aware styling** - Clear visual distinction between focused and unfocused states
- **Minimal chrome** - Reduce visual noise, let content breathe
- **Color as meaning** - Use semantic colors (primary, error, success, warning) consistently

---

## CSS Variables System

Define reusable values at the top of your TCSS file:

```tcss
/* Border Variables */
$border-style: round;
$border-blurred: $primary-background-lighten-3;
$border: $primary-lighten-3;
$border-disabled: $panel;

/* Layout Dimensions */
$sidebar_width: 17;
$main_width: 1.25fr;
$preview_width: 0.75fr;
$footer_height: 7;
$footer_focus_height: 9;

/* Scrollbar Colors */
$scrollbar: $primary;
$scrollbar-hover: $primary-lighten-3;
$scrollbar-active: $primary-lighten-3;
$scrollbar-background: $primary-muted;
```

### Key Insight: Color Modifiers

Textual provides automatic color modifiers you can chain onto theme colors:
- `-lighten-1`, `-lighten-2`, `-lighten-3` - Lighter variants
- `-darken-1`, `-darken-2`, `-darken-3` - Darker variants
- `-muted` - Reduced saturation

```tcss
/* Example usage */
.focused { border-color: $primary-lighten-3 }
.disabled { color: $panel-lighten-3 }
.subtle { background: $primary-muted }
```

---

## Border Styling Patterns

### The Dual-State Border Pattern

This is the signature look: borders that change color based on focus state.

```tcss
/* Base state - subtle, blurred border */
#my_panel {
  background: transparent;
  border: $border-style $border-blurred;
  border-subtitle-color: $background;
  border-subtitle-background: $border-blurred;
}

/* Focused state - vibrant border */
#my_panel:focus-within {
  border: $border-style $border;
  border-subtitle-background: $border;
}
```

### Border Titles

Use border titles to label panels without taking up content space:

```python
# In your widget's on_mount or compose
def on_mount(self) -> None:
    self.query_one("#my_panel").border_title = "My Panel"
    self.query_one("#my_panel").border_subtitle = "Status info"
```

```tcss
#my_panel {
  border-title-align: center;
  border-subtitle-align: right;
}
```

### ANSI/Light Theme Support

Always provide fallbacks for different terminal capabilities:

```tcss
#my_panel {
  border: $border-style $border-blurred;
  border-subtitle-background: $border-blurred;

  /* ANSI terminals can't do background colors well */
  &:ansi {
    border-subtitle-background: transparent;
    border-subtitle-color: $border-blurred;
  }

  /* Light themes need inverted colors */
  &:light {
    border: $border-style $border-blurred-light;
    border-subtitle-background: $border-blurred-light;
  }
}
```

---

## Focus States & Visual Feedback

### The Transparency Reset Pattern

Prevent Textual's default dimming of unfocused widgets:

```tcss
.my-widget {
  opacity: 1 !important;
  background-tint: transparent !important;
  text-opacity: 1 !important;
  tint: transparent !important;
}
```

### Selection List Styling

Create clear visual hierarchy for list selections:

```tcss
MyList {
  padding: 0;
  background-tint: ansi_default !important;

  /* Unhighlighted option - subtle */
  .option-list--option-highlighted {
    color: $foreground;
    background: transparent;
    text-style: none;
  }

  /* Hover state */
  .option-list--option-hover {
    color: $primary;
    background: transparent;
  }

  /* Focused + highlighted - prominent */
  &:focus .option-list--option-highlighted {
    color: $block-cursor-foreground;
    background: $primary;
    text-style: none;
  }

  /* Selection checkboxes */
  .selection-list--button {
    background: transparent;
    color: $primary;
  }

  .selection-list--button-selected-highlighted {
    background: $primary;
    color: $background;
  }
}
```

### Disabled States

```tcss
.my-button {
  &:disabled {
    border: $border-style $border-disabled;
    opacity: 1 !important;
    background-tint: transparent !important;
    color: $panel-lighten-3;
  }

  &:light:disabled {
    color: $panel-darken-3;
  }
}
```

---

## Layout Patterns

### The Three-Panel Layout

A classic sidebar + main + preview layout:

```python
def compose(self) -> ComposeResult:
    with HorizontalGroup(id="main"):
        with VerticalGroup(id="sidebar"):
            yield SearchInput(placeholder="Search")
            yield MySidebar(id="sidebar_list")
        yield MainContent(id="content")
        yield PreviewPanel(id="preview")
```

```tcss
#main {
  height: 1fr;
  align: center middle;
}

#sidebar {
  height: 1fr;
  width: 17;  /* Fixed width sidebar */
}

#content {
  height: 1fr;
  width: 1.25fr;  /* Flexible, takes more space */
}

#preview {
  height: 1fr;
  width: 0.75fr;  /* Flexible, takes less space */
}
```

### The Header + Main + Footer Pattern

```python
def compose(self) -> ComposeResult:
    with Vertical(id="root"):
        yield HeaderArea(id="header")
        with VerticalGroup(id="toolbar"):
            with HorizontalScroll(id="menu"):
                yield Button("Copy")
                yield Button("Paste")
            with VerticalGroup(id="nav"):
                yield PathInput()
        with HorizontalGroup(id="main"):
            # ... panels
        with HorizontalGroup(id="footer"):
            yield ProcessContainer()
            yield MetadataContainer()
```

### Footer Height Animation

Footer grows when focused for better interaction:

```tcss
#footer {
  height: 7;

  & > * {
    height: 1fr;
    background: transparent;
  }

  &:focus-within {
    height: 9;
    max-height: 40vh;
  }
}
```

---

## Component Recipes

### Custom Scrollbars

Minimal, themed scrollbars:

```tcss
* {
  scrollbar-size: 1 1;
  scrollbar-color: $primary;
  scrollbar-background: $primary-muted;
  scrollbar-color-hover: $primary-lighten-3;
  scrollbar-color-active: $primary-lighten-3;
}

/* Hide scrollbars on specific widgets */
Input {
  scrollbar-size: 0 0;
  scrollbar-visibility: hidden;
  overflow: hidden hidden;
}
```

### Input Fields

Clean, borderless inputs:

```tcss
#my_container Input {
  padding: 0 0 0 1;
  margin: 0;
  height: 1;
  border: none;
  background: transparent;
  color: $foreground-lighten-1;
}

Input {
  .input--placeholder {
    color: $foreground-darken-1;
  }

  &:ansi {
    .input--cursor {
      color: $primary;
    }
    .input--selection {
      background: $secondary-darken-3;
      text-style: bold;
    }
  }
}
```

### Progress Bars

Status-aware progress bars:

```tcss
ProgressBarContainer {
  padding-right: 1;

  ProgressBar { width: 1fr }

  /* Error state */
  &.error .bar--bar,
  &.error .bar--complete {
    color: $error;
  }

  /* Success/done state */
  &.done .bar--complete {
    color: $success;
  }

  /* Indeterminate/loading */
  .bar--indeterminate {
    color: $accent;
  }

  /* In progress */
  .bar--bar {
    color: $warning;
  }
}
```

### Custom Tab Underline

```python
from textual.renderables.bar import Bar as BarRenderable

class BetterBarRenderable(BarRenderable):
    """Custom tab underline with different characters."""
    HALF_BAR_LEFT: str = "╶"
    BAR: str = "─"
    HALF_BAR_RIGHT: str = "╴"


class BetterUnderline(Underline):
    def render(self) -> RenderResult:
        bar_style = self.get_component_rich_style("underline--bar")
        return BetterBarRenderable(
            highlight_range=self._highlight_range,
            highlight_style=Style.from_color(bar_style.color),
            background_style=Style.from_color(bar_style.bgcolor),
        )
```

```tcss
Tabline {
  .underline--bar {
    color: $primary;
    background: $background-lighten-3;
  }

  &:ansi .underline--bar {
    background: $background-lighten-3;
  }
}
```

### Tab Styling

```tcss
TablineTab {
  color: auto;
  opacity: 1 !important;

  &:hover {
    background: $boost-lighten-3;
    color: $foreground;
    &:ansi { background: transparent }
  }

  &.-active {
    background: $primary;
    color: $background;

    &:hover {
      background: $primary;
      color: $background;
    }
  }
}
```

---

## Modal Dialogs

### Basic Modal Structure

```python
class YesOrNo(ModalScreen):
    def compose(self) -> ComposeResult:
        with Grid(id="dialog"):
            with VerticalGroup(id="question_container"):
                yield Label(self.message, classes="question")
            yield Button("Yes", variant="primary", id="yes")
            yield Button("No", variant="error", id="no")

    def on_mount(self) -> None:
        self.query_one("#dialog").border_title = "Confirm"
```

### Modal TCSS Pattern

```tcss
YesOrNo {
  align: center middle;

  #dialog {
    grid-size: 2;
    grid-gutter: 1 2;
    padding: 1 3;
    border: $border-style $border;
    grid-rows: 1fr 3;
    max-width: 57;
    max-height: 15;
    height: 75vh;
    width: 75vw;

    #question_container {
      column-span: 2;
      height: 1fr;
      width: 1fr;
      content-align: center middle;

      .question {
        text-align: center;
        width: 1fr;
      }
    }

    Button { width: 100% }
  }
}
```

### Button Variants in ANSI Mode

```tcss
ModalScreen Button:ansi {
  background: transparent;
  border: $border-style $surface-darken-1;

  &.-active {
    border: $border-style $surface-lighten-1;
    tint: transparent;
  }

  &.-primary {
    border: $border-style $primary-lighten-3;
    color: white;
    &:hover { border: $border-style $primary }
    &.-active { border: $border-style $primary-darken-3 }
  }

  &.-error {
    border: $border-style $error-lighten-2;
    color: white;
    &:hover { border: $border-style $error }
    &.-active { border: $border-style $error-darken-2 }
  }
}
```

### Input Modal

```tcss
ModalInput {
  align: center middle;

  HorizontalGroup {
    border: $border-style $border;
    width: 50vw;
    max-height: 3;
    background: transparent !important;
    border-title-align: left;
    border-subtitle-align: right;

    &.invalid {
      border: $border-style $error-lighten-3;
    }
  }

  Input {
    background: transparent !important;
    overflow-x: hidden;
    width: 1fr;
  }
}
```

---

## Toast Notifications

```tcss
Toast {
  max-height: 100%;
  width: 33vw;
  max-width: 100vw;
  layer: toastLayer;

  .toast--title {
    text-style: underline;
  }

  /* Severity indicators via border */
  &.-information { border-right: outer $success }
  &.-warning { border-right: outer $warning }
  &.-error { border-right: outer $error }

  /* ANSI fallback */
  &:ansi {
    padding: 0;
    background: transparent;
    padding-left: 1;

    &.-information { border: $border-style $success }
    &.-warning { border: $border-style $warning }
    &.-error { border: $border-style $error }
  }
}
```

---

## Theming System

### Custom Theme Class

```python
from dataclasses import dataclass, field
from textual.theme import Theme


@dataclass
class MyThemeClass(Theme):
    name: str
    primary: str
    secondary: str | None = None
    warning: str | None = None
    error: str | None = None
    success: str | None = None
    accent: str | None = None
    foreground: str | None = None
    background: str | None = None
    surface: str | None = None
    panel: str | None = None
    boost: str | None = None
    dark: bool = True
    luminosity_spread: float = 0.15
    text_alpha: float = 0.95
    variables: dict[str, str] = field(default_factory=dict)
    bar_gradient: list[str] | None = None  # Custom field
```

### App Setup

```python
class Application(App):
    CSS_PATH = ["style.tcss"]

    def __init__(self) -> None:
        super().__init__(watch_css=True)  # Hot reload CSS during development

    def on_mount(self) -> None:
        # Register custom themes
        for theme in get_custom_themes():
            self.register_theme(theme)

        self.theme = "my_theme"
        self.ansi_color = False  # Set True for transparent mode
```

### Transparent/ANSI Mode Toggle

```python
async def toggle_transparency(self) -> None:
    self.ansi_color = not self.ansi_color
```

---

## Responsive Design

### Breakpoint System

```python
class Application(App):
    # Width breakpoints
    HORIZONTAL_BREAKPOINTS = [
        (0, "-filelistonly"),   # Very narrow: only main content
        (35, "-nopreview"),     # Medium: sidebar + content
        (70, "-all-horizontal") # Wide: all three panels
    ]

    # Height breakpoints
    VERTICAL_BREAKPOINTS = [
        (0, "-middle-only"),    # Very short
        (16, "-nomenu-atall"),  # Short
        (19, "-nopath"),        # Medium
        (24, "-all-vertical")   # Tall: show everything
    ]
```

### Breakpoint CSS

```tcss
/* Hide panels at narrow widths */
Screen.-filelistonly #sidebar,
Screen.-filelistonly #preview,
Screen.-nopreview #preview {
  display: none !important;
}

/* Adjust dialog widths */
Screen.-filelistonly #dialog {
  width: 90vw;
  max-width: 90vw;
}

Screen.-nopreview #dialog {
  width: 75vw;
  max-width: 75vw;
}

/* Adjust footer at short heights */
Screen.-all-vertical #footer { max-height: 25vh }
Screen.-nopath #footer { max-height: 30vh }
Screen.-middle-only #footer { max-height: 25vh }
```

### Compact Mode Classes

Allow users to toggle between compact and comfortable layouts:

```python
def on_mount(self) -> None:
    if config["compact_mode"]["buttons"]:
        self.add_class("compact-buttons")
    else:
        self.add_class("comfy-buttons")
```

```tcss
.compact-buttons .my-button {
  width: 3;
  height: 1;
}

.comfy-buttons .my-button {
  width: 7;
  height: 3;
}
```

---

## Tips & Tricks

### 1. Use Layers for Overlays

```tcss
MyPopup {
  layer: overlay;
}

Toast {
  layer: toastLayer;
}
```

### 2. Hide/Show Pattern

```tcss
.hide, .hidden { display: none }
```

```python
self.query_one("#panel").add_class("hidden")
self.query_one("#panel").remove_class("hidden")
```

### 3. Prevent Text Wrapping in Lists

```tcss
OptionList {
  text-wrap: nowrap;
  text-overflow: ellipsis;
}
```

### 4. Center Content in Dialogs

```tcss
#question_container {
  content-align: center middle;

  .question {
    text-align: center;
    width: 1fr;
  }
}
```

### 5. Stable Scrollbar Gutters

Prevent layout shift when scrollbars appear:

```tcss
#my_scrollable {
  scrollbar-gutter: stable;
}
```

### 6. Border Subtitle for Status

Use border subtitles to show dynamic status without taking content space:

```python
def update_status(self, count: int) -> None:
    self.border_subtitle = f"{count} items"
```

### 7. Multiple CSS Files

Layer user customization over defaults:

```python
class Application(App):
    CSS_PATH = [
        "style.tcss",                    # Your defaults
        path.join(CONFIG_DIR, "style.tcss")  # User overrides
    ]
```

---

## Quick Reference

| Pattern | Use Case |
|---------|----------|
| `$border-style: round` | Soft, modern borders |
| `background: transparent` | Blend with terminal |
| `:focus-within` | Style container when child focused |
| `&:ansi` | ANSI terminal fallback |
| `&:light` | Light theme variant |
| `opacity: 1 !important` | Prevent default dimming |
| `grid-rows: 1fr 3` | Flexible + fixed grid rows |
| `layer: overlay` | Popup/modal layers |
| `scrollbar-gutter: stable` | Prevent layout shift |

---

## Minimal Starter Template

```tcss
/* Variables */
$border-style: round;
$border-blurred: $primary-background-lighten-3;
$border: $primary-lighten-3;

/* Reset default dimming */
* {
  scrollbar-size: 1 1;
}

/* Base panel styling */
.panel {
  background: transparent;
  border: $border-style $border-blurred;
  border-subtitle-background: $border-blurred;
  opacity: 1 !important;
  background-tint: transparent !important;
}

.panel:focus-within {
  border: $border-style $border;
  border-subtitle-background: $border;
}

/* Hide utility */
.hidden { display: none }
```

---

This cookbook covers the core patterns. The key to the aesthetic is consistency: use the same border style everywhere, make focus states obvious, and let transparency work with your terminal's background.
