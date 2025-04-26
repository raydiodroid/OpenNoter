import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form.tsx'
import { useEffect } from 'react'
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
import { useModelStore } from '@/store/modelStore'
import { Alert } from 'antd'
import { Textarea } from '@/components/ui/textarea.tsx'
// ✅ 定义表单 schema
const formSchema = z.object({
  video_url: z.string().url('请输入正确的视频链接'),
  platform: z.string().nonempty('请选择平台'),
  quality: z.enum(['fast', 'medium', 'slow'], {
    required_error: '请选择音频质量',
  }),
  screenshot: z.boolean().optional(),
  link: z.boolean().optional(),
  model_name: z.string().nonempty('请选择模型'),
  format: z.array(z.string()).default([]), // ✨ 确保默认是空数组
  style: z.string().nonempty('请选择笔记生成风格'),
  extras: z.string().optional(),
})

type NoteFormValues = z.infer<typeof formSchema>
const noteFormats = [
  {
    label: '目录',
    value: 'toc',
  },
  { label: '原片跳转', value: 'link' },
  { label: '原片截图', value: 'screenshot' },
  { label: 'AI总结', value: 'summary' },
]
const noteStyles = [
  {
    label: '精简',
    value: 'minimal', // 简洁、快速呈现要点
  },
  {
    label: '详细',
    value: 'detailed', // 详细记录，包含时间戳、关键点
  },
  {
    label: '教程',
    value: 'tutorial', // 详细记录，包含时间戳、关键点
  },
  {
    label: '学术',
    value: 'academic', // 适合学术报告，正式且结构化
  },
  {
    label: '小红书',
    value: 'xiaohongshu', // 适合社交平台分享，亲切、口语化
  },
  {
    label: '生活向',
    value: 'life_journal', // 记录个人生活感悟，情感化表达
  },
  {
    label: '任务导向',
    value: 'task_oriented', // 强调任务、目标，适合工作和待办事项
  },
  {
    label: '商业风格',
    value: 'business', // 适合商业报告、会议纪要，正式且精准
  },
  {
    label: '会议纪要',
    value: 'meeting_minutes', // 适合商业报告、会议纪要，正式且精准
  },
]

const NoteForm = () => {
  useTaskStore(state => state.tasks)
  const setCurrentTask = useTaskStore(state => state.setCurrentTask)
  const currentTaskId = useTaskStore(state => state.currentTaskId)
  const getCurrentTask = useTaskStore(state => state.getCurrentTask)
  const loadEnabledModels = useModelStore(state => state.loadEnabledModels)
  const modelList = useModelStore(state => state.modelList)
  const showFeatureHint = useModelStore(state => state.showFeatureHint)
  const setShowFeatureHint = useModelStore(state => state.setShowFeatureHint)
  const form = useForm<NoteFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      video_url: '',
      platform: 'bilibili',
      quality: 'medium', // 默认中等质量
      screenshot: false,
      model_name: modelList[0]?.model_name || '', // 确保有值
      format: [], // 初始化为空数组
      style: 'minimal', // 默认选择精简风格
      extras: '', // 初始化为空字符串
    },
  })

  const onClose = () => {
    setShowFeatureHint(false)
  }
  const isGenerating = () => {
    console.log('🚀 isGenerating', getCurrentTask()?.status)
    return getCurrentTask()?.status === 'PENDING'
  }

  const onSubmit = async (data: NoteFormValues) => {
    console.log('🎯 提交内容：', data)
    const payload = {
      video_url: data.video_url,
      platform: data.platform,
      quality: data.quality,
      model_name: data.model_name,
      provider_id: modelList.find(model => model.model_name === data.model_name).provider_id,
      format: data.format,
      style: data.style,
      extras: data.extras,
    }
    const res = await generateNote(payload)
    const taskId = res.data.task_id
    useTaskStore.getState().addPendingTask(taskId, data.platform, payload)
  }
  useEffect(() => {
    loadEnabledModels()
  }, [])

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

            <FormField
              control={form.control}
              name="model_name"
              render={({ field }) => (
                <FormItem>
                  <div className="my-3 flex items-center justify-between">
                    <h2 className="block">模型选择</h2>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
                        </TooltipTrigger>
                        <TooltipContent>
                          <p className="max-w-[200px] text-xs">不同模型返回质量不同，可自行测试</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </div>
                  <Select onValueChange={field.onChange} defaultValue={field.value}>
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="选择配置好的模型" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {modelList.map(item => {
                        return <SelectItem value={item.model_name}>{item.model_name}</SelectItem>
                      })}
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

          <FormField
            control={form.control}
            name="style"
            render={({ field }) => (
              <FormItem>
                <div className="my-3 flex items-center justify-between">
                  <h2 className="block">笔记风格</h2>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
                      </TooltipTrigger>
                      <TooltipContent>
                        <p className="max-w-[200px] text-xs">选择你希望生成的笔记风格</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>

                <Select onValueChange={field.onChange} defaultValue={field.value}>
                  <FormControl>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="选择笔记风格" />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {noteStyles.map(item => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="format"
            render={({ field }) => (
              <FormItem>
                <div className="my-3 flex items-center justify-between">
                  <h2 className="block">笔记格式</h2>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
                      </TooltipTrigger>
                      <TooltipContent>
                        <p className="text-xs">选择要包含的笔记元素，比如时间戳、截图提示或总结</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>

                <FormControl>
                  <div className="flex space-x-1.5">
                    {noteFormats.map(item => (
                      <label key={item.value} className="flex items-center space-x-2">
                        <Checkbox
                          checked={field.value?.includes(item.value)}
                          onCheckedChange={checked => {
                            const currentValue = field.value ?? [] // ✨ 保底是数组
                            if (checked) {
                              field.onChange([...currentValue, item.value])
                            } else {
                              field.onChange(currentValue.filter(v => v !== item.value))
                            }
                          }}
                        />
                        <span>{item.label}</span>
                      </label>
                    ))}
                  </div>
                </FormControl>
                <FormField
                  control={form.control}
                  name="extras"
                  render={({ field }) => (
                    <FormItem>
                      <div className="my-3 flex items-center justify-between">
                        <h2 className="block">备注</h2>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
                            </TooltipTrigger>
                            <TooltipContent>
                              <p className="text-xs">会把这段加入到Prompt最后 可自行测试</p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                      <Textarea placeholder={'笔记需要罗列出 xxx 关键点'} />

                      {/*<FormDescription className="text-xs text-neutral-500">*/}
                      {/*    质量越高，下载体积越大，速度越慢*/}
                      {/*</FormDescription>*/}
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormMessage />
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
      {showFeatureHint && (
        <Alert
          message="功能介绍 v2.0.0"
          description={
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
          }
          type="info"
          onClose={onClose}
          closable
        />
      )}
      {/*<div className="bg-primary-light mt-6 rounded-lg p-4"></div>*/}
    </div>
  )
}

export default NoteForm
