# Design System & UI Specification: Clean Light Theme

> **Usage Instructions for AI Agent**:  
> You are tasked with implementing the UI for this project using the exact **Clean Light Theme** design system specified below.  
> Follow the color tokens, typography scales, spacing rules, card treatments, and component patterns strictly.  
> Do **NOT** default to generic white-and-gray Tailwind defaults or dark neon styles. Adhere to this exact institutional, high-trust, crisp aesthetic.

---

## 1. Aesthetic Identity & Philosophy

- **Style**: Modern Institutional FinTech / Quant Workspace — clean, high-contrast, mathematically precise, calm, and trustworthy.
- **Core Feel**: Crisp off-white surfaces, hairline borders, rich slate typography, deep forest viridian/teal primary accents, and refined warm amber/gold secondary accents.
- **Visual Texture**: Light subtle mesh patterns (`#31516b0b` 1px grid), soft translucent glass headers (`backdrop-filter: blur(16px)`), subtle box shadows (`rgba(15, 23, 42, 0.04)`), and micro-interactions.
- **Accessibility**: Strict WCAG AA contrast compliance across all text and interactive elements. All touch targets $\ge 44\text{px}$.

---

## 2. Design Tokens

### 2.1 CSS Variables (Root Tokens)

Drop this directly into your global CSS (`globals.css`, `index.css`, or `theme.css`):

```css
:root {
  color-scheme: light;

  /* Canvas & Surfaces */
  --bg-canvas: #f4f7f8;        /* Soft cool off-white page canvas */
  --bg-surface: #ffffff;       /* Pure white elevated cards and panels */
  --bg-subtle: #eaf0f2;        /* Raised background for wells, inputs, tags */
  --bg-soft-teal: #edf5f3;     /* Tinted soft teal background for highlights */
  --bg-soft-gold: #fbf6ec;     /* Tinted soft gold background for badges/active */

  /* Borders & Dividers */
  --border-subtle: #d4dee4;    /* Hairline border for cards, inputs, dividers */
  --border-muted: #e2e8f0;     /* Extra soft interior divider lines */
  --border-focus: #0f766e;     /* High-visibility active focus ring */

  /* Typography & Text */
  --text-primary: #0f172a;     /* Deep slate (almost black) for headings/titles */
  --text-body: #1e293b;        /* High-contrast readable body text */
  --text-muted: #526173;       /* Balanced slate-cyan for descriptions/labels */
  --text-dim: #64748b;         /* Captions, timestamps, secondary meta */

  /* Brand Accents */
  --accent-teal: #0f766e;      /* Forest Viridian / Teal (Primary action color) */
  --accent-teal-hover: #115e59;/* Darker teal for hover states */
  --accent-gold: #966915;      /* Burnished brass / Gold for high-value highlights */
  --accent-gold-hover: #78520d;

  /* Semantic Feedback */
  --success: #0f766e;          /* Up / Bullish / Active state */
  --success-bg: #e6f4f2;
  --danger: #b91c1c;           /* Down / Bearish / Error state */
  --danger-bg: #fef2f2;
  --warning: #b45309;          /* Pending / Caution */
  --warning-bg: #fffbeb;
  --info: #0284c7;             /* Informational note */
  --info-bg: #f0f9ff;

  /* Typography Fonts */
  --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;

  /* Shadows */
  --shadow-card: 0 10px 30px -10px rgba(15, 23, 42, 0.04), 0 2px 6px -2px rgba(15, 23, 42, 0.02);
  --shadow-dropdown: 0 20px 40px -15px rgba(15, 23, 42, 0.12), 0 0 1px 1px rgba(15, 23, 42, 0.05);
  --shadow-button: 0 9px 22px -12px rgba(15, 118, 110, 0.35);
  --shadow-button-hover: 0 14px 28px -13px rgba(15, 118, 110, 0.45);

  /* Radii */
  --radius-sm: 6px;            /* Tags, chips, inline badges */
  --radius-md: 9px;            /* Inputs, buttons, segmented toggles */
  --radius-lg: 12px;           /* Standard cards, popovers, tables */
  --radius-xl: 18px;           /* Modals, feature highlight containers */
}
```

---

## 3. Tailwind CSS Configuration Extension

If the target project uses Tailwind CSS, extend `tailwind.config.js` with these tokens:

```javascript
/** @type {import('tailwindcss').Config} */
module.exports = {
  theme: {
    extend: {
      colors: {
        canvas: '#f4f7f8',
        surface: '#ffffff',
        subtle: '#eaf0f2',
        borderSubtle: '#d4dee4',
        borderMuted: '#e2e8f0',
        textPrimary: '#0f172a',
        textMuted: '#526173',
        textDim: '#64748b',
        tealAccent: {
          DEFAULT: '#0f766e',
          hover: '#115e59',
          soft: '#edf5f3',
        },
        goldAccent: {
          DEFAULT: '#966915',
          hover: '#78520d',
          soft: '#fbf6ec',
        },
      },
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        themeSm: '6px',
        themeMd: '9px',
        themeLg: '12px',
        themeXl: '18px',
      },
      boxShadow: {
        themeCard: '0 10px 30px -10px rgba(15, 23, 42, 0.04), 0 2px 6px -2px rgba(15, 23, 42, 0.02)',
        themeDropdown: '0 20px 40px -15px rgba(15, 23, 42, 0.12)',
        themeBtn: '0 9px 22px -12px rgba(15, 118, 110, 0.35)',
      }
    },
  },
};
```

---

## 4. Typography Rules

1. **Hierarchy & Sizes**:
   - **Page Heading / Hero Title**: `clamp(28px, 3.5vw, 40px)`, bold `700`, tracking `-0.04em`, line-height `1.2`.
   - **Section Title**: `24px - 28px`, bold `700`, tracking `-0.03em`.
   - **Card / Widget Title**: `16px - 18px`, semibold `600`, tracking `-0.02em`.
   - **Body Text**: `14px - 15px`, regular `400` or medium `500`, line-height `1.65`, color `var(--text-body)`.
   - **Secondary / Subtext**: `12px - 13px`, medium `500`, color `var(--text-muted)`.
   - **Eyebrow / Overline**: `10px - 11px`, bold `700`, uppercase, tracking `0.12em`, color `var(--accent-teal)` or `var(--accent-gold)`.
   - **Numbers, Tickers, Metrics, Timestamps, Table Cells**: Always use `font-family: var(--font-mono);` and `font-variant-numeric: tabular-nums;`.

2. **Contrast & Balance**:
   - Always apply `text-wrap: balance;` on headings.
   - Never use pure pitch-black (`#000000`) for text. Use `--text-primary` (`#0f172a`).
   - Secondary text must remain comfortably legible (contrast ratio $> 4.5:1$ against `#f4f7f8` or `#ffffff`).

---

## 5. UI Shell & Layout Architecture

### 5.1 Workspace Sidebar Navigation
- **Width**: `224px` (fixed on desktop, drawer on mobile).
- **Background**: `#ffffff`, border-right `1px solid var(--border-subtle)`.
- **Top Brand Lockup**:
  - Height: `72px - 78px`, padding `0 16px`.
  - Icon badge: `36px - 40px` rounded square with subtle border and soft teal tint.
  - Brand name: `14px` bold `700`, tracking `0.05em`.
- **Nav Groups**:
  - Category header: `10px` uppercase, `font-weight: 700`, letter-spacing `0.12em`, color `var(--text-muted)`.
  - Nav Item: `min-height: 44px`, padding `8px 12px`, border-radius `7px - 9px`, font size `12px - 13px`, color `var(--text-muted)`.
  - **Hover state**: Background `var(--bg-subtle)`, text `var(--text-primary)`.
  - **Active state**: 
    - Text: `var(--accent-gold)` or `var(--accent-teal)`
    - Background: `var(--bg-soft-gold)` or `var(--bg-soft-teal)`
    - Left accent pill indicator: `border-left: 3px solid var(--accent-gold);`

### 5.2 Sticky Topbar
- **Height**: `64px - 68px`.
- **Position**: `sticky; top: 0; z-index: 40;`
- **Background**: `rgba(255, 255, 255, 0.92)` with `backdrop-filter: blur(16px)`.
- **Border**: Bottom `1px solid var(--border-subtle)`.
- **Content**:
  - Breadcrumb / context label on left.
  - Center search box: `width: min(440px, 45%)`, height `40px`, background `var(--bg-subtle)`, border `1px solid var(--border-subtle)`, rounded `9px`. Contains placeholder text and `<kbd>Ctrl+K</kbd>` chip on right.
  - Right action items: Status indicator, theme toggle button, and user avatar circle (`34px`, background `var(--accent-teal)`, white bold initials).

---

## 6. Core Component Specifications

### 6.1 Buttons

#### Primary Button (High Priority Call-To-Action)
```css
.btn-primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 44px;
  padding: 10px 20px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, #147a72 0%, #0f766e 100%);
  color: #ffffff;
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 600;
  border: 1px solid rgba(255, 255, 255, 0.2);
  box-shadow: var(--shadow-button);
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: transform 0.2s cubic-bezier(0.2, 0.8, 0.2, 1), box-shadow 0.2s ease;
}

/* Subtle light shimmer effect on hover */
.btn-primary::before {
  content: '';
  position: absolute;
  inset: -60% -30%;
  background: linear-gradient(105deg, transparent 35%, rgba(255, 255, 255, 0.28) 50%, transparent 65%);
  transform: translate3d(-100%, 0, 0) rotate(10deg);
  transition: transform 0.6s cubic-bezier(0.16, 1, 0.3, 1);
  pointer-events: none;
}

.btn-primary:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-button-hover);
}

.btn-primary:hover::before {
  transform: translate3d(100%, 0, 0) rotate(10deg);
}

.btn-primary:active {
  transform: translateY(1px) scale(0.99);
}
```

#### Secondary Button (Outlined / Surface)
```css
.btn-secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 44px;
  padding: 10px 18px;
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 600;
  border: 1px solid var(--border-subtle);
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-secondary:hover {
  background: var(--bg-subtle);
  border-color: #b5c7d3;
  color: var(--text-primary);
}

.btn-secondary:active {
  background: #dfe7eb;
}
```

---

### 6.2 Cards & Containers

- **Standard Card**:
  - Background: `var(--bg-surface)` (`#ffffff`)
  - Border: `1px solid var(--border-subtle)` (`#d4dee4`)
  - Border Radius: `var(--radius-lg)` (`12px`)
  - Padding: `20px - 24px`
  - Shadow: `var(--shadow-card)`
  - Hover effect (clickable cards): `border-color: var(--accent-teal); transform: translateY(-2px); transition: all 0.2s ease;`

- **Metric / KPI Stat Card**:
  - Eyebrow: `11px` uppercase tracking `0.08em`, color `var(--text-muted)`.
  - Stat Value: `26px - 32px` bold `700`, `font-family: var(--font-mono); font-variant-numeric: tabular-nums;`.
  - Delta / Change Pill: `font-size: 11px`, padding `3px 8px`, border-radius `var(--radius-sm)`.
    - Positive: Green/Teal text (`#0f766e`), background `#edf5f3`.
    - Negative: Red text (`#b91c1c`), background `#fef2f2`.

---

### 6.3 Form Inputs & Search Fields

```css
.input-field {
  width: 100%;
  min-height: 42px;
  padding: 8px 14px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.input-field::placeholder {
  color: var(--text-dim);
}

.input-field:focus {
  border-color: var(--accent-teal);
  box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.12);
}
```

#### Keyboard Shortcut Tag (`<kbd>`)
```css
kbd, .kbd-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  font-size: 10px;
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text-muted);
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  user-select: none;
}
```

---

### 6.4 Data Tables

- **Header Row (`<th>`)**:
  - Background: `var(--bg-subtle)` (`#eaf0f2`) or `var(--bg-surface)` with bottom border `1px solid var(--border-subtle)`.
  - Text: `11px` bold `700`, uppercase, tracking `0.06em`, color `var(--text-muted)`.
  - Padding: `12px 16px`.
- **Data Rows (`<td>`)**:
  - Padding: `14px 16px`.
  - Border bottom: `1px solid var(--border-muted)`.
  - Numbers: `font-family: var(--font-mono); font-variant-numeric: tabular-nums;`.
  - Row Hover: `background-color: #f7fafb;`.

---

### 6.5 Status Badges & Pills

```html
<!-- Success / Positive State -->
<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-semibold rounded bg-[#edf5f3] text-[#0f766e] border border-[#c4e3dc]">
  ● Active / Verified
</span>

<!-- High-Priority / Gold State -->
<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-semibold rounded bg-[#fbf6ec] text-[#966915] border border-[#ebd8ad]">
  ★ Featured / Premium
</span>

<!-- Neutral / Subtle State -->
<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded bg-[#eaf0f2] text-[#526173] border border-[#d4dee4]">
  Standard
</span>
```

---

## 7. Motion & Micro-Interactions

1. **Subtle & Restrained**: Motion intensity should be `3/10` to `4/10` (never bouncy or distracting).
2. **Durations & Easings**:
   - Micro-state changes (hover, focus, borders): `150ms - 200ms ease`.
   - Modals, drawers, tooltips: `200ms - 250ms cubic-bezier(0.2, 0.8, 0.2, 1)`.
3. **Accessibility**:
   - Always wrap CSS transitions and animations in `@media (prefers-reduced-motion: no-preference)`.

---

## 8. Anti-Slop / Quality Checklist for the AI

Before presenting UI code, verify:
- [ ] Canvas is cool off-white (`#f4f7f8`), NOT blinding `#ffffff` everywhere and NOT dark mode.
- [ ] Elevated cards and panels are crisp `#ffffff` with hairline `#d4dee4` borders.
- [ ] Text uses deep slate (`#0f172a`), NOT pure harsh `#000000`.
- [ ] Primary button is rich teal gradient (`#147a72` to `#0f766e`), NOT generic purple or bright cyan.
- [ ] Secondary highlights use warm amber/gold (`#966915`), NOT bright yellow.
- [ ] Numbers, stats, timestamps, and tickers use monospace font (`JetBrains Mono`) with `tabular-nums`.
- [ ] All interactive buttons and links have visible keyboard `:focus-visible` rings.
- [ ] Padding and spacing feel spacious, structured, and institutional.
