import { create } from 'zustand'

export type Theme = 'light' | 'dark'

const THEME_KEY = 'opennoter.theme'

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch {
    // localStorage 不可用时保持默认浅色
  }
  return 'light'
}

function applyTheme(theme: Theme) {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.theme = theme
}

interface ThemeState {
  theme: Theme
  toggle: () => void
}

export const useThemeStore = create<ThemeState>(set => ({
  theme: initialTheme(),
  toggle: () =>
    set(state => {
      const theme: Theme = state.theme === 'light' ? 'dark' : 'light'
      try {
        localStorage.setItem(THEME_KEY, theme)
      } catch {
        // 只影响偏好持久化
      }
      applyTheme(theme)
      return { theme }
    }),
}))

// 模块加载时同步初始主题，避免首帧闪色
applyTheme(useThemeStore.getState().theme)
