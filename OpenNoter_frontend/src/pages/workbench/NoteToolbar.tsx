import { ActionButton, Picker, PickerItem, Text, ToggleButton } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import ChartIcon from '@react-spectrum/s2/icons/ViewGrid'
import ChatIcon from '@react-spectrum/s2/icons/Comment'
import CopyIcon from '@react-spectrum/s2/icons/Copy'
import DownloadIcon from '@react-spectrum/s2/icons/Download'
import TranscriptIcon from '@react-spectrum/s2/icons/Transcript'
import type { MarkdownVersion } from '@/types'

const bar = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 12,
  height: 48,
  paddingX: 24,
  boxSizing: 'border-box',
  backgroundColor: 'base',
  flexShrink: 0,
  borderStyle: 'solid',
  borderColor: 'gray-200',
  borderTopWidth: 0,
  borderBottomWidth: 1,
  borderStartWidth: 0,
  borderEndWidth: 0,
})

const group = style({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  flexWrap: 'wrap',
})

export default function NoteToolbar({
  versions,
  currentVerId,
  onVersionChange,
  view,
  onViewChange,
  transcriptOpen,
  onToggleTranscript,
  chatOpen,
  onToggleChat,
  onCopy,
  onExport,
}: {
  versions: MarkdownVersion[]
  currentVerId: string
  onVersionChange: (id: string) => void
  view: 'doc' | 'map'
  onViewChange: (view: 'doc' | 'map') => void
  transcriptOpen: boolean
  onToggleTranscript: () => void
  chatOpen: boolean
  onToggleChat: () => void
  onCopy: () => void
  onExport: () => void
}) {
  return (
    <div className={bar}>
      <div className={group}>
        {versions.length > 1 ? (
          <Picker
            aria-label="选择版本"
            size="S"
            selectedKey={currentVerId}
            onSelectionChange={key => onVersionChange(String(key))}
          >
            {versions.map((version, index) => {
              const label = `版本 ${versions.length - index}`
              return (
                <PickerItem key={version.ver_id} id={version.ver_id} textValue={label}>
                  {label}
                </PickerItem>
              )
            })}
          </Picker>
        ) : null}
      </div>

      <div className={group}>
        <ToggleButton
          size="S"
          isQuiet
          isSelected={view === 'map'}
          onChange={isSelected => onViewChange(isSelected ? 'map' : 'doc')}
        >
          <ChartIcon />
          <Text>思维导图</Text>
        </ToggleButton>
        <ActionButton size="S" isQuiet aria-label="复制全文" onPress={onCopy}>
          <CopyIcon />
          <Text>复制全文</Text>
        </ActionButton>
        <ActionButton size="S" isQuiet aria-label="导出 Markdown" onPress={onExport}>
          <DownloadIcon />
          <Text>导出 Markdown</Text>
        </ActionButton>
        <ToggleButton
          size="S"
          isQuiet
          isSelected={transcriptOpen}
          onChange={onToggleTranscript}
          isDisabled={view === 'map'}
        >
          <TranscriptIcon />
          <Text>原文参照</Text>
        </ToggleButton>
        <ToggleButton size="S" isQuiet isSelected={chatOpen} onChange={onToggleChat}>
          <ChatIcon />
          <Text>AI 问答</Text>
        </ToggleButton>
      </div>
    </div>
  )
}
