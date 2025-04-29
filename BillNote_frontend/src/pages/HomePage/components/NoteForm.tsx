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
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { uploadFile } from '@/services/upload.ts'
// ✅ 定义表单 schema
const formSchema = z
  .object({
    video_url: z.string(),
    platform: z.string().nonempty('请选择平台'),
    quality: z.enum(['fast', 'medium', 'slow'], {
      required_error: '请选择音频质量',
    }),
    screenshot: z.boolean().optional(),
    link: z.boolean().optional(),
    model_name: z.string().nonempty('请选择模型'),
    format: z.array(z.string()).default([]),
    style: z.string().nonempty('请选择笔记生成风格'),
    extras: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    const { video_url, platform } = data

    if (platform === 'local') {
      if (!video_url || typeof video_url !== 'string') {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: '本地视频路径不能为空',
          path: ['video_url'],
        })
      }
    } else {
      try {
        const url = new URL(video_url)
        if (!(url.protocol === 'http:' || url.protocol === 'https:')) {
          throw new Error()
        }
      } catch {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: '请输入正确的视频链接',
          path: ['video_url'],
        })
      }
    }
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
  const platform = form.watch('platform')

  const onClose = () => {
    setShowFeatureHint(false)
  }
  const isGenerating = () => {
    console.log('🚀 isGenerating', getCurrentTask()?.status)
    return getCurrentTask()?.status != 'SUCCESS' && getCurrentTask()?.status != 'FAILED' && getCurrentTask()?.status != undefined
  }
  const handleFileUpload = async (file: File, onSuccess: (url: string) => void) => {
    if (!file) return
    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await uploadFile(formData)
      if (res.data.code === 0) {
        const uploadedUrl = res.data.data.url
        console.log('✅ 上传成功', uploadedUrl)

        onSuccess(uploadedUrl)
      }
    } catch (error) {
      console.error('上传失败', error)
      // 可以弹个 toast 或者提示上传失败
    }
  }
  // TODO 修复选择其他视频平台以后再选择本地视频还可以选择 Link 的问题
  const onSubmit = async (data: NoteFormValues) => {
    console.log('🎯 提交内容：', data)
    message.success('提交任务')

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
    <>
      <ScrollArea className="sm:h-[400px] md:h-[800px]">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-2">
            <div className="flex w-full items-center gap-2 py-1.5">
              <Button
                type="submit"
                className="bg-primary w-full sm:w-full"
                disabled={isGenerating()}
              >
                {isGenerating() && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {isGenerating() ? '正在生成…' : '生成笔记'}
              </Button>
            </div>
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
                          <SelectItem value="local">本地视频</SelectItem>
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
                        {form.watch('platform') === 'local' ? (
                          <div className="flex flex-col gap-2">
                            {/* 第一行：本地路径输入框 */}
                            <Input placeholder="请输入本地视频路径" {...field} className="w-full" />
                          </div>
                        ) : (
                          <Input placeholder="请输入视频网站链接" {...field} />
                        )}
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <FormField
                control={form.control}
                name="video_url"
                render={({ field }) => (
                  <FormItem className="w-full">
                    <FormControl>
                      {form.watch('platform') === 'local' ? (
                        <div
                          className="hover:border-primary flex h-40 w-full cursor-pointer items-center justify-center rounded-md border-2 border-dashed border-gray-300 transition-colors"
                          onDragOver={e => {
                            e.preventDefault()
                            e.stopPropagation()
                          }}
                          onDrop={e => {
                            e.preventDefault()
                            e.stopPropagation()
                            const file = e.dataTransfer.files?.[0]
                            if (file) {
                              handleFileUpload(file, uploadedUrl => {
                                field.onChange(uploadedUrl)
                              })
                            }
                          }}
                          onClick={() => {
                            const input = document.createElement('input')
                            input.type = 'file'
                            input.accept = 'video/*'
                            input.onchange = (e: any) => {
                              const file = e.target.files?.[0]
                              if (file) {
                                handleFileUpload(file, uploadedUrl => {
                                  field.onChange(uploadedUrl)
                                })
                              }
                            }
                            input.click()
                          }}
                        >
                          <div className="text-center text-sm text-gray-500">
                            <p className="mb-2">拖拽文件到这里上传</p>
                            <p className="text-xs text-gray-400">或点击选择文件</p>
                          </div>
                        </div>
                      ) : (
                        <></>
                      )}
                    </FormControl>
                    {/* ❗可以不要FormMessage，不然重复两次报错提示 */}
                  </FormItem>
                )}
              />
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
                            <p className="max-w-[200px] text-xs">
                              质量越高，下载体积越大，速度越慢
                            </p>
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
                            <p className="max-w-[200px] text-xs">
                              不同模型返回质量不同，可自行测试
                            </p>
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
                          <p className="text-xs">
                            选择要包含的笔记元素，比如时间戳、截图提示或总结
                          </p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </div>

                  <FormControl>
                    <div className="flex flex-wrap space-x-1.5">
                      {noteFormats.map(item => (
                        <label key={item.value} className="flex items-center space-x-2">
                          <Checkbox
                            checked={field.value?.includes(item.value)}
                            disabled={item.value === 'link' && platform === 'local'}
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
                        <Textarea placeholder={'笔记需要罗列出 xxx 关键点'} {...field} />

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
          </form>
        </Form>
      </ScrollArea>

      {/* 添加一些额外的说明或功能介绍 */}

      {/*<div className="bg-primary-light mt-6 rounded-lg p-4"></div>*/}
    </>
  )
}

export default NoteForm
