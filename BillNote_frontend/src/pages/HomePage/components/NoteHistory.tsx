import { useTaskStore } from '@/store/taskStore'
import { FC } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { Badge } from '@/components/ui/badge.tsx'
import { cn } from '@/lib/utils.ts'
import { Trash } from 'lucide-react'
import { Button } from '@/components/ui/button.tsx'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip.tsx'

interface NoteHistoryProps {
  onSelect: (taskId: string) => void
  selectedId: string | null
}

const NoteHistory: FC<NoteHistoryProps> = ({ onSelect, selectedId }) => {
  const tasks = useTaskStore(state => state.tasks)
  const removeTask = useTaskStore(state => state.removeTask)

  if (tasks.length === 0) {
    return (
      <div className="rounded-md border border-neutral-200 bg-neutral-50 py-6 text-center">
        <p className="text-sm text-neutral-500">暂无历史记录</p>
      </div>
    )
  }

  return (
    <ScrollArea className="h-auto max-h-[20vh] sm:max-h-[10vh]">
      <div className="flex flex-col space-y-2">
        {tasks.map(task => (
          <div
            key={task.id}
            className={cn(
              'flex cursor-pointer items-center gap-4 rounded-md border p-3 transition hover:bg-neutral-50',
              selectedId === task.id && 'border-primary bg-primary-light'
            )}
            onClick={() => onSelect(task.id)}
          >
            {/* 封面图 */}
            <img
              src={
                task.audioMeta.cover_url
                  ? `/api/image_proxy?url=${encodeURIComponent(task.audioMeta.cover_url)}`
                  : '/placeholder.png'
              }
              alt="封面"
              className="h-10 w-16 rounded-md object-cover"
            />

            {/* 标题 + 状态 */}

            <div className="flex w-full min-w-0 items-center justify-between gap-2">
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="max-w-[120px] flex-1 truncate font-medium">
                      {task.audioMeta.title || '未命名笔记'}
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>{task.audioMeta.title || '未命名笔记'}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="shrink-0">
                {task.status === 'SUCCESS' && <Badge variant="default">已完成</Badge>}
                {task.status === 'PENDING' && <Badge variant="outline">等待中</Badge>}
                {task.status === 'FAILED' && <Badge variant="destructive">失败</Badge>}
              </div>
            </div>

            {/* 删除按钮 */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    onClick={e => {
                      e.stopPropagation()
                      removeTask(task.id)
                    }}
                    className="shrink-0"
                  >
                    <Trash className="text-muted-foreground h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>删除</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        ))}
      </div>
    </ScrollArea>
  )
}

export default NoteHistory
