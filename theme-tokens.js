/**
 * Clean Light Theme Tokens
 * Derived from design.md
 * Ready for Tailwind config, CSS-in-JS, or direct JavaScript styling
 */

export const lightThemeTokens = {
  colors: {
    // Surfaces & Backgrounds
    canvas: '#f4f7f8',
    surface: '#ffffff',
    subtle: '#eaf0f2',
    softTeal: '#edf5f3',
    softGold: '#fbf6ec',

    // Borders & Lines
    borderSubtle: '#d4dee4',
    borderMuted: '#e2e8f0',
    borderFocus: '#0f766e',

    // Typography
    textPrimary: '#0f172a',
    textBody: '#1e293b',
    textMuted: '#526173',
    textDim: '#64748b',
    textOnAccent: '#ffffff',

    // Brand Accents
    accentTeal: {
      DEFAULT: '#0f766e',
      hover: '#115e59',
      active: '#134e4a',
      soft: '#edf5f3',
    },
    accentGold: {
      DEFAULT: '#966915',
      hover: '#78520d',
      soft: '#fbf6ec',
    },

    // Semantic States
    success: {
      DEFAULT: '#0f766e',
      bg: '#edf5f3',
      border: '#c4e3dc',
    },
    danger: {
      DEFAULT: '#b91c1c',
      bg: '#fef2f2',
      border: '#fecaca',
    },
    warning: {
      DEFAULT: '#b45309',
      bg: '#fffbeb',
      border: '#fde68a',
    },
    info: {
      DEFAULT: '#0284c7',
      bg: '#f0f9ff',
      border: '#bae6fd',
    },
  },

  fonts: {
    sans: "'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    mono: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  },

  radii: {
    xs: '4px',
    sm: '6px',
    md: '9px',
    lg: '12px',
    xl: '18px',
    full: '9999px',
  },

  shadows: {
    card: '0 10px 30px -10px rgba(15, 23, 42, 0.04), 0 2px 6px -2px rgba(15, 23, 42, 0.02)',
    cardHover: '0 16px 36px -10px rgba(15, 23, 42, 0.08), 0 4px 10px -2px rgba(15, 23, 42, 0.03)',
    dropdown: '0 20px 40px -15px rgba(15, 23, 42, 0.12), 0 0 1px 1px rgba(15, 23, 42, 0.05)',
    button: '0 9px 22px -12px rgba(15, 118, 110, 0.35)',
    buttonHover: '0 14px 28px -13px rgba(15, 118, 110, 0.45)',
  },
};

export default lightThemeTokens;
