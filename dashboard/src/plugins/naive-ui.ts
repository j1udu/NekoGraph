import type { GlobalThemeOverrides } from 'naive-ui'

export const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#176c52',
    primaryColorHover: '#21805f',
    primaryColorPressed: '#125b45',
    primaryColorSuppl: '#176c52',
    borderRadius: '7px',
    borderRadiusSmall: '6px',
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  Button: { heightMedium: '38px', fontWeight: '650' },
  Card: { borderRadius: '8px' },
  DataTable: { thColor: '#f8faf9', tdColorHover: '#f7faf8' },
  Menu: {
    itemColorActive: '#d8f0e5',
    itemTextColorActive: '#174f3e',
    itemIconColorActive: '#174f3e',
  },
}
