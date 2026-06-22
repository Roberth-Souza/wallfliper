<div align="center">

# 🖼️ Wallfliper

**A minimalist, Rofi/yazi-style wallpaper selector for Wayland.**

Borderless, dark, keyboard-first — the wallpapers are the content, the UI disappears.
Pick a still image or a looping video, hit `Enter`, done.

![License](https://img.shields.io/badge/license-GPL--3.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Toolkit](https://img.shields.io/badge/Qt-PySide6-41cd52)
![Platform](https://img.shields.io/badge/platform-Wayland%20%C2%B7%20wlr--layer--shell-informational)

</div>

---

<div align="center">






</div>

---

## ✨ Features

- **Keyboard-first** — arrow keys / `hjkl` / `wasd` to move, `/` to fuzzy-filter, `Enter` to apply. No mouse needed, but it works too (click to select, double-click to apply).
- **Images _and_ video wallpapers** — via [`swww`](https://github.com/LGFae/swww), looping video via [`mpvpaper`](https://github.com/GhostNaN/mpvpaper).
- **Live previews** — selecting a video plays a short looping clip right on its thumbnail.
- **Audition mode** — `Space` applies a wallpaper but keeps the picker open, so you can flip through options on your real desktop.
- **Lightweight & on-demand** — launches when you call it, exits cleanly, and never idles in the background. Rendering is handed to detached daemons.
- **Restore on login** — remembers your last wallpaper so a video survives a reboot.
- **Color-scheme integration** — tells noctalia / matugen / wallust / pywal to re-theme your system from the new wallpaper.
- **Riced to taste** — round/sharp corners and backdrop opacity, made for a `layerrule = blur` compositor.

## 🖥️ Supported compositors

Wallfliper draws its picker as a `wlr-layer-shell` overlay and delegates painting to `swww`/`mpvpaper`, so it targets compositors that implement **`wlr-layer-shell`**:

> **Hyprland · Sway · river · Wayfire · niri**

Out of scope (no layer-shell, or only partial): KDE Plasma, GNOME, X11, Windows.

## 📦 Installation

Install from source — works on any supported compositor:

> [!IMPORTANT]
> **Install PySide6 from your distribution, not pip.** `layer-shell-qt` is a compiled
> Qt plugin loaded into the running process — its Qt version must match the Qt that
> PySide6 uses. Distro packages are all built against the same system Qt, so they always
> match. A `pip install PySide6` bundles its *own* Qt and can mismatch the system
> layer-shell plugin, causing cryptic load failures.

**1. Install the dependencies**

| Dependency | Purpose | Required? |
| --- | --- | --- |
| `pyside6` | the Qt6 / QML runtime | ✅ required |
| `layer-shell-qt` | the overlay (`org.kde.layershell` QML module) | ✅ required |
| `swww` *or* `awww` | image wallpapers (auto-detected) | image support |
| `mpvpaper` | video wallpapers | video support |
| `ffmpeg` | video thumbnails, previews & color extraction | video extras |

**On Arch / CachyOS** — copy-paste:

```fish
# official repos
sudo pacman -S pyside6 layer-shell-qt swww ffmpeg

# video backend (AUR — use your helper)
paru -S mpvpaper        # or: yay -S mpvpaper
```

**On other distros** — install them with your package manager (names vary, e.g.
`python3-pyside6`). `swww` and `mpvpaper` are usually **not packaged** outside Arch —
build them from source (both have simple instructions):

- **swww** → <https://github.com/LGFae/swww> (Rust)
- **mpvpaper** → <https://github.com/GhostNaN/mpvpaper>

**2. Run it**

```fish
git clone https://github.com/Roberth-Souza/wallfliper
cd wallfliper

python main.py --check        # ✓/✗ report of every dependency
python main.py                # launch
```

> 💡 `python main.py --check` prints a per-dependency report with an install hint for
> whatever's missing — run it first if anything misbehaves.

## ⌨️ Usage

### Recomended

Bind `wallfliper` (or `python /path/to/main.py`) to a compositor hotkey. Pressing the
hotkey again while it's open closes it , it's a toggle.

| Key | Action |
| --- | --- |
| `↑ ↓ ← →` · `h j k l` · `w a s d` | Move selection |
| `/` | Start searching — then type to fuzzy-filter |
| `Backspace` | Edit the filter (empty filter → leave search) |
| `Enter` | Apply selected wallpaper **and close** |
| `Space` | Apply but **keep open** (audition on your desktop) |
| `Esc` | Close (or close the settings panel) |
| Double-click | Apply and close |
| Click outside the panel | Close |
| ⚙ (gear) | Open settings |

Applying an image stops any running video wallpaper , there's only ever one wallpaper at a time.

### ⚙️ Settings

Click the gear (or it's keyboard-driven: `j/k` move · `←/→` change · `Enter` select · `Esc` close):

- **corners** — round or sharp (rices the whole UI)
- **background** — backdrop darkness / opacity
- **folder** — choose your wallpaper directory (via your `xdg-desktop-portal` file chooser)

## 🌫️ Blur (optional, Hyprland)

Hyprland blurs *windows* by default, but **not** layer-shell surfaces — so Wallfliper
starts out unblurred. To get the frosted-glass panel, add a layer rule for its
`wallfliper` namespace:

```ini
layerrule = blur, wallfliper
layerrule = ignorealpha 0.5, wallfliper
```

`ignorealpha` keeps blur off the fully-transparent click-away margin (and the rounded
corners), so only the panel frosts. The panel is fairly opaque by default, so lower
**background** in Settings to actually see the blur.

> **Lua config?**
> ```lua
> hl.layer_rule({ name = "wallfliper", match = { namespace = "wallfliper" }, blur = true, ignore_alpha = 0.5 })
> ```
> Verify the namespace any time with `hyprctl layers` while Wallfliper is open.

Other `wlr-layer-shell` compositors expose their own blur mechanism (or none) — consult
their docs; Wallfliper just provides the transparent surface for them to blur.

## 🔁 Restore on login

Wallfliper saves the last-applied wallpaper. To re-apply it (so a video wallpaper survives a reboot):

```fish
wallfliper --restore            # re-apply now
wallfliper --install-autostart  # run --restore automatically on login
```

No background daemon of our own — rendering is handled by `swww-daemon` / `mpvpaper`.

## 🎨 Color-scheme integration

After applying a wallpaper, Wallfliper notifies external color tools so your **system**
color scheme regenerates from it (best-effort, never blocks). It auto-detects
[noctalia-shell](https://github.com/noctalia-dev) if running; for everything else set
`color_hook` in `~/.config/wallfliper/config.json` (`{path}` is substituted):

```json
{
  "color_hook": "matugen image {path}"
}
```

Works with matugen / wallust / pywal / any command. For video wallpapers it themes from a
still frame extracted with `ffmpeg`.

## 🩹 Troubleshooting

**First step, always:** `wallfliper --check` — it tells you exactly what's missing.

| Symptom | Cause & fix |
| --- | --- |
| `ImportError` / won't start | PySide6 missing — install your distro's `pyside6`. |
| *"failed to load QML UI"* | `layer-shell-qt` missing — install it (provides `org.kde.layershell`). |
| Overlay never appears | Not on a `wlr-layer-shell` Wayland session — check your compositor. |
| ⚠ *"no wallpaper tool found"* on apply | Install `swww` (or `awww`) for images. |
| ⚠ *"mpvpaper is not installed"* on apply | Install `mpvpaper` for video wallpapers. |
| Video cards show `▶` instead of a frame | Install `ffmpeg` (thumbnails/previews are optional). |
| Settings folder picker doesn't open | Install an `xdg-desktop-portal` backend (e.g. `xdg-desktop-portal-gtk` or `-termfilechooser`). |

the app still runs and tells you in the status bar what to install.

## 📄 License

[GPL-3.0](LICENSE). PySide6 is used under the LGPL.
