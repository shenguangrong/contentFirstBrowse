# contentFirstBrowse (Browse Mode Content First)

`contentFirstBrowse` is an NVDA global plugin that optimizes speech order in browse mode.

When moving the caret in browse mode, the plugin reads text content first, then role/state information. This reduces interruptions caused by "control info first, content later".

## Features

- Enables a "content first" speech order in browse mode.
- Enabled by default after installation.
- Can be enabled or disabled from the "Browse mode content first" category in NVDA Settings.
- Only affects qualified browse mode caret movement, without changing behavior in other scenarios.

## When It Applies

The plugin is active only when all conditions are met:

- Output reason is `CARET` (caret movement speech).
- The current text info object belongs to browse mode.
- Focus mode is off (`passThrough = False`).
- The movement was not triggered by a focus change.

In all other cases, it falls back to NVDA's original speech logic.

## Compatibility

- Plugin version: `1.1.1`
- Minimum NVDA version: `2025.1`
- Last tested NVDA version: `2026.1`

## Usage

- Open the NVDA menu, then choose Preferences and Settings.
- Select "Browse mode content first" from the category list.
- Check or clear "Enable content-first speech in browse mode", then choose Apply or OK.
- This plugin has no shortcuts.
- In browse mode, move with arrow keys (or other reading commands) to hear content first.

## Known Limitations

- The plugin hooks NVDA internal speech functions by patching.
- If NVDA changes related internals in future versions, the plugin may need updates for compatibility.

## Author

- Shen Guangrong
