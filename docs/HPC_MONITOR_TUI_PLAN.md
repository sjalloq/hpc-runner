# HPC Monitor TUI Implementation Plan

A Textual-based terminal UI for monitoring HPC jobs across schedulers.

## Overview

| Aspect | Decision |
|--------|----------|
| Entry point | `hpc monitor` |
| Framework | Textual |
| Styling | Based on `TEXTUAL_STYLING_COOKBOOK.md` |
| Tabs | Active, Completed |
| User scope | `$USER` default, toggle for all users |
| Actions | Cancel (Active tab only), View logs |
| Local storage | None — relies entirely on scheduler APIs |

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      TUI App                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │
│  │ Active Tab  │  │ Completed Tab │  │ Log Viewer Modal│   │
│  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘   │
│         │                │                  │            │
│         ▼                ▼                  ▼            │
│  ┌─────────────────────────────────────────────────────┐ │
│  │              JobProvider (async)                    │ │
│  │  - list_active_jobs()                               │ │
│  │  - list_completed_jobs()                            │ │
│  │  - get_job_details()                                │ │
│  │  - read_log_file()                                  │ │
│  └──────────────────────┬──────────────────────────────┘ │
└─────────────────────────┼────────────────────────────────┘
                          ▼
              ┌───────────────────────┐
              │   BaseScheduler       │
              │   (auto-detected)     │
              └───────────────────────┘
```

## File Structure

```
hpc_runner/
├── tui/
│   ├── __init__.py
│   ├── app.py                 # Main HpcMonitorApp
│   ├── screens/
│   │   ├── __init__.py
│   │   └── log_viewer.py      # Modal screen for log display
│   ├── components/
│   │   ├── __init__.py
│   │   ├── job_table.py       # DataTable with job rows
│   │   ├── filter_bar.py      # Composable filter widgets
│   │   ├── status_badge.py    # Colored status indicator
│   │   ├── detail_panel.py    # Job detail view
│   │   ├── user_toggle.py     # $USER / All users switch
│   │   └── unavailable.py     # "Feature not available" message
│   ├── providers/
│   │   ├── __init__.py
│   │   └── jobs.py            # Async wrapper around scheduler
│   └── styles/
│       └── monitor.tcss       # Cookbook-based styling
├── cli/
│   ├── ...
│   └── monitor.py             # hpc monitor command
├── core/
│   ├── ...
│   └── job_info.py            # JobInfo dataclass (new)
```

## UI Layout

### Active Tab
```
┌─────────────────────────────────────────────────────────────┐
│  hpc monitor                          [👤 Me]    user@cluster│
├────────────┬────────────────────────────────────────────────┤
│   Active   │  Completed                                     │
├────────────┴────────────────────────────────────────────────┤
│ [Status: ▼ All] [Queue: ▼ All] [🔍 Search...]    ↻ 10s auto │
├─────────────────────────────────────────────────────────────┤
│ ID     Name           Queue     Status    Runtime   CPU/Mem │
│─────────────────────────────────────────────────────────────│
│ 12345  build_job      gpu.q     RUNNING   2h 15m    4/16G   │
│ 12346  test_suite     batch.q   PENDING      —      8/32G   │
│ 12347  compile        batch.q   HELD         —      2/8G    │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ Job 12345: build_job                              [Cancel]  │
│─────────────────────────────────────────────────────────────│
│ Submitted: 2024-01-15 10:30:00    Queue: gpu.q              │
│ Resources: 4 CPU, 16G mem, 1 GPU  Dependencies: 12340       │
│ Output: /home/user/logs/build_job.o12345                    │
│                                                             │
│ [View stdout] [View stderr]                                 │
└─────────────────────────────────────────────────────────────┘
```

### Completed Tab
```
┌─────────────────────────────────────────────────────────────┐
│  hpc monitor                          [👤 Me]    user@cluster│
├────────────┬────────────────────────────────────────────────┤
│   Active   │  Completed                                     │
├────────────┴────────────────────────────────────────────────┤
│ [From: 📅] [To: 📅] [Exit: ▼ All] [Queue: ▼] [🔍 Search...] │
├─────────────────────────────────────────────────────────────┤
│ ID     Name           Queue     Status    Runtime   Exit    │
│─────────────────────────────────────────────────────────────│
│ 12340  preprocess     batch.q   COMPLETE  45m       0       │
│ 12339  failed_job     gpu.q     FAILED    2m        1       │
│ 12338  analysis       batch.q   COMPLETE  3h 20m    0       │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ Job 12340: preprocess                                       │
│─────────────────────────────────────────────────────────────│
│ Submitted: 2024-01-14 08:00:00    Completed: 2024-01-14 08:45│
│ Resources: 2 CPU, 8G mem          Exit code: 0              │
│ Output: /home/user/logs/preprocess.o12340                   │
│                                                             │
│ [View stdout] [View stderr]                                 │
└─────────────────────────────────────────────────────────────┘
```

### Completed Tab — Accounting Unavailable
```
├────────────┬────────────────────────────────────────────────┤
│   Active   │  Completed                                     │
├────────────┴────────────────────────────────────────────────┤
│                                                             │
│     ┌─────────────────────────────────────────────────┐     │
│     │  Job accounting is not enabled on this cluster  │     │
│     │                                                 │     │
│     │  Historical job data requires:                  │     │
│     │    • SGE: qacct with accounting enabled         │     │
│     │    • Slurm: sacct with AccountingStorageType    │     │
│     │                                                 │     │
│     │  Contact your system administrator to enable.   │     │
│     └─────────────────────────────────────────────────┘     │
│                                                             │
```

---

## Type Definitions

Add to `hpc_runner/core/job_info.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .types import JobStatus


@dataclass
class JobInfo:
    """Unified job information for TUI display."""
    job_id: str
    name: str
    user: str
    status: JobStatus
    queue: str | None = None
    submit_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    runtime: timedelta | None = None
    cpu: int | None = None
    memory: str | None = None
    exit_code: int | None = None  # None for active jobs
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    
    # Optional extended info
    node: str | None = None
    dependencies: list[str] | None = None
    array_task_id: int | None = None


class AccountingNotAvailable(Exception):
    """Raised when job accounting is not enabled on the cluster."""
    pass
```

## Scheduler API Additions

Add to `BaseScheduler` abstract class:

```python
from abc import abstractmethod
from datetime import datetime

from ..core.job_info import JobInfo, AccountingNotAvailable
from ..core.types import JobStatus


class BaseScheduler(ABC):
    # ... existing methods ...
    
    @abstractmethod
    def list_active_jobs(
        self,
        user: str | None = None,
        status: set[JobStatus] | None = None,
        queue: str | None = None,
    ) -> list[JobInfo]:
        """List currently active (running/pending/held) jobs.
        
        Args:
            user: Filter by username. None = all users.
            status: Filter by status set. None = all active statuses.
            queue: Filter by queue name. None = all queues.
            
        Returns:
            List of JobInfo for matching active jobs.
        """
        ...
    
    @abstractmethod
    def list_completed_jobs(
        self,
        user: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        exit_code: int | None = None,
        queue: str | None = None,
        limit: int = 100,
    ) -> list[JobInfo]:
        """List completed jobs from scheduler accounting.
        
        Args:
            user: Filter by username. None = all users.
            since: Jobs completed after this time.
            until: Jobs completed before this time.
            exit_code: Filter by exit code. None = all.
            queue: Filter by queue name. None = all queues.
            limit: Maximum number of jobs to return.
            
        Returns:
            List of JobInfo for matching completed jobs.
            
        Raises:
            AccountingNotAvailable: If scheduler accounting is not enabled.
        """
        ...
    
    @abstractmethod
    def has_accounting(self) -> bool:
        """Check if job accounting/history is available.
        
        Returns:
            True if list_completed_jobs() will work, False otherwise.
        """
        ...
    
    @abstractmethod
    def get_job_details(self, job_id: str) -> JobInfo:
        """Get detailed information for a single job.
        
        Args:
            job_id: The job identifier.
            
        Returns:
            JobInfo with all available details.
            
        Raises:
            JobNotFoundError: If job doesn't exist.
        """
        ...
```

---

## Implementation Stages

### Stage 1: Project Setup & Dependencies
- [x] **COMPLETE**

**Goal:** Add Textual dependency and create TUI package structure.

**Tasks:**
1. Add `textual>=6.11` to `pyproject.toml` dependencies
2. Create directory structure:
   ```
   src/hpc_runner/tui/
   ├── __init__.py
   ├── styles/
   │   └── monitor.tcss
   ```
3. Create empty `__init__.py` files
4. Create initial `monitor.tcss` with cookbook variables only

**Verification:**
- `pip install -e .` succeeds
- `from hpc_runner.tui import *` works

---

### Stage 2: CLI Entry Point
- [x] **COMPLETE**

**Goal:** Add `hpc monitor` command that launches placeholder app.

**Tasks:**
1. Create `src/hpc_runner/cli/monitor.py`:
   ```python
   import click
   
   @click.command()
   @click.option("--refresh", "-r", default=10, help="Refresh interval in seconds")
   def monitor(refresh: int):
       """Launch interactive job monitor."""
       from hpc_runner.tui.app import HpcMonitorApp
       app = HpcMonitorApp(refresh_interval=refresh)
       app.run()
   ```
2. Register command in `cli/main.py`
3. Create minimal `tui/app.py`:
   ```python
   from textual.app import App
   from textual.widgets import Static
   
   class HpcMonitorApp(App):
       def __init__(self, refresh_interval: int = 10):
           super().__init__()
           self.refresh_interval = refresh_interval
       
       def compose(self):
           yield Static("HPC Monitor - Coming Soon")
   ```

**Verification:**
- `hpc monitor` launches and shows placeholder text
- `hpc monitor --help` shows refresh option
- App exits cleanly with `q` or `Ctrl+C`

---

### Stage 3: App Shell with Tabs
- [x] **COMPLETE**

**Goal:** Create the basic app layout with Active/Completed tabs.

**Tasks:**
1. Create `tui/app.py` with:
   - TabbedContent with "Active" and "Completed" tabs
   - Header showing "hpc monitor" and user@hostname
   - Footer with basic keybindings
2. Apply cookbook styling in `monitor.tcss`:
   - CSS variables for borders/colors
   - Transparent backgrounds
   - Rounded border style
   - Tab underline styling

**Verification:**
- App shows two tabs that can be switched
- Tab switching works with click and keyboard
- Styling matches cookbook aesthetic
- Header shows correct user and hostname

---

### Stage 4: Core Types & Scheduler API Stubs
- [x] **COMPLETE**

**Goal:** Add JobInfo dataclass and scheduler method signatures.

**Tasks:**
1. Create `core/job_info.py` with `JobInfo` dataclass and `AccountingNotAvailable` exception
2. Add abstract methods to `BaseScheduler`:
   - `list_active_jobs()`
   - `list_completed_jobs()`
   - `has_accounting()`
   - `get_job_details()`
3. Add stub implementations to `LocalScheduler` (return empty lists, `has_accounting() -> False`)
4. Update `core/__init__.py` exports

**Verification:**
- All imports work
- Type checking passes (`mypy src/hpc_runner`)
- Existing tests still pass

---

### Stage 5: SGE Active Jobs Implementation
- [x] **COMPLETE**

**Goal:** Implement `list_active_jobs()` for SGE scheduler.

**Tasks:**
1. In `schedulers/sge/scheduler.py`, implement `list_active_jobs()`:
   - Call `qstat -xml` (or `qstat -u <user> -xml`)
   - Parse XML response using existing parser patterns
   - Convert to `list[JobInfo]`
   - Apply filters (status, queue)
2. Handle the "no jobs" case gracefully
3. Add tests in `tests/test_schedulers/test_sge.py`

**Verification:**
- Unit tests pass with mocked qstat output
- On SGE cluster: `list_active_jobs()` returns correct data
- Filtering by user/status/queue works

---

### Stage 6: Job Table Component
- [x] **COMPLETE**

**Goal:** Create reusable job table widget.

**Tasks:**
1. Create `tui/components/__init__.py`
2. Create `tui/components/job_table.py`:
   - Extend `DataTable`
   - Columns: ID, Name, Queue, Status, Runtime, Resources
   - Method: `update_jobs(jobs: list[JobInfo])`
   - Row selection emits message with job_id
3. Style the table in `monitor.tcss`:
   - Focus state borders
   - Status column coloring

**Verification:**
- Table renders with mock data
- Row selection works
- Columns resize appropriately

---

### Stage 7: Status Badge Component
- [x] **SKIPPED** - Plain text status in JobTable fits minimal aesthetic better

**Goal:** Create colored status indicator widget.

**Tasks:**
1. Create `tui/components/status_badge.py`:
   - Small widget showing status text with color
   - Colors: RUNNING=green, PENDING=yellow, HELD=orange, FAILED=red, COMPLETED=blue
   - ANSI fallback styling
2. Integrate into JobTable status column

**Verification:**
- Status badges show correct colors
- Works in both dark and light terminals
- ANSI mode fallback works

---

### Stage 8: Job Provider
- [x] **COMPLETE**

**Goal:** Create async data provider that wraps scheduler calls.

**Tasks:**
1. Create `tui/providers/__init__.py`
2. Create `tui/providers/jobs.py`:
   ```python
   class JobProvider:
       def __init__(self, scheduler: BaseScheduler):
           self.scheduler = scheduler
           self._current_user = os.environ.get("USER")
       
       async def get_active_jobs(
           self,
           user_filter: str | None = None,
           status_filter: set[JobStatus] | None = None,
           queue_filter: str | None = None,
       ) -> list[JobInfo]:
           # Run scheduler call in thread pool
           ...
   ```
3. Handle exceptions gracefully, return empty list on error

**Verification:**
- Provider works with real scheduler
- Errors don't crash the app
- Async calls don't block UI

---

### Stage 9: Active Tab Integration ✅ COMPLETE

**Goal:** Wire up Active tab to show real job data.

**Tasks:**
1. Update `tui/app.py`:
   - Initialize JobProvider with detected scheduler
   - On mount: fetch and display active jobs
   - Auto-refresh using `set_interval()`
2. Create Active tab content container with:
   - JobTable
   - Refresh indicator showing countdown
3. Add user toggle (Me / All) in header

**Verification:**
- Active tab shows current user's jobs
- Auto-refresh updates the table
- User toggle switches between filters
- "No jobs" state displays nicely

---

### Stage 10: Filter Bar Component

**Goal:** Create composable filter bar for job tables.

**Tasks:**
1. Create `tui/components/filter_bar.py`:
   - Status dropdown (All, Running, Pending, Held)
   - Queue dropdown (populated from job data)
   - Search input (filters by name/ID)
2. Filter bar emits `FilterChanged` message
3. Integrate into Active tab

**Verification:**
- Filters update job table in real-time
- Multiple filters compose correctly
- Search is case-insensitive
- Clear/reset works

---

### Stage 11: Detail Panel Component

**Goal:** Create job detail panel shown below/beside table.

**Tasks:**
1. Create `tui/components/detail_panel.py`:
   - Shows full JobInfo details
   - Formatted display of times, resources, paths
   - Placeholder buttons for stdout/stderr/cancel
2. Panel updates when table selection changes
3. Style with cookbook border patterns

**Verification:**
- Selection in table updates detail panel
- All JobInfo fields display correctly
- Panel handles missing optional fields gracefully

---

### Stage 12: Cancel Job Action

**Goal:** Implement job cancellation with confirmation.

**Tasks:**
1. Create `tui/screens/confirm.py`:
   - Modal dialog: "Cancel job {id}? [Yes] [No]"
   - Cookbook modal styling
2. Wire Cancel button in detail panel:
   - Push confirm screen
   - On confirm: call `scheduler.cancel(job_id)`
   - Show success/error toast
   - Refresh job list
3. Add toast notification component or use Textual's built-in

**Verification:**
- Cancel button shows confirmation
- Cancellation calls scheduler correctly
- Success/error feedback displays
- Job list refreshes after cancel

---

### Stage 13: Log Viewer Modal

**Goal:** Create full-screen log viewer.

**Tasks:**
1. Create `tui/screens/log_viewer.py`:
   - Modal screen with scrollable text area
   - Header shows file path
   - Keybindings: `q`/`Escape` to close, `g`/`G` for top/bottom
2. Implement file reading:
   - Read file from `JobInfo.stdout_path` or `stderr_path`
   - If file doesn't exist: show error message in viewer
   - Handle large files (read last N lines or stream)
3. Wire View stdout/stderr buttons in detail panel

**Verification:**
- Log viewer opens and displays file content
- Missing files show clear error
- Large files don't crash the app
- Scrolling and navigation work

---

### Stage 14: SGE Completed Jobs Implementation

**Goal:** Implement `list_completed_jobs()` and `has_accounting()` for SGE.

**Tasks:**
1. Implement `has_accounting()`:
   - Try running `qacct -j 1` or similar
   - Return True if accounting responds, False if error
2. Implement `list_completed_jobs()`:
   - Call `qacct -u <user> -d <days>` etc.
   - Parse output into `list[JobInfo]`
   - Apply filters (date range, exit code, queue)
   - Raise `AccountingNotAvailable` if accounting not enabled
3. Add tests with mocked qacct output

**Verification:**
- `has_accounting()` correctly detects availability
- `list_completed_jobs()` returns parsed data
- Date range filtering works
- Exit code filtering works

---

### Stage 15: Unavailable Message Component

**Goal:** Create the "accounting not available" display.

**Tasks:**
1. Create `tui/components/unavailable.py`:
   - Centered panel with message
   - Explains what's needed (SGE: qacct, Slurm: sacct)
   - Styled as an info box, not an error
2. Style with cookbook patterns

**Verification:**
- Component displays correctly when shown
- Message is helpful and accurate
- Styling is consistent with app

---

### Stage 16: Completed Tab Integration

**Goal:** Wire up Completed tab with completed jobs or unavailable message.

**Tasks:**
1. On Completed tab mount:
   - Check `scheduler.has_accounting()`
   - If False: show UnavailableMessage
   - If True: show filter bar + job table
2. Create completed-tab filter bar:
   - Date range (From/To date pickers)
   - Exit code dropdown (All, Success, Failed)
   - Queue dropdown
   - Search input
3. Fetch and display completed jobs
4. Detail panel works same as Active tab (minus Cancel button)

**Verification:**
- Completed tab shows correct state based on accounting availability
- Date filtering works
- Exit code filtering works
- Detail panel and log viewing work for historical jobs

---

### Stage 17: Keyboard Shortcuts & Footer

**Goal:** Add keyboard navigation and help footer.

**Tasks:**
1. Define key bindings in app:
   - `q`: Quit
   - `r`: Manual refresh
   - `tab`: Switch tabs
   - `j/k` or arrows: Navigate table
   - `enter`: View selected job logs
   - `c`: Cancel selected job (Active tab only)
   - `?`: Show help
2. Create footer showing available shortcuts
3. Footer updates based on context (different shortcuts per tab)

**Verification:**
- All keybindings work as documented
- Footer shows correct shortcuts
- Help screen/overlay works

---

### Stage 18: Error Handling & Edge Cases

**Goal:** Ensure robust error handling throughout.

**Tasks:**
1. Handle scheduler connection errors gracefully
2. Handle empty job lists (show "No jobs found" message)
3. Handle refresh failures (show toast, keep stale data)
4. Handle permission errors (can't view other users' jobs)
5. Add loading indicators during data fetch
6. Test with no jobs, many jobs (100+), and error conditions

**Verification:**
- App never crashes on scheduler errors
- User sees helpful error messages
- Loading states are clear
- Recovery from errors works

---

### Stage 19: Polish & Responsive Layout

**Goal:** Final polish and responsive design.

**Tasks:**
1. Add responsive breakpoints:
   - Narrow: Hide detail panel, show on selection as modal
   - Wide: Show detail panel beside table
2. Test and fix ANSI mode styling
3. Test and fix light theme styling
4. Ensure consistent spacing and alignment
5. Review all text for clarity

**Verification:**
- App looks good at various terminal sizes
- Works in ANSI mode
- Works with light terminal themes
- No visual glitches or misalignments

---

### Stage 20: Documentation & Tests

**Goal:** Document the TUI and add integration tests.

**Tasks:**
1. Add TUI section to README.md
2. Document keybindings
3. Document configuration options
4. Add integration tests for TUI components
5. Update CLAUDE.md with TUI architecture

**Verification:**
- Documentation is accurate and helpful
- Tests pass
- New developer can understand the TUI structure

---

## Configuration Options

Add to `defaults/config.toml`:

```toml
[monitor]
refresh_interval = 10  # seconds
default_user_filter = "me"  # "me" or "all"
history_days = 7  # default date range for history tab
page_size = 50  # jobs per page
```

## Keyboard Shortcuts Reference

| Key | Action | Context |
|-----|--------|---------|
| `q` | Quit | Global |
| `r` | Refresh | Global |
| `Tab` | Switch tabs | Global |
| `j` / `↓` | Next row | Job table |
| `k` / `↑` | Previous row | Job table |
| `Enter` | View stdout | Job selected |
| `e` | View stderr | Job selected |
| `c` | Cancel job | Active tab, job selected |
| `u` | Toggle user filter | Global |
| `/` | Focus search | Filter bar |
| `Escape` | Close modal/clear | Various |
| `?` | Show help | Global |

---

## Testing Strategy

**Unit Tests:**
- JobInfo dataclass serialization
- Filter logic
- Scheduler output parsing

**Component Tests:**
- JobTable rendering
- FilterBar state management
- StatusBadge colors

**Integration Tests:**
- Full app startup
- Tab switching
- Data refresh cycle

**Manual Testing Checklist:**
- [ ] Fresh install and launch
- [ ] With active jobs
- [ ] With no jobs
- [ ] Cancel job flow
- [ ] View logs flow
- [ ] Completed tab with accounting
- [ ] Completed tab without accounting
- [ ] Various terminal sizes
- [ ] ANSI mode
- [ ] Light terminal theme
