import { onSessionChange, sessionScope } from '@/lib/sessionScope'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ChatSource } from '@/services/chat'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: ChatSource[]
}

interface ChatState {
  chatHistory: Record<string, ChatMessage[]>
  addMessage: (taskId: string, msg: ChatMessage) => void
  clearChat: (taskId: string) => void
  getMessages: (taskId: string) => ChatMessage[]
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      chatHistory: {},

      addMessage: (taskId, msg) =>
        set(state => ({
          chatHistory: {
            ...state.chatHistory,
            [taskId]: [...(state.chatHistory[taskId] || []), msg],
          },
        })),

      clearChat: (taskId) =>
        set(state => {
          const next = { ...state.chatHistory }
          delete next[taskId]
          return { chatHistory: next }
        }),

      getMessages: (taskId) => get().chatHistory[taskId] || [],
    }),
    {
      name: 'opennoter-chat-storage:locked',
      skipHydration: true,
    },
  ),
)

function switchChatScope() {
  useChatStore.persist.setOptions({ name: 'opennoter-chat-storage:locked' })
  useChatStore.setState({ chatHistory: {} })
  const scope = sessionScope()
  useChatStore.persist.setOptions({ name: `opennoter-chat-storage:${scope ?? 'locked'}` })
  if (scope) void useChatStore.persist.rehydrate()
}
onSessionChange(switchChatScope)
if (sessionScope()) switchChatScope()
