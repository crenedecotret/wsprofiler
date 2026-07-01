# Display ICC Profile Support for wsprofiler

## Problem Summary

On launch, wsprofiler emits these Qt debug messages:

```
qt.gui.icc: fromIccProfile: failed size sanity 2
QColorSpace attempted constructed from invalid primaries: ...
```

These are Qt 6 *trying* to load the display's ICC profile (from the X11 `_ICC_PROFILE` atom) but finding garbage/corrupted data. The application currently ignores the display profile entirely — all on-screen color is rendered with hardcoded sRGB math. The user wants to know why, and wants the app to use the system's display ICC profile for accurate on-screen color.

## Current State (What Happens Now)

### On-screen color is 100% hardcoded sRGB

| Location | File | What it does |
|---|---|---|
| Chart patch rendering | `ui/chart_view.py:97` | `QColor(*patch.approx_rgb())` — sRGB only |
| Patch RGB approximation | `ti/ti2.py:150-212` | `_xyz_to_srgb()` — hardcoded sRGB matrix + Bradford D65 adaptation |
| Live TI3 measurement display | `ui/ti3_watcher.py:77-142` | `_simple_xyz_to_rgb()` — another hardcoded sRGB path |
| Main window | `ui/main_window.py:27-31` | `QGuiApplication.primaryScreen()` called ONLY for geometry, not color |
| Two-step wizard | `ui/two_step_dialog.py:286-292` | Same — geometry only |

### The app generates ICC profiles as output (printer profiles)

This is the app's primary purpose: produce printer ICC profiles via ArgyllCMS `colprof`. It does NOT use ICC profiles for on-screen display management.

### Qt 6 IS trying to read the display profile

Qt 6's `QScreen::colorSpace()` queries the platform plugin. On X11/XCB, it reads the `_ICC_PROFILE` atom on the root window. The error `"failed size sanity 2"` means the atom exists but contains invalid data (reading only 2 bytes, or the profile header size field is corrupt). On Wayland, the behavior depends on compositor support for the color-management protocol.

## Why Display ICC Profile Isn't Currently Used

1. **Printer-centric workflow**: The spectrophotometer (`chartread`) measures physical printed charts, not the screen. On-screen color accuracy is a nice-to-have, not a requirement for producing correct printer ICC profiles.

2. **Cross-platform complexity**: Reading the system's active display profile requires different approaches per platform:
   - **X11**: `_ICC_PROFILE` atom on root window (low-level XCB/Xlib, not exposed via Qt APIs directly)
   - **Wayland**: `wp-color-management-v1` or `wlr-gamma-control` protocol
   - **GNOME/colord**: D-Bus service `org.freedesktop.ColorManager`
   - **Windows**: WCS `GetICMProfile()` / `WcsGetDefaultColorProfile()`
   - **macOS**: ColorSync `CGDisplayCopyColorSpace()`

3. **Profile type uncertainty**: Display profiles are often LUT-based (A2B/B2A) not just matrix-based. Applying a full LUT profile to window rendering is more complex than a simple matrix transform. Qt's `QColorSpace` supports ICC profiles, but the quality of rendering depends on the profile type.

4. **Design decision**: Most printer-profiling workflows assume sRGB previews. ArgyllCMS itself takes this approach — `printtarg` renders chart TIFFs in sRGB. The assumption is that a calibrated display will approximately match sRGB for visualization purposes.

## Proposed Solution

Add display ICC profile awareness so wsprofiler can color-manage its on-screen rendering. The approach has two tiers:

### Tier 1 (Simple, Quick Win): Qt's Built-in Color Space

Use Qt 6's `QScreen::colorSpace()` and `QWindow::setColorSpace()`. This works everywhere Qt 6 supports and adds ~5 lines of code.

- **What it does**: Tags the application window with the screen's color space, letting Qt's compositor/rendering engine handle the color transformation for all window content.
- **Limitation**: Only works if Qt's platform plugin successfully reads the display profile (i.e., the `_ICC_PROFILE` atom is valid). Won't help on the user's current system since the atom data is corrupt.

### Tier 2 (Robust): Platform-specific Display Profile Discovery

Implement a cross-platform mechanism to find and load the system's active display ICC profile, then manually apply it:

1. Try `QScreen::colorSpace()` first
2. On X11: read `_ICC_PROFILE` atom with XCB (fall back to colord D-Bus if atom is missing/corrupt)
3. On Wayland: check for wp-color-management or fall back to colord
4. On Windows: `EnumICMProfiles()` / `WcsGetDefaultColorProfile()`
5. On macOS: `CGDisplayCopyColorSpace()` → `CGColorSpaceCopyICCData()`

Apply the profile by:
- Converting sRGB patch colors through the display profile to display-native RGB
- Setting `QWindow::setColorSpace()` with the loaded profile

## Phase 2: Implementation Plan

### Step 1: Fix the Startup Script (System Level)

Choose Option A, B, or C from Phase 1 above. Option A (colord) is recommended for per-monitor discoverability.

### Step 2: Create `wsprofiler/display_profile.py`

Cross-platform module with layered discovery (tries each in order, returns first success):

```
1. QGuiApplication.primaryScreen().colorSpace()   ← Qt 6 built-in
2. (Linux) colord D-Bus: get display devices + active profiles
3. (Linux) _ICC_PROFILE X11 atom (with multi-atom chunk assembly)
4. (Windows) EnumICMProfiles / GetICMProfile
5. (macOS) CGDisplayCopyColorSpace
```

Key functions:
- `get_display_color_space(screen_index: int = 0) -> QColorSpace | None`
- `_get_colord_profile(device_path: str) -> bytes | None`
- `_get_x11_atom_profile() -> bytes | None`

### Step 3: Add Opt-in Preference

Add a checkbox to the settings/preferences area:
- Label: "Use display color profile for accurate on-screen color"
- Default: OFF (backward compatible, no surprises)
- Persisted via QSettings under `display/use_color_management`

### Step 4: Apply QColorSpace to Windows

In `main_window.py` and `two_step_dialog.py`, after window creation:

```python
if settings.value("display/use_color_management", False, type=bool):
    cs = get_display_color_space()
    if cs and cs.isValid():
        window.windowHandle().setColorSpace(cs)
```

### Step 5: Profile-Aware Chart Rendering (Optional Enhancement)

When display color management is enabled, optionally skip the hardcoded sRGB→XYZ→sRGB round-trip in `ti2.py` and `ti3_watcher.py`, and instead use the display profile's native color space for patch colors.

### Files to Create/Modify

| File | Action |
|---|---|
| `wsprofiler/display_profile.py` | **NEW** — cross-platform display ICC discovery |
| `wsprofiler/ui/main_window.py` | Apply QColorSpace after window creation |
| `wsprofiler/ui/two_step_dialog.py` | Apply QColorSpace after dialog creation |
| `wsprofiler/ui/settings.py` or preferences area | Add "Use display color profile" checkbox |
| `pyproject.toml` | Add `dbus-python` as optional dependency for Linux |

## Decisions Made

1. **Approach**: Diagnostic-first — fix the corrupt ICC atom before adding code
2. **User control**: Opt-in (checkbox/preference) — user must enable display color management
3. **Tier**: Will start with Tier 1 (Qt built-in) and extend to Tier 2 if needed

## Diagnosis: Root Cause of Corrupt `_ICC_PROFILE`

**Both monitors share one X screen** → one root window → one `_ICC_PROFILE` atom. The second `dispwin -d2 -I` overwrites the first. Only D2's profile is in the atom.

**Profile is 1.26 MB** → exceeds X11 ~256KB atom size limit. `dispwin` may chunk across multiple numbered atoms (`_ICC_PROFILE_2`, `_ICC_PROFILE_3`, …), but the startup script only cleans up atoms 0 and 1. Leftover atoms from previous sessions corrupt the chunked data.

**colord IS running** (PID 1359, system bus), but no display devices are registered. This is the proper per-monitor pathway that's currently unused.

## Phase 1: Fix System-Level Profile Loading

Replace the `dispwin -I` startup script with a colord-based approach that properly supports:
- Per-monitor profile assignment
- No atom size limits
- Discoverability by Qt and other apps

### Option A: Use colord directly (recommended)

```bash
#!/bin/bash
# Register profiles with colord (one per output)
# dispwin can use colord backend if available
dispwin -v -d1 -I -S l "/home/charles/.local/share/icc/Koorui-D1_2026-02.icc"
dispwin -v -d2 -I -S l "/home/charles/.local/share/icc/Koorui-D2_2026-02.icc"
```

The `-S l` flag tells dispwin to use the "local" (colord) install scope. Verify with:
```bash
dbus-send --system --dest=org.freedesktop.ColorManager \
  --print-reply /org/freedesktop/ColorManager \
  org.freedesktop.ColorManager.GetDevices
```

### Option B: Use only LUT loading (no profile install)

```bash
#!/bin/bash
dispwin -v -d1 -L "/home/charles/.local/share/icc/Koorui-D1_2026-02.icc"
dispwin -v -d2 -L "/home/charles/.local/share/icc/Koorui-D2_2026-02.icc"
```

`-L` loads the calibration curves into the GPU LUT directly — no atom needed. But profiles won't be discoverable by applications.

### Option C: Fix the atom-chunking cleanup

```bash
#!/bin/bash
# Remove ALL possible chunk atoms (0-15)
for i in $(seq 0 15); do
    xprop -root -remove "_ICC_PROFILE_${i}" 2>/dev/null
done
xprop -root -remove _ICC_PROFILE 2>/dev/null

dispwin -v -d1 -I "/home/charles/.local/share/icc/Koorui-D1_2026-02.icc"
# Only load LUT for second monitor (can't install two profiles to same atom)
dispwin -v -d2 -L "/home/charles/.local/share/icc/Koorui-D2_2026-02.icc"
```

This fixes the corruption but still only supports one profile via the atom.

## Phase 2: Add Display ICC Support to wsprofiler (Opt-in)

Add a user preference toggle for display color management. When enabled:
