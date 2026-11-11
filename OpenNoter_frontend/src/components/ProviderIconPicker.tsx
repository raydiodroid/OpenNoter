import type { ReactNode } from 'react'
import { Picker, PickerItem, Text } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

const label = style({ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 })
const icon = style({
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 20,
  height: 20,
  flexShrink: 0,
})

/** Custom logos belong inside the label slot, not in Picker's reserved grid columns. */
export function ProviderIconPicker({
  value,
  onChange,
  options,
  renderIcon,
  isDisabled,
}: {
  value: string
  onChange: (value: string) => void
  options: readonly { id: string; label: string }[]
  renderIcon: (name: string) => ReactNode
  isDisabled?: boolean
}) {
  const choices = options.some(option => option.id === value)
    ? options
    : [{ id: value, label: '当前图标' }, ...options]
  return (
    <Picker
      label="图标"
      selectedKey={value}
      onSelectionChange={key => onChange(String(key))}
      isDisabled={isDisabled}
    >
      {choices.map(option => (
        <PickerItem key={option.id} id={option.id} textValue={option.label}>
          <Text slot="label">
            <span className={label}>
              <span className={icon} aria-hidden="true">
                {renderIcon(option.id)}
              </span>
              <span>{option.label}</span>
            </span>
          </Text>
        </PickerItem>
      ))}
    </Picker>
  )
}
