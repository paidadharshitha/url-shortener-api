---
name: Shortlink
description: A working link notebook with white sheets, green ink, and a compact ledger.
colors:
  primary: "#176448"
  primary-hover: "#104d36"
  primary-soft: "#e8f3ec"
  canvas: "#f5f8f6"
  surface: "#fff"
  ink: "#1d3028"
  muted: "#62716a"
  line: "#dce5df"
  field-line: "#cedbd3"
  row-hover: "#fbfdfb"
  row-selected: "#f0f7f2"
  chart-bar: "#a9ceba"
  danger: "#a33232"
  danger-soft: "#fff2f0"
  active-ink: "#28613c"
  active-soft: "#edf6ef"
  paused-ink: "#865819"
  paused-soft: "#fff4df"
  expired-ink: "#646c66"
  expired-soft: "#f1f2f1"
typography:
  display:
    fontFamily: "Manrope, Arial, sans-serif"
    fontSize: "30px"
    fontWeight: 750
    lineHeight: 1.25
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Manrope, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 750
    lineHeight: 1.4
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Manrope, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    lineHeight: 1.6
  body:
    fontFamily: "Manrope, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Manrope, Arial, sans-serif"
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1.6
  button:
    fontFamily: "Manrope, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    lineHeight: 1.6
  address:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace"
    fontSize: "10px"
    fontWeight: 400
    lineHeight: 1.6
  metric:
    fontFamily: "Manrope, Arial, sans-serif"
    fontSize: "27px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "-0.025em"
rounded:
  panel: "14px"
  control: "8px"
  field: "7px"
  badge: "6px"
  status: "5px"
  toast: "9px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  section: "24px"
  block: "32px"
  desktop-gutter: "48px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "9px 15px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.surface}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "9px 15px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "9px 15px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "10px 12px"
    width: "100%"
  navigation-current:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.primary}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  status-active:
    backgroundColor: "{colors.active-soft}"
    textColor: "{colors.active-ink}"
    rounded: "{rounded.status}"
    padding: "4px 7px"
  status-paused:
    backgroundColor: "{colors.paused-soft}"
    textColor: "{colors.paused-ink}"
    rounded: "{rounded.status}"
    padding: "4px 7px"
  status-expired:
    backgroundColor: "{colors.expired-soft}"
    textColor: "{colors.expired-ink}"
    rounded: "{rounded.status}"
    padding: "4px 7px"
  composer:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "25px"
  ledger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
  detail-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "22px"
  toast:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.toast}"
    padding: "12px 20px"
---

# Design System: Shortlink

## Overview

**Creative North Star: "A working link notebook"**

White sheets sit on a cool pale-green desk. Green ink identifies actions, selected navigation, and short addresses; Manrope keeps the workspace clear and compact. Thin rules and stable alignment give the interface the feel of an orderly notebook.

The system supports repeated creation, scanning, and inspection. It uses restrained surface boundaries, direct labels, and brief state changes. The dashboard's particular composition is recorded in `.impeccable/surfaces/dashboard.md`; this document records the implemented visual vocabulary.

**Key Characteristics:**
- White panels on a pale-green canvas.
- Green action ink and warm amber for paused links.
- Manrope for interface text; monospace for short addresses.
- Flat, bordered surfaces with compact desktop density.
- Readable stacked records on phones.

## Colors

The palette is cool and quiet, with green connecting navigation, action, and link identity. The frontmatter is the normative token inventory; the source is `app/static/styles.css`.

### Primary

- **Ink Green** (`primary`): primary actions, links, active navigation text, focus outlines, and the latest chart bar.
- **Deep Ink Green** (`primary-hover`): the primary button's hover state.
- **Washed Green** (`primary-soft`): selected navigation and small section-icon grounds.
- **Chart Sage** (`chart-bar`): historical bars, with green ink used for hover and the latest day.

### Neutral

- **Desk Green** (`canvas`): page ground and quiet hover areas.
- **Sheet White** (`surface`): rail, panels, inputs, and secondary buttons.
- **Notebook Ink** (`ink`): primary text and toast background.
- **Soft Graphite Green** (`muted`): secondary copy, metadata, placeholders, and navigation labels.
- **Sheet Rule** (`line`) and **Field Rule** (`field-line`): surface divisions and input edges.
- **Page Hover** (`row-hover`) and **Selected Sheet** (`row-selected`): hover and selected record backgrounds.

Active, paused, and expired status tokens pair a tinted ground with a darker text color. Error text uses `danger` on `danger-soft`. Status names remain visible alongside the colored dot.

**The Status Words Rule.** Keep the status word and dot together; color alone does not convey link state.

## Typography

**Display and Body Font:** Manrope, with Arial and sans-serif fallbacks. The local variable font at `app/static/manrope.ttf` supplies weights from 200 to 800 with `font-display: swap`.

**Address Font:** ui-monospace, SFMono-Regular, Consolas, monospace.

The hierarchy uses a compact size range, strong weights for task anchors, and slightly tightened headings. The display role is the page heading; it is not a separate decorative typeface. The frontmatter captures the desktop defaults.

- Page headings use `display`; phone headings reduce to 27px.
- Section headings use `headline`, with local sizes of 15–19px. The composer heading is 16px, then 14px on phones and 13px at the narrowest breakpoint.
- Link titles use `title` on desktop and increase to 13px on phones.
- Body text uses `body`; supporting copy commonly uses 10–13px. Form labels use `label`.
- Short addresses use `address` in the desktop ledger, 11px in phone records and detail panels, and 12px in the created-link result. The phone result uses 10px.
- Metric values use `metric`, reducing to 25px on phones. Counts and dates use tabular numerals where implemented.

**The Address Voice Rule.** Reserve the monospace stack for short addresses and code; keep interface prose in Manrope.

## Layout

The desktop rail is fixed at 204px. The main region has a matching left margin, a maximum width of 1720px, and padding of 44px 48px 26px. The summary is a four-column strip separated by rules. The composer and chart use a `minmax(0, 1.4fr) minmax(250px, 1fr)` grid with a 36px gap. At widths of at least 1700px the chart's minimum column increases to 300px.

The ledger occupies the available width. Opening details creates a 335px side column separated by a 20px gap; the created-date column hides while that side panel is present. Desktop cells use 17px 22px padding, reduced to 15px horizontal padding beside details.

| Viewport condition | Implemented behavior |
| --- | --- |
| Up to 1250px | Rail becomes 184px; main padding becomes 34px 30px 24px; composer/chart gap becomes 24px; composer action wraps below its input; details become 310px wide. |
| Up to 1000px | Rail becomes 166px; main padding becomes 30px 24px; composer and chart stack; composer input and action share a row again; details move above the ledger; list controls move below the heading. |
| Up to 700px | Rail becomes a compact top bar; main margin resets to zero with 28px 20px 20px padding; metrics form two columns; composer input and action stack; custom fields form one column; details stay above the ledger. |
| Up to 360px | Main and top-bar side gutters become 14px; navigation labels use 10px; search and status filter occupy separate full-width rows. |

At the phone breakpoint, each table record becomes a three-column grid with the link content spanning the full first row. Status, click count, and copy action follow below. Records have 16px padding and gaps of 8px vertically and 16px horizontally. Titles and addresses wrap anywhere; created dates are hidden. The column headings remain available to assistive technology.

## Elevation & Depth

Panels and controls are flat at rest. White against the pale canvas, one-pixel borders, selected-row tint, and divider rules establish depth. The toast is the only shadow-bearing component: `0 6px 24px #153e2626`.

Created results reveal over 0.3s, detail panels over 0.28s, and toasts over 0.2s with `cubic-bezier(0.16, 1, 0.3, 1)`. Each reveal moves from 6px below while opacity rises from 0.75 to 1. Buttons change background and border color over 0.16s; the custom-options chevron rotates over 0.2s. The loading spinner rotates over 0.8s with linear timing. Reduced-motion preference disables all animations and transitions and switches programmatic detail scrolling to immediate movement.

**The Flat Sheet Rule.** Keep regular panels free of drop shadows; reserve the existing shadow treatment for transient toast feedback.

## Shapes

The frontmatter names the existing radius roles: panels, controls, fields, badges, statuses, and toasts. Larger sheets have the softest corners; compact status tags have tighter corners. Borders remain one pixel. Status and connection dots are circular. The brand mark uses a 10px radius and a fixed −8-degree rotation; it is a small identity detail rather than a general card treatment.

Icons use unfilled, rounded strokes at 1.7 units, with 20px default dimensions and smaller control-specific sizes. Charts use vertical bars with a 3px corner radius.

## Components

### Buttons

Compact, strong labels distinguish actions without added depth. Primary, secondary, and quiet variants share the frontmatter padding and control radius. The default minimum height is 40px; the desktop composer action is 44px, the phone composer action is 43px, and compact pagination actions are 32px. Secondary and quiet buttons hover to `#edf3ef` with a `#b9d0c2` border. Primary hover uses its dedicated token. Disabled buttons use opacity 0.45 and a not-allowed cursor.

Keyboard focus uses a 3px primary-color outline offset by 4px. Icon actions are 34px square with a 7px radius; phone row actions are 36px square. Descriptive accessible names accompany icon-only actions.

### Inputs / Fields

White fields have a one-pixel field-rule border, 12px type, and a minimum height of 42px. Their border and caret turn green on focus; the global focus-visible outline remains. Placeholders use the muted token at full opacity. Errors appear beside the relevant form action, with danger text on a pale error ground, 12px 14px padding, and a 7px radius.

Search includes an inset icon and uses 36px left padding on desktop, reducing to 30px on phones. Search and status-filter controls have a 39px minimum height.

### Navigation

The desktop index uses 13px semibold labels, 10px 12px padding, and a 7px vertical gap. Active navigation uses green ink on washed green; hover uses the canvas ground and notebook ink. At the phone breakpoint the index becomes horizontal tabs, with 6px top corners, a 2px gap, and 10px 7px padding.

### Chips

Status tags pair a 4px circular dot with 10px semibold text, increasing to 11px on phones. Their visual variants are active, paused, and expired. The list count badge is a separate white, bordered shape with 10px tabular text and 1px 7px padding.

### Cards / Containers

Composer, ledger, and detail surfaces share white fill, the panel radius, and a one-pixel sheet-rule border. Composer padding starts at 25px, becomes 22px at 1250px, and 20px on phones. Details use 22px padding, reduced to 20px on phones. The ledger handles spacing through its cells and pagination rather than a second padded wrapper.

### Link Ledger and Details

Desktop records align link identity, status, click count, created date, and actions in a table. Hover and selection use their respective surface tints. Phone records use the stacked layout specified above. A title button opens the inline detail panel; closing it restores focus to the triggering action, or to search when that action no longer exists. Escape also closes the panel. A successful creation moves focus to the new link's copy action.

### Charts and Feedback

The click chart uses a 360 × 126 SVG view box, sage bars, three dashed grid lines, and compact Manrope labels. The latest bar and label use green ink. Chart values come from the API; empty activity preserves the chart's real zero state and explains the next useful action. Each chart has an accessible text summary and bars have native titles.

Toasts are compact ink-colored messages with white text, the dedicated shadow, and polite live-region semantics. Their desktop horizontal position accounts for the rail; phones center them 16px above the bottom edge.

## Do's and Don'ts

### Do:
- **Do** reuse the green action palette, white sheets, and pale canvas.
- **Do** keep phone link titles and addresses readable with wrapping and the implemented 13px/11px sizes.
- **Do** preserve visible keyboard focus, focus restoration, and immediate reduced-motion updates.
- **Do** pair status colors with words and show real API values in charts and counts.

### Don't:
- **Don't** add shadows to regular panels or use decorative gradients to separate sections.
- **Don't** squeeze the desktop table columns into a phone-width grid.
- **Don't** use monospace for navigation, headings, or supporting prose.
- **Don't** replace task controls or honest empty states with ornamental metric tiles or simulated chart data.
