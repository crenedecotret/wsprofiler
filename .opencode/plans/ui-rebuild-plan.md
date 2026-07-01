# UI Rebuild Plan: wsprofiler — Phase 1 (Simplified)

## Executive Summary

Rebuild the wsprofiler UI with a modern dark theme and improved navigation, while keeping all existing functionality intact. This is the **minimum viable visual upgrade** — focused on theme + navigation + Create page polish. New pages (Print, Refine) are deferred.

---

## Technology: Stay with Python + PySide6

The core logic (Argyll integration, CGATS parsing, profiling algorithms, PTY subprocess handling) is all working. The bottleneck is design, not technology. PySide6 has everything needed.

---

## Scope: Theme + Navigation + Create Page Polish

### What We're Doing
1. ✅ Dark theme system with QSS
2. ✅ Top step bar navigation (replaces sidebar)
3. ✅ Refresh Generate (Create Chart) page layout
4. ✅ Style existing Measure and Profile pages
5. ✅ Style Two-Step Wizard dialog

### What We're NOT Doing (Deferred)
- ❌ New Print Chart page (Step 2)
- ❌ New Check & Refine page (Step 5)
- ❌ Guided/Manual toggle (future enhancement)
- ❌ Live chart preview on Create page

---

## Design System

### Color Palette (Dark Theme)

```
Backgrounds:
  --bg-primary:      #1a1a2e   (main window)
  --bg-secondary:    #16213e   (sidebar/panels)
  --bg-surface:      #0f3460   (cards/surfaces)
  --bg-input:        #1a1a3e   (input fields)

Accent:
  --accent-primary:  #e94560   (pink/coral — buttons, highlights)
  --accent-success:  #1F8A4C   (green — success states)
  --accent-warning:  #f4a261   (orange — warnings)
  --accent-error:    #e63946   (red — errors)

Text:
  --text-primary:    #ffffff   (main text)
  --text-secondary:  #a0a0b0   (secondary text)
  --text-muted:      #6c6c80   (muted/placeholder)

Borders:
  --border-color:    #2a2a4a   (default borders)
  --border-focus:    #e94560   (focus rings)
```

### Typography
- Font: `'Segoe UI', 'SF Pro Display', system-ui, sans-serif`
- Page titles: 22px, weight 600
- Section headers: 14px, weight 600, uppercase, letter-spacing 1px
- Body: 14px, weight 400
- Labels: 13px, weight 500

---

## Implementation Steps

### Step 1: Create Theme System
**Files to create:**
- `wsprofiler/ui/theme.py` — Color constants + `apply_theme(app)` function
- `wsprofiler/ui/styles/dark.qss` — Complete dark theme QSS

**What it covers:**
- QMainWindow, QWidget backgrounds
- QPushButton (primary, secondary, danger variants)
- QLineEdit, QComboBox, QSpinBox, QCheckBox styling
- QListWidget (for navigation)
- QTextEdit/QPlainTextEdit (console/log)
- QLabel styling
- QGroupBox
- QToolTip
- QScrollBar
- Focus rings and hover states

### Step 2: Apply Theme on Startup
**File to modify:** `wsprofiler/app.py`
- Import and call `apply_theme(app)` before creating MainWindow

### Step 3: Create Top Step Bar
**File to create:** `wsprofiler/ui/step_bar.py`
- `StepBar(QWidget)` — horizontal row of numbered circles
- States: active (accent color), completed (green with ✓), inactive (muted)
- Click to navigate between steps
- Labels below each circle: "Create Chart", "Print Chart", "Measure", "Build Profile", "Check & Refine"

### Step 4: Replace Sidebar Navigation
**File to modify:** `wsprofiler/ui/wizard.py`
- Remove left sidebar (QListWidget + Two-Step button)
- Add StepBar at top
- Move "Two-Step Profile Wizard" button to a less prominent location (maybe menu or toolbar)
- Update layout: StepBar on top, QStackedWidget below

### Step 5: Update Main Window
**File to modify:** `wsprofiler/ui/main_window.py`
- Update window title (remove "— Argyll GUI", keep clean)
- Remove 8px margins (let theme handle spacing)
- Ensure dark background fills entire window

### Step 6: Polish Create (Generate) Page
**File to modify:** `wsprofiler/ui/pages/generate_page.py`
- Add section headers ("Output", "Measurement Instrument", "Paper", "Chart Size")
- Group controls into styled panels/cards
- Style the "Generate" button as primary accent button
- Add calculated patches display (if not already present)
- Style the output/log console area

### Step 7: Style Measure Page
**File to modify:** `wsprofiler/ui/pages/measurement_page.py`
- Apply dark theme to chart view background
- Style instrument panel
- Update measurement feedback colors
- Ensure chart patches render well on dark background

### Step 8: Style Profile Page
**File to modify:** `wsprofiler/ui/pages/profile_page.py`
- Apply dark theme
- Style controls consistently with other pages

### Step 9: Style Two-Step Wizard
**File to modify:** `wsprofiler/ui/two_step_dialog.py`
- Apply dark theme to dialog
- Style the step indicator
- Update button styling
- Ensure modal looks consistent with main window

### Step 10: Style Dialogs
**Files to modify:**
- `wsprofiler/ui/chartread_dialog.py` — Calibration/error dialogs
- `wsprofiler/ui/log_console.py` — Console styling

---

## File Summary

### New Files (3)
```
wsprofiler/ui/theme.py              # Theme system
wsprofiler/ui/styles/dark.qss       # Dark theme stylesheet
wsprofiler/ui/step_bar.py           # Top navigation step bar
```

### Modified Files (9)
```
wsprofiler/app.py                   # Apply theme
wsprofiler/ui/main_window.py        # Layout updates
wsprofiler/ui/wizard.py             # Replace sidebar with StepBar
wsprofiler/ui/pages/generate_page.py    # Polish
wsprofiler/ui/pages/measurement_page.py # Style refresh
wsprofiler/ui/pages/profile_page.py     # Style refresh
wsprofiler/ui/two_step_dialog.py        # Theme apply
wsprofiler/ui/chartread_dialog.py       # Theme apply
wsprofiler/ui/log_console.py            # Theme apply
```

---

## Migration Strategy

1. **Theme first** — Create `dark.qss` and `theme.py`, apply globally. Test that nothing breaks.
2. **Navigation second** — Add StepBar, remove sidebar. Test navigation works.
3. **Polish third** — Style individual pages. Test each page independently.
4. **Regression test** — Run through full workflow (Generate → Measure → Profile) to ensure nothing is broken.

---

## Estimated Effort

| Step | Complexity | Time |
|------|------------|------|
| Step 1: Theme System | Medium | 2-3 hrs |
| Step 2: Apply Theme | Low | 15 min |
| Step 3: Step Bar | Medium | 2-3 hrs |
| Step 4: Replace Sidebar | Medium | 1 hr |
| Step 5: Main Window | Low | 15 min |
| Step 6: Create Page Polish | Medium | 2 hrs |
| Step 7: Measure Page | Low | 1 hr |
| Step 8: Profile Page | Low | 30 min |
| Step 9: Two-Step Wizard | Medium | 1 hr |
| Step 10: Dialogs | Low | 30 min |
| **Total** | | **11-14 hrs** |

---

## Success Criteria

1. ✅ Dark theme applied consistently across all pages
2. ✅ Top step bar navigation (replaces sidebar)
3. ✅ Styled Generate page with section grouping
4. ✅ All existing functionality preserved
5. ✅ Two-Step Wizard looks consistent
6. ✅ Cross-platform appearance (Linux + Windows)
