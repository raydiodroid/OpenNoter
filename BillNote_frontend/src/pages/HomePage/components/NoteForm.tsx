import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form.tsx'
import { Input } from '@/components/ui/input.tsx'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select.tsx'
import { Button } from '@/components/ui/button.tsx'
import { Checkbox } from '@/components/ui/checkbox.tsx'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { Info, Clock, Loader2 } from 'lucide-react'

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip.tsx'
import { generateNote } from '@/services/note.ts'
import { useTaskStore } from '@/store/taskStore'
import NoteHistory from '@/pages/HomePage/components/NoteHistory.tsx'

// ✅ 定义表单 schema
const formSchema = z.object({
  video_url: z.string().url('请输入正确的视频链接'),
  platform: z.string().nonempty('请选择平台'),
  quality: z.enum(['fast', 'medium', 'slow'], {
    required_error: '请选择音频质量',
  }),
  screenshot: z.boolean().optional(),
  link: z.boolean().optional(),
})

type NoteFormValues = z.infer<typeof formSchema>

const NoteForm = () => {
  useTaskStore(state => state.tasks)
  const setCurrentTask = useTaskStore(state => state.setCurrentTask)
  const currentTaskId = useTaskStore(state => state.currentTaskId)
  const getCurrentTask = useTaskStore(state => state.getCurrentTask)
  const form = useForm<NoteFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      video_url: '',
      platform: 'bilibili',
      quality: 'medium', // 默认中等质量
      screenshot: false,
    },
  })

  const isGenerating = () => {
    console.log('🚀 isGenerating', getCurrentTask()?.status)
    return getCurrentTask()?.status === 'PENDING'
  }

  const onSubmit = async (data: NoteFormValues) => {
    console.log('🎯 提交内容：', data)
    await generateNote({
      video_url: data.video_url,
      platform: data.platform,
      quality: data.quality,
      screenshot: data.screenshot,
      link: data.link,
    })
  }

  return (
    <div className="flex h-full flex-col">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          <div className="space-y-2">
            <div className="my-3 flex items-center justify-between">
              <h2 className="block">视频链接</h2>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs">输入视频链接，支持哔哩哔哩、YouTube等平台</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>

            <div className="flex gap-2">
              {/* 平台选择 */}
              <FormField
                control={form.control}
                name="platform"
                render={({ field }) => (
                  <FormItem>
                    <Select onValueChange={field.onChange} defaultValue={field.value}>
                      <FormControl>
                        <SelectTrigger className="w-32">
                          <SelectValue placeholder="选择平台" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="bilibili">哔哩哔哩</SelectItem>
                        <SelectItem value="youtube">Youtube</SelectItem>
                        {/*<SelectItem value="local">本地视频</SelectItem>*/}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* 视频地址 */}
              <FormField
                control={form.control}
                name="video_url"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormControl>
                      <Input placeholder="视频链接" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            {/*<p className="text-xs text-neutral-500">*/}
            {/*    支持哔哩哔哩视频链接，例如：*/}
            {/*    https://www.bilibili.com/video/BV1vc25YQE9X/*/}
            {/*</p>*/}
            <FormField
              control={form.control}
              name="quality"
              render={({ field }) => (
                <FormItem>
                  <div className="my-3 flex items-center justify-between">
                    <h2 className="block">音频质量</h2>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
                        </TooltipTrigger>
                        <TooltipContent>
                          <p className="max-w-[200px] text-xs">质量越高，下载体积越大，速度越慢</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </div>
                  <Select onValueChange={field.onChange} defaultValue={field.value}>
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="选择质量" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="fast">快速（压缩）</SelectItem>
                      <SelectItem value="medium">中等（推荐）</SelectItem>
                      <SelectItem value="slow">高质量（清晰）</SelectItem>
                    </SelectContent>
                  </Select>
                  {/*<FormDescription className="text-xs text-neutral-500">*/}
                  {/*    质量越高，下载体积越大，速度越慢*/}
                  {/*</FormDescription>*/}
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          {/* 是否需要原片位置 */}
          <FormField
            control={form.control}
            name="link"
            render={({ field }) => (
              <FormItem className="flex items-center space-x-2">
                {/* Tooltip 部分 */}

                <FormControl>
                  <Checkbox checked={field.value} onCheckedChange={field.onChange} id="link" />
                </FormControl>

                <FormLabel htmlFor="link" className="text-sm leading-none font-medium">
                  是否插入内容跳转链接
                </FormLabel>
              </FormItem>
            )}
          />
          {/* 是否需要下载 */}
          <FormField
            control={form.control}
            name="screenshot"
            render={({ field }) => (
              <FormItem className="flex items-center space-x-2">
                {/* Tooltip 部分 */}

                <FormControl>
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={field.onChange}
                    id="screenshot"
                  />
                </FormControl>

                <FormLabel htmlFor="screenshot" className="text-sm leading-none font-medium">
                  是否插入视频截图
                </FormLabel>
              </FormItem>
            )}
          />
          <div className={'flex w-full items-center gap-2 py-1.5'}>
            {/* 提交按钮 */}
            <Button type="submit" className="bg-primary w-full" disabled={isGenerating()}>
              {isGenerating() && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {isGenerating() ? '正在生成…' : '生成笔记'}
            </Button>
          </div>
        </form>
      </Form>

      {/*生成历史    */}
      <div className="my-4 flex items-center gap-2">
        <Clock className="h-4 w-4 text-neutral-500" />
        <h2 className="text-base font-medium text-neutral-900">生成历史</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <NoteHistory onSelect={setCurrentTask} selectedId={currentTaskId} />
      </div>

      {/* 添加一些额外的说明或功能介绍 */}
      <div className="bg-primary-light mt-6 rounded-lg p-4">
        <h3 className="text-primary mb-2 font-medium">功能介绍</h3>
        <ul className="space-y-2 text-sm text-neutral-600">
          <li className="flex items-start gap-2">
            <span className="text-primary font-bold">•</span>
            <span>自动提取视频内容，生成结构化笔记</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-primary font-bold">•</span>
            <span>支持多个视频平台，包括哔哩哔哩、YouTube等</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-primary font-bold">•</span>
            <span>一键复制笔记，支持Markdown格式</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-primary font-bold">•</span>
            <span>可选择是否插入图片</span>
          </li>
        </ul>
      </div>
    </div>
  )
}

export default NoteForm
