import type { ReactNode } from 'react'
import { Button, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import AddIcon from '@react-spectrum/s2/icons/Add'
import { Panel } from '@/components/Page'

const list = style({ display: 'flex', flexDirection: 'column', gap: 2, padding: 8 })
const addRow = style({ display: 'flex', paddingTop: 8 })
const fullWidth = style({ width: 'full' })

export function ProviderListPanel({
  children,
  onCreate,
  isDisabled,
}: {
  children: ReactNode
  onCreate: () => void
  isDisabled?: boolean
}) {
  return (
    <Panel padded={false}>
      <div className={list} aria-label="供应商列表">
        {children}
        <div className={addRow}>
          <Button variant="secondary" onPress={onCreate} isDisabled={isDisabled} styles={fullWidth}>
            <AddIcon />
            <Text>添加供应商</Text>
          </Button>
        </div>
      </div>
    </Panel>
  )
}
