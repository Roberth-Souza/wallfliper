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


![Demonstração do Wallfliper](https://github.com/user-attachments/assets/3a3bb80b-213b-46e7-9703-ea75c17b1330)


</div>

---

## ✨ Features

- **Keyboard-first** — arrow keys / `hjkl` / `wasd` to move, `/` to filter, `Enter` to apply. No mouse needed, but it works too (click to select, double-click to apply).
- **Images _and_ video wallpapers** — via [`swww`](https://github.com/LGFae/swww), looping video via [`mpvpaper`](https://github.com/GhostNaN/mpvpaper).
- **Smooth transitions** — animated switches for both images and video
- **Live previews** — selecting a video plays a short looping clip right on its thumbnail.
- **Audition mode** — `Space` applies a wallpaper but keeps the picker open, so you can flip through options on your real desktop.
- **Lightweight & on-demand** — launches when you call it, exits cleanly, and never idles in the background. Rendering is handed to detached daemons.
- **Restore on login** — remembers your last wallpaper so a video survives a reboot.
- **Color-scheme integration** — tells noctalia / matugen / wallust / pywal to re-theme your system from the new wallpaper.
- **Riced to taste** — flat, borderless TUI look; the surface is transparent except the floating bar and the cards, so your desktop stays visible while you browse.

## 🖥️ Supported compositors

Wallfliper draws its picker as a `wlr-layer-shell` overlay and delegates painting to `swww`/`mpvpaper`, so it targets compositors that implement **`wlr-layer-shell`**:

> **Hyprland · Sway · river · Wayfire · niri**

Out of scope (no layer-shell, or only partial): KDE Plasma, GNOME, X11, Windows.

## 🙃 Why this exists

Honestly? I just wanted to give Claude Code a try, and ended up enjoying it a little too much.

Wallfliper was built mostly **"low-code"** — most of the codebase was written with the help of AI tooling like **Claude Code** and **Google Stitch**, with me steering the design, scope and decisions.

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
sudo pacman -S pyside6 layer-shell-qt swww ffmpeg

paru -S mpvpaper
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

### Recommended

Bind Wallfliper to a compositor hotkey. Pressing the hotkey again while it's open
closes it — it's a toggle.

```lua
-- Hyprland (Lua config) — ~/.config/hypr/modules/keybinds.lua
hl.bind("SUPER + W", hl.dsp.exec_cmd("python /path/to/wallfliper/main.py"))
```
```ini
# Sway — ~/.config/sway/config
bindsym $mod+w exec python /path/to/wallfliper/main.py
```
```kdl
# niri — ~/.config/niri/config.kdl
binds { Mod+W { spawn "python" "/path/to/wallfliper/main.py"; } }
```
---

| Key | Action |
| --- | --- |
| `↑ ↓ ← →` · `h j k l` · `w a s d` | Move selection |
| `/` | Start searching — then type to filter |
| `Backspace` | Edit the filter (empty filter → leave search) |
| `i` · `v` · `e` | Show **images only** / **videos only** / **everything** |
| `c` | **Color filter** — a swatch strip opens below the cards; move to filter live, `Enter` keeps it, `Esc`/`c` clears |
| `Enter` | Apply selected wallpaper **and close** |
| `Space` | Apply but **keep open** (audition on your desktop) |
| `Shift+D` | **Delete** the selected wallpaper file (permanent, no confirmation) |
| `Esc` | Close (or close the settings panel) |
| Double-click | Apply and close |
| Click outside the cards | Close |
| `/config` + `Enter` | Open settings |

Applying an image stops any running video wallpaper — there's only ever one wallpaper at a time.

### ⚙️ Settings

Type `/config` while searching and hit `Enter` (keyboard-driven: `j/k` move · `Enter`
select · `Esc` close):

- **folder** — choose your wallpaper directory (via your `xdg-desktop-portal` file chooser)

## 🌫️ Blur (optional, Hyprland)

There is no backdrop panel — only the floating bar is painted with alpha. To frost it,
add a layer rule for the `wallfliper` namespace (`ignore_alpha` keeps the transparent
desktop area unblurred):

```lua
hl.layer_rule({ name = "wallfliper", match = { namespace = "wallfliper" }, blur = true, ignore_alpha = 0.5 })
```

Verify the namespace any time with `hyprctl layers` while Wallfliper is open.

## 🔁 Restore on login

Wallfliper saves the last-applied wallpaper to `~/.config/wallfliper/state.json`.
`wallfliper --restore` reads that and re-spawns `swww`/`mpvpaper` — so a **video
wallpaper survives a reboot**. (Wallfliper itself doesn't stay running; it just hands
the file back to the renderer and exits — mpvpaper is stateless and dies on reboot,
so something has to re-launch it, and that's all `--restore` does.)

```fish
python /path/to/wallfliper/main.py --restore   # re-apply the saved wallpaper now
```

**Run it on login from your compositor's autostart**, wrapped in a short `sleep` so the
compositor finishes bringing up your desktop first. Without the delay a *video* wallpaper
can come up frozen on a low-res frame: mid-boot the wallpaper surface is briefly reported
as "hidden", which trips `mpvpaper`'s auto-pause (a known mpvpaper quirk). A few seconds'
delay sidesteps it:

```lua
-- Hyprland (Lua config) — inside hl.on("hyprland.start", ...) in your autostart.lua
hl.exec_cmd("sh -c 'sleep 5 && python /path/to/wallfliper/main.py --restore'")
```
```ini
# Sway — ~/.config/sway/config
exec sh -c 'sleep 5 && python /path/to/wallfliper/main.py --restore'
```
```kdl
# niri — ~/.config/niri/config.kdl
spawn-at-startup "sh" "-c" "sleep 5 && python /path/to/wallfliper/main.py --restore"
```
```ini
# Wayfire — ~/.config/wayfire.ini
[autostart]
wallfliper = sh -c 'sleep 5 && python /path/to/wallfliper/main.py --restore'
```

> Uses **system `python`** (the same interpreter that runs the app) — no virtualenv,
> no activation. Once you install via a package (e.g. AUR), a real `wallfliper` command
> lands on PATH and every line above shortens to just `wallfliper --restore`.

<details>
<summary>Alternative: XDG autostart entry</summary>

`wallfliper --install-autostart` writes `~/.config/autostart/wallfliper-restore.desktop`
(the freedesktop standard). It works on sessions that **honor XDG autostart** — GNOME/KDE,
or wlroots compositors launched via UWSM/systemd. A **bare Hyprland/Sway/niri session
won't read that folder**, so on a plain setup use the compositor `exec` line above instead.
</details>

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

The app still runs without the optional tools — missing dependencies are reported in the
terminal at startup, and `--check` lists them with install hints.

## 📄 License

[GPL-3.0](LICENSE). PySide6 is used under the LGPL.
