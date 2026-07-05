# Wallfliper — Design Principles

North star: a **TUI/yazi-style** front. The wallpapers are the content; the UI disappears.
When in doubt, remove. Every pixel of chrome must justify itself.

## Hard rules (non-negotiable)
- **Framed, not decorated.** A 2px solid white border outlines the panel — the same frame language as the author's rofi/yoink setup. No title bar, no window controls, no menu bar.
- **Dark, flat, monochrome.** No gradients, no drop shadows, no glassmorphism, no skeuomorphism. Selection/focus is white; everything else is a shade of grey. The backdrop is fixed near-black `rgba(5,5,5,0.95)` (matches the system rofi theme) — not a setting.
- **Sharp corners only.** Every `Rectangle` is square (no `radius`). Not a setting — there's no "round" alternative anywhere in the UI.
- **Black & white only.** The entire palette is greyscale: near-black background, dark-grey surface, grey muted text, white primary text/selection. **No color** — no blue, no accent hue, nothing tinted. The wallpapers are the only color on screen.
- **Keyboard-first.** Every action reachable without a mouse. Mouse is optional, never required. Modal, like yazi/vim: in *normal* mode `w/a/s/d` or `h/j/k/l` + arrows move (WASD by preference, vim keys and arrows also work), `Space` applies-and-stays (audition the wallpaper live on the desktop), `Enter` applies-and-exits, `/` opens search; in *search* mode every printable key (incl. `w/a/s/d` and space) filters so any filename is typable, while arrows navigate results (not the query). Leaving search has two flavors: `Enter`, an arrow key, or clicking a result **confirms** the filter (drops back to normal nav, query kept); `Esc`, `/` again, or clicking empty app chrome **cancels** it (clears the query, full grid returns). `Esc` in normal mode exits the app instantly.
- **Typography over iconography.** Monospace everywhere: JetBrainsMono Nerd Font when installed, falling back to the fontconfig generic `monospace` (never a hard font dependency). No icons in the chrome at all — even settings is a typed command (`/config`), not a gear.
- **No modal noise.** No confirmation dialogs, no toasts-as-decoration, no spinners used as ornament. Loading is a thin unobtrusive bar, nothing more.
- **Instant.** Filter-as-you-type with zero perceptible lag. Thumbnails load async; the UI never blocks.

## Layout
- Top prompt: `wallfliper` in bold top-left, rofi-style, with the live `/<query>` typed inline next to it (white + `|` cursor while editing, grey when a filter persists, hidden when idle). Nothing else up here — no gear, no source label (text tabs return once Wallhaven/Lively backends exist), no filter toggles. Thin, recedes.
- Framing: the panel hugs the content — small inner padding, no fat margin between the card frame and the cards. The wallpaper count and current-folder path are not shown anywhere; the bottom line carries only the search hint and a transient apply confirmation.
- Body: a **horizontal carousel of portrait wallcards** — a few read at once, the user scrolls sideways (`w/a/s/d`, `h/j/k/l`, arrows, or hover). This is the hero. Filenames are hidden by default (a future opt-in Settings toggle); the wallpaper is the label.
- Focus: the focused card gets a white outline **instantly** (immediate feedback), then — after a short settle delay so scrubbing fast through cards doesn't thrash — smoothly **widens** from portrait into a landscape card, so its wallpaper (almost always 16:9) reads clearly. This deliberate, delayed widen is the *one* allowed motion on a card: it's slow, gated on a delay, and only ever the single focused card. Still no drop shadows, no per-card hover bounce, no card lift on plain mouseover.
- Search: lives **in the top prompt** next to the app name — no box, no icon, no bottom bar. Same modal behaviour (`/` enters, Esc/`/` cancels, Enter/arrow confirms). `/config` + Enter is a command, not a query: it opens the settings overlay.
- Filters: **removed from the chrome.** The image/video toggle icons are gone for a cleaner front. Kind filtering is an exclusive mode (one at a time, not independent toggles): `i` shows images only, `v` videos only, `e` everything; each is idempotent (re-pressing the active mode is a no-op). No on-screen indicator yet (revisit if filtering returns as a first-class affordance).
- Selection colour: white outline only — the same white-selection language everywhere.
- Settings: typing `/config` in search opens a small centered overlay card (not a separate window), dimming the carousel behind it. It's *user-invoked*, so it's not "modal noise" — but it stays flat/monochrome/keyboard-first like everything else, and exists to expose the few real knobs (wallpaper folder) plus light maintenance later, never to add chrome to the main view. Backdrop darkness is fixed (see hard rules), not a knob. Laid out as a TUI list: a header, then `label` rows with the value alongside.

## Anti-goals
- Not Electron. Not a "modern app" look. Not Material. Not rounded-everything.
- No onboarding, no splash screen, no branding moment.

If a proposed change makes it look more like a polished consumer app and less like a fast keyboard tool, it's wrong.
