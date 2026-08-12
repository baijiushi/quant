# oversell Design Direction

## Mode and visual world

This is an **Operate-mode financial research workbench**, not a marketing dashboard. The visual direction is a light "research terminal": calm mineral surfaces, deep ink text, restrained teal status color, compact controls, and dense tabular data. It should feel authored and dependable rather than glossy or playful.

## Hierarchy

- Persistent left navigation owns product-level movement: selection and backtest.
- A compact command header shows the page purpose, current strategy/data mode, run state, and primary actions.
- Run parameters live in a right-side drawer so configuration does not permanently consume the first screen.
- The selection screen prioritizes the candidate table. Task progress and market risk form a secondary monitoring rail.
- AI, research, and chart are independent workspaces, not cards stacked beneath the candidate table.

## Foundation

- Primary: `#087f78` teal for committed actions and healthy progress.
- Ink: `#132f33` for headings; `#29484d` for body; `#526c71` for secondary text.
- Canvas: `#edf2f1`; surface: `#ffffff`; quiet surface: `#f5f8f7`.
- Risk: `#c43d3d`; warning: `#a86814`; information: `#245f8f`.
- Borders carry most separation. Use shadows only for floating navigation, drawers, and active overlays.
- Radius system: 6px for controls, 10px for internal groups, 14px for major surfaces. Pills are reserved for tags and statuses.

## Typography and data

- IBM Plex Sans is bundled for Latin text and numerals; Chinese falls back to Noto Sans SC / Microsoft YaHei.
- IBM Plex Mono is used only for logs, identifiers, dates, codes, and measurements.
- Candidate tables use tabular numerals, sticky headers, restrained row hover, and a visible selected state.
- The desktop body baseline is 15px. Dense tables may use 13px and helper text may use 12px, but no primary control or decision value should fall below that floor.
- Headings use a compact scale; no decorative eyebrow labels, giant metric heroes, or gradient text.

## Interaction

- The primary run action is always visible in the command header and repeated at the end of the settings drawer.
- Choosing a network data mode must make its cost and expected duration obvious before running.
- Candidate rows are keyboard accessible and open the corresponding chart/entry workspace.
- The K-line chart reserves enough vertical space for price and volume. `Shift + wheel` over the price grid, or a plain wheel over the price axis, zooms the Y axis around the pointer; double-click restores automatic range.
- Logs expand when a task is running or failed and can be collapsed after success.
- Loading, empty, disabled, error, cancellation, and restored-background-task states are all explicit.
- Motion is restrained: one short workspace reveal and functional progress transitions only.

## Responsive behavior

- Desktop: 216px global sidebar, full-width work area, candidate content plus monitoring rail.
- Tablet: sidebar becomes a compact top rail; run workspace becomes one column.
- Mobile: navigation and workspace tabs scroll horizontally, drawers use full width, and tables retain horizontal scrolling rather than hiding financial fields.
