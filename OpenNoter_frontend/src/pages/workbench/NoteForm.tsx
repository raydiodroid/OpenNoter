import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ActionButton,
  Button,
  Checkbox,
  CheckboxGroup,
  Link,
  NumberField,
  Picker,
  PickerItem,
  Switch,
  Text,
  TextArea,
  TextField,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import UploadIcon from '@react-spectrum/s2/icons/Upload'
import AddIcon from '@react-spectrum/s2/icons/Add'
import AlertIcon from '@react-spectrum/s2/icons/AlertTriangle'

import { PlatformMark } from '@/components/Brand'
import { NOTE_FORMATS, NOTE_STYLES, PLATFORMS } from '@/constants'
import { useModelStore } from '@/store/modelStore'
import { isRunningStatus, selectCurrentTask, useTaskStore } from '@/store/taskStore'
import { generateNote, uploadFile } from '@/services/note'
import { withScheme } from '@/lib/format'
import { toast } from '@/lib/toast'
import type { TaskFormData } from '@/types'
import { Notice } from '@/components/Notice'

interface FormValues {
  platform: string
  video_url: string
  model_id: string
  style: string
  format: string[]
  extras: string
  video_understanding: boolean
  video_interval: number
  grid_columns: number
  grid_rows: number
}

const DEFAULT_VALUES: FormValues = {
  platform: 'bilibili',
  video_url: '',
  model_id: '',
  style: 'minimal',
  format: [],
  extras: '',
  video_understanding: false,
  video_interval: 6,
  grid_columns: 2,
  grid_rows: 2,
}

const form = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 24,
  paddingX: 16,
  paddingTop: 24,
  paddingBottom: 32,
})

const section = style({
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
})

const sectionTitle = style({
  font: 'title',
  color: 'neutral',
  margin: 0,
})

const twoCol = style({ display: 'grid', gridTemplateColumns: ['1fr', '1fr'], gap: 12 })

const platformOption = style({ display: 'inline-flex', alignItems: 'center', gap: 8 })

const dropzone = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
  minHeight: 112,
  padding: 16,
  borderRadius: 'lg',
  borderWidth: 1,
  borderStyle: 'dashed',
  borderColor: 'gray-400',
  backgroundColor: 'base',
  cursor: 'pointer',
  transition: 'default',
})

const dropzoneActive = style({
  borderColor: 'accent-900',
  backgroundColor: 'accent-100',
})

const dropHint = style({ font: 'detail-sm', color: 'neutral-subdued', textAlign: 'center' })

const dropTitle = style({ font: 'ui', color: 'neutral', fontWeight: 'bold' })

const submitRow = style({
  position: 'sticky',
  bottom: 0,
  display: 'flex',
  gap: 8,
  marginX: -16,
  paddingX: 16,
  paddingY: 12,
  backgroundColor: 'base',
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 1,
  borderBottomWidth: 0,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const inlineNote = style({
  font: 'detail',
  color: 'neutral-subdued',
  margin: 0,
})

export default function NoteForm() {
  const navigate = useNavigate()
  const currentTask = useTaskStore(selectCurrentTask)
  const addPendingTask = useTaskStore(s => s.addPendingTask)
  const updateTask = useTaskStore(s => s.updateTask)
  const setCurrentTask = useTaskStore(s => s.setCurrentTask)

  const models = useModelStore(s => s.enabled)
  const loadEnabled = useModelStore(s => s.loadEnabled)

  const [values, setValues] = useState<FormValues>(DEFAULT_VALUES)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    loadEnabled()
  }, [loadEnabled])

  const isEditing = Boolean(currentTask)
  const running = currentTask ? isRunningStatus(currentTask.status) : false
  const isLocal = values.platform === 'local'

  // 选中已有任务时回填表单（编辑 / 重新生成都复用同一份参数）
  useEffect(() => {
    if (!currentTask) {
      setValues(DEFAULT_VALUES)
      return
    }
    const data = currentTask.formData
    setValues({
      platform: data.platform || 'bilibili',
      video_url: data.video_url || '',
      model_id: models.find(m => m.model_name === data.model_name)?.id?.toString() ?? '',
      style: data.style || 'minimal',
      format: Array.isArray(data.format) ? data.format : [],
      extras: data.extras || '',
      video_understanding: Boolean(data.video_understanding),
      video_interval: data.video_interval || 6,
      grid_columns: data.grid_size?.[0] || 2,
      grid_rows: data.grid_size?.[1] || 2,
    })
    // 只在切换任务时回填，避免用户输入被覆盖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTask?.id])

  // 模型列表异步到达后：优先按任务里记录的模型名回填，否则选第一个
  useEffect(() => {
    if (models.length === 0) return
    setValues(prev => {
      if (prev.model_id && models.some(model => String(model.id) === prev.model_id)) return prev
      const preferred = currentTask?.formData.model_name
      const match = preferred ? models.find(model => model.model_name === preferred) : undefined
      return { ...prev, model_id: String((match ?? models[0]).id) }
    })
  }, [models, currentTask?.formData.model_name])

  const patch = (next: Partial<FormValues>) => {
    setValues(prev => ({ ...prev, ...next }))
  }

  const handleUpload = async (file: File) => {
    const body = new FormData()
    body.append('file', file)
    setUploading(true)
    try {
      const res = await uploadFile(body)
      patch({ video_url: res.url })
      toast.success('视频已上传')
    } catch {
      // 拦截器已提示
    } finally {
      setUploading(false)
    }
  }

  const validate = () => {
    const next: Record<string, string> = {}
    if (!values.video_url.trim()) {
      next.video_url = isLocal ? '请先选择或填写本地视频路径' : '请粘贴视频链接'
    } else if (!isLocal) {
      try {
        const url = new URL(withScheme(values.video_url))
        if (!['http:', 'https:'].includes(url.protocol)) throw new Error('bad')
      } catch {
        next.video_url = '请输入正确的视频链接'
      }
    }
    if (!values.model_id) next.model_id = '请选择模型'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (running || submitting) return
    if (!validate()) return

    const model = models.find(m => String(m.id) === values.model_id)
    if (!model) {
      setErrors({ model_id: '所选模型已不可用，请重新选择' })
      return
    }

    const payload: TaskFormData = {
      video_url: isLocal ? values.video_url.trim() : withScheme(values.video_url),
      platform: values.platform,
      quality: 'medium',
      model_name: model.model_name,
      provider_id: model.provider_id,
      style: values.style,
      format: values.format,
      extras: values.extras.trim() || undefined,
      video_understanding: values.video_understanding,
      video_interval: values.video_understanding ? values.video_interval : 0,
      grid_size: values.video_understanding ? [values.grid_columns, values.grid_rows] : [],
      screenshot: values.format.includes('screenshot'),
      link: values.format.includes('link'),
    }

    setSubmitting(true)
    try {
      if (currentTask) {
        await generateNote({ ...payload, task_id: currentTask.id })
        updateTask(currentTask.id, { status: 'PENDING', formData: payload })
        toast.info('已重新提交生成任务')
      } else {
        const res = await generateNote(payload)
        addPendingTask(res.task_id, payload)
        toast.success('任务已提交')
      }
    } catch (error: any) {
      if (error?.data?.reason === 'transcriber_model_not_ready') {
        const downloading = error.data.downloading
        toast.error(downloading ? '转写模型正在下载中，请稍候再试' : '转写模型尚未下载，请先下载')
        if (!downloading) navigate('/settings/transcriber')
      } else {
        toast.error(error?.msg || '提交失败，请稍后再试')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className={form} onSubmit={handleSubmit}>
      {/* ── 视频来源 ── */}
      <section className={section}>
        <h2 className={sectionTitle}>视频来源</h2>

        <Picker
          label="平台"
          selectedKey={values.platform}
          onSelectionChange={key => patch({ platform: String(key) })}
          isDisabled={isEditing}
        >
          {PLATFORMS.map(platform => (
            <PickerItem key={platform.value} id={platform.value} textValue={platform.label}>
              <Text slot="label">
                <span className={platformOption}>
                  <PlatformMark Logo={platform.Logo} size={16} />
                  {platform.label}
                </span>
              </Text>
            </PickerItem>
          ))}
        </Picker>

        <TextField
          label={isLocal ? '本地视频路径' : '视频链接'}
          value={values.video_url}
          onChange={video_url => patch({ video_url })}
          isDisabled={isEditing}
          isInvalid={Boolean(errors.video_url)}
          errorMessage={errors.video_url}
          placeholder={isLocal ? '选择文件后自动填入' : '粘贴视频网站链接'}
          isRequired
        />

        {isLocal && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*"
              hidden
              onChange={event => {
                const file = event.target.files?.[0]
                if (file) handleUpload(file)
                event.target.value = ''
              }}
            />
            <div
              className={
                dragOver ? `${dropzone} ${dropzoneActive} focus-ring` : `${dropzone} focus-ring`
              }
              role="button"
              tabIndex={0}
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') fileInputRef.current?.click()
              }}
              onDragOver={event => {
                event.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={event => {
                event.preventDefault()
                setDragOver(false)
                const file = event.dataTransfer.files?.[0]
                if (file) handleUpload(file)
              }}
            >
              <UploadIcon />
              <span className={dropTitle}>{uploading ? '上传中…' : '拖入视频，或点击选择'}</span>
              <span className={dropHint}>本地转写需要先下载对应 Whisper 模型</span>
            </div>
          </>
        )}
      </section>

      {/* ── 生成设置 ── */}
      <section className={section}>
        <h2 className={sectionTitle}>生成设置</h2>

        {models.length > 0 ? (
          <div className={twoCol}>
            <Picker
              label="模型"
              selectedKey={values.model_id || null}
              onSelectionChange={key => {
                patch({ model_id: String(key) })
                setErrors(prev => ({ ...prev, model_id: '' }))
              }}
              isInvalid={Boolean(errors.model_id)}
              errorMessage={errors.model_id}
            >
              {models.map(model => (
                <PickerItem
                  key={String(model.id)}
                  id={String(model.id)}
                  textValue={model.model_name}
                >
                  {model.model_name}
                </PickerItem>
              ))}
            </Picker>

            <Picker
              label="笔记风格"
              selectedKey={values.style}
              onSelectionChange={key => patch({ style: String(key) })}
            >
              {NOTE_STYLES.map(style => (
                <PickerItem key={style.value} id={style.value} textValue={style.label}>
                  {style.label}
                </PickerItem>
              ))}
            </Picker>
          </div>
        ) : (
          <>
            <Notice variant="notice">
              <AlertIcon />
              <Text>还没有可用模型，先添加一个供应商并启用模型。</Text>
              <Link onPress={() => navigate('/settings/ai')}>去添加模型</Link>
            </Notice>

            <Picker
              label="笔记风格"
              selectedKey={values.style}
              onSelectionChange={key => patch({ style: String(key) })}
            >
              {NOTE_STYLES.map(style => (
                <PickerItem key={style.value} id={style.value} textValue={style.label}>
                  {style.label}
                </PickerItem>
              ))}
            </Picker>
          </>
        )}
      </section>

      {/* ── 视频理解 ── */}
      <section className={section}>
        <h2 className={sectionTitle}>视频理解</h2>
        <Switch
          isSelected={values.video_understanding}
          onChange={video_understanding => patch({ video_understanding })}
        >
          启用多模态理解
        </Switch>
        <p className={inlineNote}>按间隔截取关键帧发给多模态模型，费用更高但理解更准。</p>

        {values.video_understanding ? (
          <>
            <NumberField
              label="采样间隔（秒）"
              value={values.video_interval}
              onChange={value => patch({ video_interval: Number.isFinite(value) ? value : 6 })}
              minValue={1}
              maxValue={30}
              step={1}
            />
            <div className={twoCol}>
              <NumberField
                label="拼图列数"
                value={values.grid_columns}
                onChange={value => patch({ grid_columns: Number.isFinite(value) ? value : 2 })}
                minValue={1}
                maxValue={10}
                step={1}
              />
              <NumberField
                label="拼图行数"
                value={values.grid_rows}
                onChange={value => patch({ grid_rows: Number.isFinite(value) ? value : 2 })}
                minValue={1}
                maxValue={10}
                step={1}
              />
            </div>
          </>
        ) : null}
      </section>

      {/* ── 输出内容 ── */}
      <section className={section}>
        <h2 className={sectionTitle}>输出内容</h2>
        <CheckboxGroup
          label="笔记里要包含哪些元素"
          value={values.format}
          onChange={format => patch({ format })}
          orientation="horizontal"
        >
          {NOTE_FORMATS.map(item => (
            <Checkbox
              key={item.value}
              value={item.value}
              isDisabled={
                (item.value === 'link' && isLocal) ||
                (item.value === 'screenshot' && !values.video_understanding)
              }
            >
              {item.label}
            </Checkbox>
          ))}
        </CheckboxGroup>
        <p className={inlineNote}>
          「原片截图」依赖视频理解；本地视频没有在线原片地址，因此不能插入跳转链接。
        </p>
      </section>

      {/* ── 备注 ── */}
      <section className={section}>
        <h2 className={sectionTitle}>备注</h2>
        <TextArea
          label="补充要求"
          value={values.extras}
          onChange={extras => patch({ extras })}
          placeholder="例如：重点整理版本更新内容，忽略活动推广…"
        />
      </section>

      <div className={submitRow}>
        <Button type="submit" variant="accent" isDisabled={running || submitting}>
          {running ? '正在生成…' : isEditing ? '重新生成' : '生成笔记'}
        </Button>
        {isEditing && (
          <ActionButton onPress={() => setCurrentTask(null)}>
            <AddIcon />
            <Text>新建笔记</Text>
          </ActionButton>
        )}
      </div>
    </form>
  )
}
