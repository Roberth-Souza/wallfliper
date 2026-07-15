# Wallfliper — Design Principles

North star: a **TUI/yazi-style** front. The wallpapers are the content; the UI disappears.
When in doubt, remove. Every pixel of chrome must justify itself.

## Hard rules (non-negotiable)
- **Framed, not decorated.** The 2px solid white border is the frame language — the same as the author's rofi/yoink setup. It outlines the floating bar and the focused card; there is no window panel to frame. No title bar, no window controls, no menu bar.
- **Dark, flat, monochrome.** No gradients, no drop shadows, no glassmorphism, no skeuomorphism. Selection/focus is white; everything else is a shade of grey. There is **no backdrop panel**: the desktop shows through everywhere except the floating bar and the wallcards. The bar keeps the fixed near-black `rgba(5,5,5,0.95)` fill (matches the system rofi theme; the alpha is what a compositor blur rule reads) — not a setting.
- **Sharp corners only.** Every `Rectangle` is square (no `radius`). Not a setting — there's no "round" alternative anywhere in the UI.
- **Black & white only.** The entire palette is greyscale: near-black background, dark-grey surface, grey muted text, white primary text/selection. **No color** — no blue, no accent hue, nothing tinted. The wallpapers are the only color on screen.
- **Keyboard-first.** Every action reachable without a mouse. Mouse is optional, never required. Modal, like yazi/vim: in *normal* mode `w/a/s/d` or `h/j/k/l` + arrows move (WASD by preference, vim keys and arrows also work), `Space` applies-and-stays (audition the wallpaper live on the desktop), `Enter` applies-and-exits, `/` opens search; in *search* mode every printable key (incl. `w/a/s/d` and space) filters so any filename is typable, while arrows navigate results (not the query). Leaving search has two flavors: `Enter`, an arrow key, or clicking a result **confirms** the filter (drops back to normal nav, query kept); `Esc`, `/` again, or clicking empty app chrome **cancels** it (clears the query, full grid returns). `Esc` in normal mode exits the app instantly.
- **Typography over iconography.** Monospace everywhere: JetBrainsMono Nerd Font when installed, falling back to the fontconfig generic `monospace` (never a hard font dependency). No icons in the chrome at all — even settings is a typed command (`/config`), not a gear.
- **No modal noise.** No confirmation dialogs, no toasts-as-decoration, no spinners used as ornament. Loading is a thin unobtrusive bar, nothing more.
- **Instant.** Filter-as-you-type with zero perceptible lag. Thumbnails load async; the UI never blocks.

## Layout
- Floating bar: a small framed card centered **above** the card strip, detached from it — the only chrome besides the cards. It carries `wallfliper` in bold with the live `/<query>` typed inline next to it (white + `|` cursor while editing, grey when a filter persists, hidden when idle). Future features (filters, source tabs once Wallhaven/Lively backends exist) dock here. Nothing else — no gear.
- No panel: the cards and the bar float directly on the desktop. The wallpaper count and current-folder path are not shown anywhere.
- Body: a **horizontal carousel of sheared portrait wallcards** — a centered band (~85% of the screen wide, ~40% tall, side margins), noctalia-wallcards style. Idle cards lean as uniform parallelograms (content shears with the card; edges stay parallel, so the slats read as a packed strip). The user scrolls sideways (`w/a/s/d`, `h/j/k/l`, arrows, wheel). This is the hero. Filenames are hidden by default (a future opt-in Settings toggle); the wallpaper is the label.
- Focus: the focused card **straightens** and gets a white outline **instantly** (immediate feedback), then — after a short settle delay so scrubbing fast through cards doesn't thrash — smoothly **widens to the wallpaper's own aspect ratio**, so the whole image is visible, corners included (never cropped; ultrawide art letterboxes instead of overflowing the screen). This deliberate, delayed widen is the *one* allowed motion beyond the straighten: it's slow, gated on a delay, and only ever the single focused card. Still no drop shadows, no per-card hover bounce, no card lift on plain mouseover.
- Search: lives **in the top prompt** next to the app name — no box, no icon, no bottom bar. Same modal behaviour (`/` enters, Esc/`/` cancels, Enter/arrow confirms). `/config` + Enter is a command, not a query: it opens the settings overlay.
- Filters: **removed from the chrome.** The image/video toggle icons are gone for a cleaner front. Kind filtering is an exclusive mode (one at a time, not independent toggles): `i` shows images only, `v` videos only, `e` everything; each is idempotent (re-pressing the active mode is a no-op). No on-screen indicator yet (revisit if filtering returns as a first-class affordance).
- Selection colour: white outline only — the same white-selection language everywhere.
- Settings: typing `/config` in search opens a small centered overlay card (not a separate window), dimming the carousel behind it. It's *user-invoked*, so it's not "modal noise" — but it stays flat/monochrome/keyboard-first like everything else, and exists to expose the few real knobs (wallpaper folder) plus light maintenance later, never to add chrome to the main view. Backdrop darkness is fixed (see hard rules), not a knob. Laid out as a TUI list: a header, then `label` rows with the value alongside.

## Anti-goals
- Not Electron. Not a "modern app" look. Not Material. Not rounded-everything.
- No onboarding, no splash screen, no branding moment.

If a proposed change makes it look more like a polished consumer app and less like a fast keyboard tool, it's wrong.
