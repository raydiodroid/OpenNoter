import { ToastQueue } from '@react-spectrum/s2'

/** 全站统一的消息提示。用 Spectrum 的 ToastQueue，失败/成功/中性三档。 */
export const toast = {
  success(message: string) {
    ToastQueue.positive(message, { timeout: 3000 })
  },
  /**
   * 可撤销的成功提示。
   *
   * Spectrum 规范：toast 最多带一个操作按钮，且**可操作的 toast 不会自动消失**
   * （否则用户还没读到「撤销」就被关掉，违反 WCAG 2.2.1），所以这里不传 timeout，
   * 由 `shouldCloseOnAction` 在点击后关闭。
   *
   * 用途：可逆的即时切换（如启用/停用用户）不该弹二次确认框，
   * 用「先做 + 可撤销」替代「先问 + 再做」。
   */
  successAction(message: string, actionLabel: string, onAction: () => void) {
    ToastQueue.positive(message, { actionLabel, onAction, shouldCloseOnAction: true })
  },
  error(message: string) {
    ToastQueue.negative(message, { timeout: 5000 })
  },
  info(message: string) {
    ToastQueue.info(message, { timeout: 3000 })
  },
}
