import { useNavigate } from 'react-router-dom'
import { Button, Content, Heading, IllustratedMessage } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import NoSearchResults from '@react-spectrum/s2/illustrations/linear/NoSearchResults'

const page = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: 'full',
  padding: 32,
  backgroundColor: 'base',
})

const stack = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 16,
  textAlign: 'center',
})

export default function NotFoundPage() {
  const navigate = useNavigate()

  return (
    <div className={page}>
      <div className={stack}>
        <IllustratedMessage>
          <NoSearchResults />
          <Heading>你好像走丢了</Heading>
          <Content>这条地址不存在，检查一下网址，或者回工作台继续。</Content>
        </IllustratedMessage>
        <Button variant="accent" onPress={() => navigate('/')}>
          返回工作台
        </Button>
      </div>
    </div>
  )
}
