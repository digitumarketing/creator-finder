# Digitum Markdown Viewer — deploy notes

This folder is a complete static site. Deploy it as-is, `index.html` at the root.

## Brand

Styled to the Digitum Pixel-stem brand kit (v1.0, September 2026):

| Token | Value | Used for |
| --- | --- | --- |
| Lime | `#C8FF00` | Accent: primary buttons, active states, highlights, the icon |
| Charcoal | `#272320` | Text on light, the dark theme surface, the icon tile |
| Cream | `#EFEAE1` | The light theme surface, text on dark |

One family, Outfit, for headings (600/700), body (400) and labels (500/600).
JetBrains Mono stays for code and the editor. Lime is only ever an accent on
cream, never text; in the dark theme it is also used for links.

There is a light and a dark theme. The sun / moon button in the top bar flips
between them and remembers the choice; without a choice the OS setting wins.
The logos in `brand/` are the supplied artwork, resized uniformly: the charcoal
wordmark on light, the lime and cream wordmark on dark.

## Cloudflare Pages / Wrangler

```
wrangler pages deploy .
```

Deploy from inside this folder so `index.html`, `manifest.json`, `sw.js` and
`icons/` all land at the domain root. Paths in `manifest.json` are root
relative (`/icons/...`, `/sw.js`), so this only works cleanly if the site is
served from a domain or subdomain root, not a subpath.

## What "Install app" and file handling need

- HTTPS. Cloudflare Pages gives this automatically.
- Chrome, Edge, or another Chromium browser on Windows, macOS, ChromeOS, or
  Android. Once installed, the OS lets people double-click a `.md` file, or
  right-click → Open with → Digitum Markdown Viewer, and it opens straight
  into the app via the File Handling API.
- Safari and Firefox don't support the file handler part yet. On iOS,
  "Add to Home Screen" still works and gives an app icon and standalone
  window, just not the OS-level file association.

### How the install prompt shows up

The install call to action lives in the page body (so it works on phones where
the sidebar is off screen) and in the sidebar. It adapts per platform:

| Platform | Button | What happens |
| --- | --- | --- |
| Chromium desktop / Android | Install app | Uses the browser's own install dialog when `beforeinstallprompt` has fired. If it hasn't, the button opens instructions for the browser menu route plus a live checklist of the installability requirements. |
| iPhone / iPad (any browser) | Add to Home Screen | Opens instructions for the Share menu. WebKit has no install prompt API. |
| macOS Safari | Add to Dock | Opens instructions for File → Add to Dock. |
| Firefox | nothing | Firefox has no install support, so nothing is offered. |
| Already installed | nothing | Detected via `display-mode: standalone` and `navigator.standalone`. |

Chrome does not fire `beforeinstallprompt` on the first paint. It waits for
its own engagement heuristics, so the address bar install icon can take a
while to appear. That's why the menu route and the checklist exist: they work
immediately and tell you whether the deploy is actually missing a file.

The checklist verifies HTTPS, that `manifest.json` and `icons/icon-512.png`
are reachable, and that a service worker is registered. If a row is red, that
file didn't make it into the deploy.

There is no install icon in the iOS address bar, in any browser. Every iOS
browser is required to use Apple's WebKit engine, which has no install
prompt API. Installing on iOS is always manual through Share →
Add to Home Screen.

## A CSS gotcha worth remembering

The stylesheet contains `[hidden]{display:none !important}` on purpose.
Elements toggled with the `hidden` attribute (`#resume`, `#installCta`,
`#doc`, `#installHelp` and others) also carry classes that set `display`,
and a class beats the browser's built in `[hidden]` rule. Without that one
line, hidden elements render anyway, which showed up as an empty
"Last session" card and an install button that did nothing. If you add a new
element that is toggled with `hidden`, this rule already covers it.

## Cache behaviour after a redeploy

`sw.js` is network-first for page loads, so a fresh deploy is picked up on the
next reload rather than being masked by the cache. Static assets are
cache-first with a background refresh. If a change still doesn't appear, bump
`CACHE` in `sw.js` (currently `digitum-md-viewer-v5`) to force old caches out.

## Editing and saving

The pencil button in the top bar turns the reading view into a two pane
editor: markdown source on the left, live preview on the right. Below 1000px
the panes stack and Edit / Preview tabs appear in the top bar. `Ctrl E`
toggles editing, `Esc` leaves it, `Ctrl S` saves immediately.

### Formatting toolbar

Above the source sits a toolbar: undo and redo, a Heading menu (normal text and
H1 to H6) with quick H1, H2 and H3 buttons, bold, italic, strikethrough, inline
code, link, image, bulleted, numbered and checklist lists, quote, code block,
table and divider. Buttons toggle, so pressing Bold on bold text removes it,
and pressing the current heading level turns the line back into normal text.
Formatting goes through the browser's own editing commands, so `Ctrl Z` undoes
it like typing.

Shortcuts: `Ctrl B` bold, `Ctrl I` italic, `Ctrl K` link, `Ctrl Shift X`
strikethrough, `Ctrl Shift E` inline code, `Ctrl Alt 1`–`6` heading,
`Ctrl Alt 0` normal text, `Ctrl Shift 7` / `8` / `9` numbered, bulleted and
checklist, `Ctrl Shift .` quote. `Enter` in a list starts the next item (and
ends the list on an empty one), `Tab` / `Shift Tab` indent and outdent.

### Selection sync between source and preview

With "Sync selection" ticked (the default):

- Selecting text in the source highlights exactly that text in the preview,
  and scrolls the preview to it. Just moving the cursor marks the block it is in.
- Selecting text in the preview selects the matching source in the editor,
  painted in lime, and scrolls the source to it. A click in the preview puts
  the cursor at that spot in the source.
- Formatting applies to either selection, so you can select words in the
  preview and press Bold or `Ctrl B`.
- Ticking a checklist box in the preview ticks it in the source.

How it works: the preview is rendered block by block from marked's tokens, and
every block carries `data-b` pointing at its range in the source. Inside a
block, rendered characters are aligned to source characters, which skips
markup such as `**`, `#`, list markers and link URLs. The editor's lime
highlight is a mirror layer behind the transparent textarea, since a textarea
cannot colour part of its text. The preview highlight uses the CSS Custom
Highlight API; browsers without it get a mark beside the whole block.

### Saving

How a file saves depends on how it was opened:

| How the file was opened | What saving does |
| --- | --- |
| "Open files" or "Open folder" in Chrome or Edge | Writes straight back to the file on disk, automatically about a second after you stop typing. The browser asks for write permission the first time. |
| Dropped in, or opened in Safari or Firefox | No write access exists, so "Save a copy" is the only route. The status reads "Copy only" from the moment you start editing. |
| Loaded from a link, or pasted in | Same as above, copy only. |

"Save a copy" uses the browser's save dialog where available. Once a copy is
saved that way, the document is backed by the new file and autosave takes
over from there.

The status chip shows Saved, Saving, Unsaved or Copy only. On very narrow
screens it shrinks to a coloured dot with the wording in its tooltip.

Two safeguards are worth knowing about. If the file changed on disk after you
opened it, saving asks whether to overwrite it or reload the newer version,
rather than silently clobbering someone else's work. And closing a document
or the browser tab with unsaved copy-only edits prompts first.

## Navigating open files

Opening files adds them to "Open now" in the sidebar. Each row has a × to
close it, and a "Close all" appears once more than one is open. The top bar
has a × for the file currently on screen, and the app name doubles as a
button that returns to the start screen without closing anything.

Closing only drops a document from the current session. It stays in Recent,
so it can be reopened later, from disk where the browser supports file
handles, otherwise from the saved copy.

Shortcuts: `Ctrl O` open files, `/` search, `[` and `]` previous or next
file, `Ctrl E` edit, `Ctrl S` save, `Esc` back to the start screen, `Ctrl P`
save as PDF. There is
deliberately no Ctrl W shortcut, since browsers reserve it for closing the
tab and a page cannot intercept it.

On screens under 861px the sidebar slides over the content and a backdrop
appears behind it. Tapping the backdrop or pressing `Esc` closes it. Close
buttons are always visible on touch devices rather than appearing on hover,
and their tap targets grow to 32px.

## Re-generating icons

Source is the Digitum pixel-stem glyph in lime `#C8FF00`, centred on a solid
charcoal `#272320` tile. All icons are
saved without an alpha channel, since some launchers render transparent PNGs
on an unpredictable background.

- `icon-16` … `icon-512` — purpose `any`. Glyph fills about 62% of the canvas.
  The full ladder exists so Windows taskbar, macOS Dock, Android launchers and
  browser tabs each get a sharp source instead of a downscaled 512.
- `maskable-192`, `maskable-512` — purpose `maskable`. Glyph fills only about
  44% of the canvas so it survives being cropped to a circle or squircle.
  Verified: the furthest lime pixel sits inside the centre 80% safe zone.
- The favicon and Apple touch icon are inlined into `index.html` as data URIs,
  so the tab icon works even before `icons/` finishes loading.
- The small mark in the top bar next to the app name stays transparent on
  purpose: `brand/mark-charcoal.png` on the light theme, `brand/mark-lime.png`
  on the dark one.

### The installed app keeps the icon it was installed with

Chrome copies the icon into the OS at install time. Changing the manifest does
not update an already installed app. After deploying new icons:

1. Uninstall the app. Chrome desktop: open the app, then its ⋮ menu →
   Uninstall. Or `chrome://apps`, right click → Remove. On Android, uninstall
   like any app. On iOS, delete the Home Screen icon.
2. Hard reload the site so the new `manifest.json` and `sw.js` are picked up.
3. Install again.

If the icon still looks wrong, the earlier install almost certainly happened
while `icons/` wasn't deployed yet, so Chrome fell back to the favicon or a
generated placeholder. The install sheet's checklist confirms whether the
icons are reachable now.
