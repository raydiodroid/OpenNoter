/* NoteForm.tsx ---------------------------------------------------- */
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from '@/components/ui/form.tsx'
import { useEffect } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'

import { Info, Loader2 } from 'lucide-react'
import { message, Alert } from 'antd'

import { generateNote } from '@/services/note.ts'
import { uploadFile } from '@/services/upload.ts'
import { useTaskStore } from '@/store/taskStore'
import { useModelStore } from '@/store/modelStore'
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger} from "@/components/ui/tooltip.tsx";
import {Checkbox} from "@/components/ui/checkbox.tsx";
import {ScrollArea} from "@/components/ui/scroll-area.tsx";
import {Button} from "@/components/ui/button.tsx";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from "@/components/ui/select.tsx";
import {Input} from "@/components/ui/input.tsx";
import {Textarea} from "@/components/ui/textarea.tsx";

/* -------------------- 常量 -------------------- */
const noteFormats = [
  { label: '目录', value: 'toc' },
  { label: '原片跳转', value: 'link' },
  { label: '原片截图', value: 'screenshot' },
  { label: 'AI总结', value: 'summary' },
] as const

const noteStyles = [
  { label: '精简', value: 'minimal' },
  { label: '详细', value: 'detailed' },
  { label: '教程', value: 'tutorial' },
  { label: '学术', value: 'academic' },
  { label: '小红书', value: 'xiaohongshu' },
  { label: '生活向', value: 'life_journal' },
  { label: '任务导向', value: 'task_oriented' },
  { label: '商业风格', value: 'business' },
  { label: '会议纪要', value: 'meeting_minutes' },
] as const

/* -------------------- 校验 Schema -------------------- */
const formSchema = z.object({
  video_url: z.string(),
  platform: z.string().nonempty('请选择平台'),
  quality: z.enum(['fast', 'medium', 'slow']),
  screenshot: z.boolean().optional(),
  link: z.boolean().optional(),
  model_name: z.string().nonempty('请选择模型'),
  format: z.array(z.string()).default([]),
  style: z.string().nonempty('请选择笔记生成风格'),
  extras: z.string().optional(),
  video_understanding: z.boolean().optional(),
  video_interval: z.coerce.number().min(1).max(30).default(4).optional(),
  grid_size: z.tuple([
    z.coerce.number().min(1).max(10),
    z.coerce.number().min(1).max(10),
  ]).default([3, 3]).optional(),
}).superRefine(({ video_url, platform }, ctx) => {
  if (platform === 'local' || platform === 'douyin') {
    if (!video_url) {
      ctx.addIssue({ code: 'custom', message: '本地视频路径不能为空', path: ['video_url'] })
    }
  } else {
    try {
      const url = new URL(video_url)
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error()
    } catch {
      ctx.addIssue({ code: 'custom', message: '请输入正确的视频链接', path: ['video_url'] })
    }
  }
})

type NoteFormValues = z.infer<typeof formSchema>

/* -------------------- 可复用子组件 -------------------- */
const SectionHeader = ({ title, tip }: { title: string; tip?: string }) => (
    <div className="my-3 flex items-center justify-between">
      <h2 className="block">{title}</h2>
      {tip && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
              </TooltipTrigger>
              <TooltipContent className="text-xs">{tip}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
      )}
    </div>
)

const CheckboxGroup = ({
                         value = [], onChange, disabledMap,
                       }: {
  value?: string[]
  onChange: (v: string[]) => void
  disabledMap: Record<string, boolean>
}) => (
    <div className="flex flex-wrap space-x-1.5">
      {noteFormats.map(({ label, value: v }) => (
          <label key={v} className="flex items-center space-x-2">
            <Checkbox
                checked={value.includes(v)}
                disabled={disabledMap[v]}
                onCheckedChange={checked =>
                    onChange(checked ? [...value, v] : value.filter(x => x !== v))}
            />
            <span>{label}</span>
          </label>
      ))}
    </div>
)

/* -------------------- 主组件 -------------------- */
const NoteForm = () => {
  /* ---- 全局状态 ---- */
  const { addPendingTask, currentTaskId, getCurrentTask } = useTaskStore()
  const { loadEnabledModels, modelList, showFeatureHint, setShowFeatureHint } = useModelStore()

  /* ---- 表单 ---- */
  const form = useForm<NoteFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      platform: 'bilibili',
      quality: 'medium',
      model_name: modelList[0]?.model_name || '',
      style: 'minimal',
      video_interval: 4,
      grid_size: [3, 3],
      format: [],
    },
  })

  /* ---- 派生状态（只 watch 一次，提高性能） ---- */
  const platform = useWatch({ control: form.control, name: 'platform' }) as string
  const videoUnderstandingEnabled = useWatch({ control: form.control, name: 'video_understanding' })

  /* ---- 副作用 ---- */
  useEffect(() => {
    loadEnabledModels()

  return}, [])

  /* ---- 帮助函数 ---- */
  const isGenerating = () =>
      !['SUCCESS', 'FAILED', undefined].includes(getCurrentTask()?.status)

  const handleFileUpload = async (file: File, cb: (url: string) => void) => {
    const formData = new FormData()
    formData.append('file', file)
    try {
      const { data } = await uploadFile(formData)
      if (data.code === 0) cb(data.data.url)
    } catch (err) {
      console.error('上传失败:', err)
      message.error('上传失败，请重试')
    }
  }

  const onSubmit = async (values: NoteFormValues) => {
    const payload:NoteFormValues = {
      ...values,
      provider_id: modelList.find(m => m.model_name === values.model_name)!.provider_id,
    }
    message.success('已提交任务')
    const { data } = await generateNote(payload)
    addPendingTask(data.task_id, values.platform, payload)
  }

  /* -------------------- 渲染 -------------------- */
  return (
      <ScrollArea className="sm:h-[400px] md:h-[800px]">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            {/* 顶部按钮 */}
            <Button type="submit" className="w-full bg-primary" disabled={isGenerating()}>
              {isGenerating() && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {isGenerating() ? '正在生成…' : '生成笔记'}
            </Button>

            {/* 视频链接 & 平台 */}
            <SectionHeader title="视频链接" tip="支持 B 站、YouTube 等平台" />
            <div className="flex gap-2">
              {/* 平台选择 */}
              <FormField
                  control={form.control}
                  name="platform"
                  render={({ field }) => (
                      <FormItem>
                        <Select onValueChange={field.onChange} defaultValue={field.value}>
                          <FormControl>
                            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            <SelectItem value="bilibili">哔哩哔哩</SelectItem>
                            <SelectItem value="youtube">YouTube</SelectItem>
                            <SelectItem value="douyin">抖音</SelectItem>
                            <SelectItem value="local">本地视频</SelectItem>
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                  )}
              />
              {/* 链接输入 / 上传框 */}
              <FormField
                  control={form.control}
                  name="video_url"
                  render={({ field }) => (
                      <FormItem className="flex-1">
                        {platform === 'local' ? (
                            <>
                              <Input placeholder="请输入本地视频路径" {...field} />
                              <div
                                  className="hover:border-primary mt-2 flex h-40 cursor-pointer items-center justify-center rounded-md border-2 border-dashed border-gray-300 transition-colors"
                                  onDragOver={e => { e.preventDefault(); e.stopPropagation() }}
                                  onDrop={e => {
                                    e.preventDefault()
                                    const file = e.dataTransfer.files?.[0]
                                    if (file) handleFileUpload(file, field.onChange)
                                  }}
                                  onClick={() => {
                                    const input = document.createElement('input')
                                    input.type = 'file'
                                    input.accept = 'video/*'
                                    input.onchange = e => {
                                      const file = (e.target as HTMLInputElement).files?.[0]
                                      if (file) handleFileUpload(file, field.onChange)
                                    }
                                    input.click()
                                  }}
                              >
                                <p className="text-center text-sm text-gray-500">
                                  拖拽文件到这里上传 <br />
                                  <span className="text-xs text-gray-400">或点击选择文件</span>
                                </p>
                              </div>
                            </>
                        ) : (
                            <Input placeholder="请输入视频网站链接" {...field} />
                        )}
                        <FormMessage />
                      </FormItem>
                  )}
              />
            </div>

            {/* 模型选择 */}
            <FormField
                control={form.control}
                name="model_name"
                render={({ field }) => (
                    <FormItem>
                      <SectionHeader title="模型选择" tip="不同模型效果不同，建议自行测试" />
                      <Select onValueChange={field.onChange} defaultValue={field.value}>
                        <FormControl>
                          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {modelList.map(m => (
                              <SelectItem key={m.model_name} value={m.model_name}>
                                {m.model_name}
                              </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                )}
            />

            {/* 视频理解 */}
            <SectionHeader title="视频理解" tip="将视频截图发给多模态模型辅助分析" />
            <div className="flex flex-col gap-2">

              <FormField
                  control={form.control}
                  name="video_understanding"
                  render={({ field }) => (
                      <FormItem>
                        <div  className="flex items-center gap-2">
                        <FormLabel>启用</FormLabel>
                        <Checkbox
                            checked={videoUnderstandingEnabled}
                            onCheckedChange={v => form.setValue('video_understanding', v)}
                        />
                        </div>
                        <FormMessage />
                      </FormItem>
                  )}
              />
              <Alert
                  type="info"
                  message="推荐多模态模型：qwen2.5-vl-72b-instruct / gpt-4o"
                  className="text-sm"
              />
              <div className="grid grid-cols-2 gap-4">
                {/* 采样间隔 */}
                <FormField
                    control={form.control}
                    name="video_interval"
                    render={({ field }) => (
                        <FormItem>
                          <FormLabel>采样间隔（秒）</FormLabel>
                          <Input
                              disabled={!videoUnderstandingEnabled}
                              type="number"
                              {...field}
                          />
                          <FormMessage />
                        </FormItem>
                    )}
                />
                {/* 拼图大小 */}
                <FormField
                    control={form.control}
                    name="grid_size"
                    render={({ field }) => (
                        <FormItem>
                          <FormLabel>拼图尺寸（列 × 行）</FormLabel>
                          <div className="flex items-center space-x-2">
                            <Input
                                disabled={!videoUnderstandingEnabled}
                                type="number"
                                value={field.value?.[0] || 3}
                                onChange={e =>
                                    field.onChange([+e.target.value, field.value?.[1] || 3])}
                                className="w-16"
                            />
                            <span>x</span>
                            <Input
                                disabled={!videoUnderstandingEnabled}
                                type="number"
                                value={field.value?.[1] || 3}
                                onChange={e =>
                                    field.onChange([field.value?.[0] || 3, +e.target.value])}
                                className="w-16"
                            />
                          </div>
                          <FormMessage />
                        </FormItem>
                    )}
                />
              </div>
            </div>

            {/* 笔记风格 */}
            <FormField
                control={form.control}
                name="style"
                render={({ field }) => (
                    <FormItem>
                      <SectionHeader title="笔记风格" tip="选择生成笔记的呈现风格" />
                      <Select onValueChange={field.onChange} defaultValue={field.value}>
                        <FormControl>
                          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {noteStyles.map(({ label, value }) => (
                              <SelectItem key={value} value={value}>{label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                )}
            />

            {/* 笔记格式 */}
            <FormField
                control={form.control}
                name="format"
                render={({ field }) => (
                    <FormItem>
                      <SectionHeader title="笔记格式" tip="选择要包含的笔记元素" />
                      <CheckboxGroup
                          value={field.value}
                          onChange={field.onChange}
                          disabledMap={{
                            link: platform === 'local',
                            screenshot: !videoUnderstandingEnabled,
                          }}
                      />
                      <FormMessage />
                    </FormItem>
                )}
            />

            {/* 备注 */}
            <FormField
                control={form.control}
                name="extras"
                render={({ field }) => (
                    <FormItem>
                      <SectionHeader title="备注" tip="可在 Prompt 结尾附加自定义说明" />
                      <Textarea placeholder="笔记需要罗列出 xxx 关键点…" {...field} />
                      <FormMessage />
                    </FormItem>
                )}
            />
          </form>
        </Form>
      </ScrollArea>
  )
}

export default NoteForm
