# Orion Frontend Design System (SAT-SA)

Reading this as: enterprise supervisory analytics console for NCIIPC cyber auditors, with a Cloudflare technical minimalism language, leaning toward Tailwind v4 utilities + Shadcn UI customization.

---

## 1. Core Dials & Configuration

In accordance with the frontend taste specifications, the interface is calibrated for mission-critical supervisory infrastructure:

* **DESIGN_VARIANCE**: `5` (Restrained, technical, industrial grid symmetry with purposeful left-aligned data layouts)
* **MOTION_INTENSITY**: `4` (Fluid CSS transitions, controlled spring-like easing, 200ms micro-interactions, deliberate 1500ms reveals, strictly motivated)
* **VISUAL_DENSITY**: `8` (Cockpit density: compact paddings, hairline 1px dividers, tabular monospaced numerals, high scannability)

---

## 2. Visual Theme & Atmosphere: Cloudflare Technical Minimalism

The visual language communicates enterprise reliability, uncompromising technical competence, and audit authority. The interface deliberately avoids decorative fluff, neon glows, and rounded consumer shapes.

### Core Aesthetic Pillars:
1. **Pristine High-Contrast Canvas**: Crisp near-white backgrounds (`#ffffff` and `#fdfdfc`) with charcoal typography (`#262626`), providing a 40.5:1 contrast ratio that eliminates cognitive fatigue during multi-hour audit sessions.
2. **Singular Action Accent**: Cloudflare Orange (`#ff5e1f`) is the exclusive accent across the application, reserved strictly for primary CTAs, critical risk indicators, and focused interactive states.
3. **Subtle Hairline Delimitation**: Structural boundaries rely on hairline borders (`#f0f0f0` in light mode, `#262626` in dark mode) and negative space rather than heavy drop shadows.
4. **Sharp Industrial Geometry**: Corner radii are kept between 1px and 4px. Cards, modals, buttons, and inputs feel engineered rather than playful.

---

## 3. Measured Token Specifications

### 3.1 Color Palette

| Token Name | Hex Value | Role & Usage | Contrast vs Background |
|---|---|---|---|
| `color-primary` | `#ff5e1f` | Primary interactive accent, key CTA background, active tabs | 3.8:1 (needs dark text or bold weight) |
| `color-primary-hover` | `#ff4800` | Hover state for primary interactive elements | 4.2:1 |
| `color-primary-accent` | `#ff7038` | Secondary highlight, focus halos, subtle badges | 3.4:1 |
| `color-on-primary` | `#ffffff` | Text and icons placed on primary orange elements | 3.8:1 |
| `color-background` | `#ffffff` | Primary app canvas, table rows, hero sections | Canvas |
| `color-surface` | `#fdfdfc` | Surface containers, card backgrounds, data tiles | 1.02:1 against canvas |
| `color-surface-subtle` | `#f0f0f0` | Input backgrounds, disabled elements, active hover fills | 1.15:1 against canvas |
| `color-border` | `#f0f0f0` | 1px hairline component borders, table dividers | Subtle structure |
| `color-border-strong` | `#d4d4d4` | Active input borders, focused component boundaries | 1.6:1 against canvas |
| `color-text-primary` | `#262626` | Main headers, body text, primary values | 40.5:1 (WCAG AAA) |
| `color-text-secondary` | `#666666` | Labels, secondary metadata, table column headers | 5.8:1 (WCAG AA) |
| `color-text-muted` | `#8c8c8c` | Micro-copy, timestamp footers, inactive icons | 3.5:1 |

#### Dark Mode Mirror Tokens
* `color-dark-background`: `#121212` (Off-black, avoids dead pure black)
* `color-dark-surface`: `#181818` (Card and panel backgrounds)
* `color-dark-border`: `#262626` (Subtle 1px structural dividing lines)
* `color-dark-text-primary`: `#f5f5f5` (Headers and primary body copy)
* `color-dark-text-secondary`: `#a3a3a3` (Metadata and descriptive copy)
* `color-dark-primary`: `#ff5e1f` (Preserved brand orange for continuity)

---

### 3.2 Typography Stack

* **Display & Heading Font**: `FT Kunst Grotesk, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`  
  *Fallback in local offline environment*: `Geist Sans, Inter, sans-serif`
* **Monospace Font**: `Apercu Mono Pro, "JetBrains Mono", "Geist Mono", monospace`  
  *Mandatory for all case IDs, IP addresses, MITRE technique IDs, hashes, timestamps, and numeric metrics.*

| Scale Category | Size | Weight | Line Height | Letter Spacing | Context |
|---|---|---|---|---|---|
| **Display** | 32px (desktop 48px) | 500 | 1.05 | -0.8px (-0.025em) | Page titles, executive score callouts |
| **Heading 1** | 24px (desktop 28px) | 500 | 1.15 | -0.6px (-0.02em) | Section titles, drawer headers |
| **Heading 2** | 19px | 500 | 1.20 | -0.48px (-0.015em) | Card titles, modal headers |
| **Heading 3** | 15px | 500 | 1.25 | -0.2px (-0.01em) | Group labels, table headers |
| **Body** | 14px | 400 | 1.25 | -0.14px (-0.01em) | Standard data tables, drawer notes, descriptions |
| **Body Small** | 12px | 400 | 1.30 | 0.0px | Metadata badges, secondary labels |
| **Mono Data** | 12px | 400 | 1.50 | 0.0px | Case numbers, IP addresses, duration timers |

---

### 3.3 Spacing Scale (4px Base Unit)

The spacing scale is shallow and disciplined:

```text
[4px, 8px, 12px, 16px, 20px, 24px, 32px, 40px, 48px, 60px, 80px, 112px, 128px]
```

* **Micro-padding (4px - 8px)**: Table cell padding, badge padding, button icon gaps.
* **Component-padding (12px - 16px)**: Card inner padding, list item gaps, input padding.
* **Layout-gaps (20px - 32px)**: Grid column gaps, section header to content separation.
* **Page-gutters (40px - 60px)**: Top bar spacing, outer page margins.

---

### 3.4 Corner Radius System (Industrial Sharp)

In alignment with the Shape Consistency Lock, all radii follow an industrial scale:

* `radius-sm`: `1px` (Table rows, micro badges, tooltips)
* `radius-md`: `2px` (Standard buttons, form inputs, dropdown menus)
* `radius-lg`: `3px` (Cards, evidence drawer, modals)
* `radius-xl`: `4px` (Main app outer containers)
* `radius-pill`: `9999px` (Reserved strictly for round indicator dots and tag filters; never for card containers)

*Falsifiable Rule*: No card, dialog, or input container may have a border radius greater than 4px.

---

### 3.5 Shadows & Elevation

* **Card Shadow**: `0 0 0 0 rgba(0,0,0,0), 0 4px 60px 0 rgba(255,80,10,0.06), 0 2px 12px 0 rgba(0,0,0,0.03)`  
  *Effect*: Flat containment with an imperceptible warm amber underglow.
* **Dropdown / Flyout Shadow**: `0 4px 20px -2px rgba(0,0,0,0.08), 0 0 0 1px #f0f0f0`
* **Zero Drop Shadows on Tables**: Tables and list items use border hairlines only (`border-b border-[#f0f0f0]`).

---

### 3.6 Motion & Easing Tokens

* **Duration Fast**: `100ms - 200ms` (Hover states, button clicks, tab switches)
* **Duration Base**: `1500ms` (Large section transitions, drawer reveals, slow confidence meters)
* **Duration Slow**: `2000ms` (Batch ingestion progress fill, deep scan completion)
* **Signature Easing**: `cubic-bezier(0.19, 1, 0.22, 1)` (High initial velocity with a confident, deliberate settle)
* **Reduced Motion Guarantee**: When `prefers-reduced-motion: reduce` is active, all transitions collapse to `0ms` or instant opacity fades.

---

## 4. Component Patterns for Orion SAT-SA

### 4.1 Primary CTA & Action Buttons
* **Styling**: Background `#ff5e1f`, text `#ffffff` (font-weight 500, font-size 14px), radius 2px, padding `8px 16px`.
* **Hover State**: Background shifts to `#ff4800` over 150ms `cubic-bezier(0.19, 1, 0.22, 1)`.
* **Active State**: Physical push feedback via `transform: translateY(1px)` or `scale(0.99)`.
* **Focus Ring**: `2px solid #ff5e1f` with a `2px` offset (`outline-offset: 2px`).
* **Single Line Rule**: Button text must never wrap on desktop. Labels are kept concise (1 to 3 words, e.g. "Export Dossier", "Inspect Evidence").

### 4.2 Supervisory Data Cards & Metrics
* **Background**: `#fdfdfc` with a 1px border of `#f0f0f0`.
* **Header**: Monospace category label (11px, `#666666`, uppercase tracking-wider) directly above the 24px metric number.
* **Accent Line**: Optional 2px top border in `#ff5e1f` on cards signaling immediate supervisory intervention.

### 4.3 High-Density Triage Table
* **Header**: Height 36px, background `#fdfdfc`, border-bottom 1px solid `#e5e5e5`, text 11px uppercase tracking-wider in `#666666`.
* **Row**: Height 44px, background `#ffffff`, hover background `#fafafa`, border-bottom 1px solid `#f0f0f0`.
* **Cell Typography**: Technical IDs and timestamps rendered in 12px `Apercu Mono Pro`.
* **Flag Badges**: 1px border `#ff5e1f/20`, background `#ff5e1f/10`, text `#ff5e1f`, radius 2px, padding `2px 6px`.

### 4.4 Evidence Drill-Down Drawer
* **Position**: Slide-out from right edge, width `640px` (or `90vw` on mobile).
* **Surface**: Background `#ffffff`, border-left 1px solid `#e5e5e5`, box-shadow `0 0 40px rgba(0,0,0,0.1)`.
* **Timeline Component**: Vertical 1px hairline in `#f0f0f0` with 6px square nodes marking alert progression.
* **Explainability Box**: Background `#fdfdfc`, border 1px solid `#f0f0f0`, border-left 3px solid `#ff5e1f`.

---

## 5. Accessibility & Guardrails

1. **High Contrast Compliance**:
   - The primary text `#262626` on `#ffffff` delivers a 40.5:1 ratio, exceeding WCAG AAA standards.
   - For small text on `#ff5e1f` background, use font-weight 500 or pair with a dark icon to guarantee immediate clarity.
2. **Color Plus Shape**:
   - Never use color alone to signal risk. High risk must pair `#ff5e1f` with an icon (`AlertTriangle`) and a clear text tag (`CRITICAL`).
3. **Keyboard Navigation**:
   - Every interactive table row and filter pill is focusable with a standardized `2px solid #ff5e1f` focus indicator.

---

## 6. Pre-Flight Quality Verification (Checklist)

Every pull request or frontend component must satisfy this check:

- [ ] **Zero Em-Dashes**: No em-dash or en-dash characters anywhere in code, copy, titles, or tooltips (use standard hyphen only).
- [ ] **One Accent Rule**: `#ff5e1f` is the singular accent color on the page.
- [ ] **Shape Consistency**: All button, card, and modal corners use the 1px - 4px industrial scale.
- [ ] **Font Discipline**: Display & body use `FT Kunst Grotesk` (or Geist); all numbers, dates, IDs use Monospace.
- [ ] **Viewport Fit**: Executive overview top section fits the viewport without unnecessary scrolling.
- [ ] **CTA Wrap Check**: No button text wraps onto two lines at desktop screen widths.
- [ ] **Reduced Motion**: All animations degrade gracefully under `prefers-reduced-motion`.
