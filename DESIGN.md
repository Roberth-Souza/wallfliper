# Wallfliper — Design Principles

North star: a **TUI/yazi-style** front. The wallpapers are the content; the UI disappears.
When in doubt, remove. Every pixel of chrome must justify itself.

## Hard rules (non-negotiable)
- **Borderless & frameless.** No title bar, no window controls, no menu bar.
- **Dark, flat, monochrome.** No gradients, no drop shadows, no glassmorphism, no skeuomorphism. Selection/focus is white; everything else is a shade of grey.
- **Sharp corners only.** Every `Rectangle` is square (no `radius`). Not a setting — there's no "round" alternative anywhere in the UI.
- **Black & white only.** The entire palette is greyscale: near-black background, dark-grey surface, grey muted text, white primary text/selection. **No color** — no blue, no accent hue, nothing tinted. The wallpapers are the only color on screen.
- **Keyboard-first.** Every action reachable without a mouse. Mouse is optional, never required. Modal, like yazi/vim: in *normal* mode `w/a/s/d` or `h/j/k/l` + arrows move (WASD by preference, vim keys and arrows also work), `Space` applies-and-stays (audition the wallpaper live on the desktop), `Enter` applies-and-exits, `/` opens search; in *search* mode every printable key (incl. `w/a/s/d` and space) filters so any filename is typable, while arrows navigate results (not the query). Leaving search has two flavors: `Enter`, an arrow key, or clicking a result **confirms** the filter (drops back to normal nav, query kept); `Esc`, `/` again, or clicking empty app chrome **cancels** it (clears the query, full grid returns). `Esc` in normal mode exits the app instantly.
- **Typography over iconography.** Prefer crisp text labels and a monospace/clean sans. Icons only where a word can't be shorter (gear = settings). No icon zoo.
- **No modal noise.** No confirmation dialogs, no toasts-as-decoration, no spinners used as ornament. Loading is a thin unobtrusive bar, nothing more.
- **Instant.** Filter-as-you-type with zero perceptible lag. Thumbnails load async; the UI never blocks.

## Layout
- Top bar: just the settings gear, top-left. Nothing else — the `Local` source label is gone while it's the only source (it returns as text tabs once Wallhaven/Lively backends exist). No search box, no filter toggles here. Thin, recedes.
- Framing: the panel hugs the content — small inner padding, no fat margin between the card frame and the cards. The wallpaper count and current-folder path are not shown anywhere; the bottom line carries only the search hint and a transient apply confirmation.
- Body: a **horizontal carousel of portrait wallcards** — a few read at once, the user scrolls sideways (`w/a/s/d`, `h/j/k/l`, arrows, or hover). This is the hero. Filenames are hidden by default (a future opt-in Settings toggle); the wallpaper is the label.
- Focus: the focused card gets a white outline **instantly** (immediate feedback), then — after a short settle delay so scrubbing fast through cards doesn't thrash — smoothly **widens** from portrait into a landscape card, so its wallpaper (almost always 16:9) reads clearly. This deliberate, delayed widen is the *one* allowed motion on a card: it's slow, gated on a delay, and only ever the single focused card. Still no drop shadows, no per-card hover bounce, no card lift on plain mouseover.
- Search: a minimal `/  search` hint **attached to the bottom edge**, centered — no box, no icon; it becomes the live `/<query>` once `/` is pressed. Same modal search behaviour as before (`/` enters, Esc/`/` cancels, Enter/arrow confirms).
- Filters: **removed from the chrome.** The image/video toggle icons are gone for a cleaner front. Kind filtering is an exclusive mode (one at a time, not independent toggles): `i` shows images only, `v` videos only, `e` everything; each is idempotent (re-pressing the active mode is a no-op). No on-screen indicator yet (revisit if filtering returns as a first-class affordance).
- Selection colour: white outline only — the same white-selection language everywhere.
- Settings: the gear opens a small centered overlay card (not a separate window), dimming the carousel behind it. It's *user-invoked*, so it's not "modal noise" — but it stays flat/monochrome/keyboard-first like everything else, and exists to expose ricing knobs (backdrop darkness, folder) plus light maintenance (clear orphaned cache), never to add chrome to the main view. Laid out as a TUI list: a section header + rule, then `label:` rows with the value right-aligned and a `>` chevron on rows that open something.

## Anti-goals
- Not Electron. Not a "modern app" look. Not Material. Not rounded-everything.
- No onboarding, no splash screen, no branding moment.

If a proposed change makes it look more like a polished consumer app and less like a fast keyboard tool, it's wrong.
