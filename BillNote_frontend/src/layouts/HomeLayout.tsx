import React, { FC } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip.tsx'

import { useState } from 'react'
import { Link } from 'react-router-dom'

interface IProps {
  NoteForm: React.ReactNode
  Preview: React.ReactNode
  History: React.ReactNode
}
const HomeLayout: FC<IProps> = ({ NoteForm, Preview, History }) => {
  const [, setShowSettings] = useState(false)

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-white">
      <div className="flex flex-1">
        {/* 左侧部分：Header + 表单 */}
        <aside className="flex w-[340px] flex-col border-r border-neutral-200 bg-white">
          {/* Header */}
          <header className="flex h-16 items-center justify-between px-6">
            <div className="flex items-center gap-2">
              <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-2xl">
                <img src="/icon.svg" alt="logo" className="h-full w-full object-contain" />
              </div>
              <div className="text-2xl font-bold text-gray-800">BiliNote</div>
            </div>
            <div>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger onClick={() => setShowSettings(true)}>
                    <Link to={'/settings'}>
                      <SlidersHorizontal className="text-muted-foreground hover:text-primary cursor-pointer" />
                    </Link>
                  </TooltipTrigger>
                  <TooltipContent>
                    <span>全局配置</span>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
          </header>

          {/* 表单内容 */}
          <div className="flex-1 overflow-auto p-4">
            {/*<NoteForm />*/}
            {NoteForm}
          </div>
        </aside>
        <aside className="flex h-full w-[300px] flex-col border-r border-neutral-200 bg-white">
          {/* Header */}

          {/* 表单内容 */}
          {/*<NoteForm />*/}
          {History}
        </aside>

        {/* 右侧预览区域 */}
        <main className="h-screen flex-1 overflow-hidden bg-white p-6">
          {/*<Outlet />*/}
          {Preview}
        </main>
      </div>

      {/* 页脚 */}
      {/*<footer className="h-12 bg-white shadow-inner flex items-center justify-center text-sm text-neutral-600">*/}
      {/*    © 2025 BiliNote. All rights reserved.*/}
      {/*</footer>*/}
    </div>
  )
}

export default HomeLayout
