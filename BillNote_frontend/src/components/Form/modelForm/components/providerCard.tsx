import { Switch } from '@/components/ui/switch'
import { FC } from 'react'
import styles from './index.module.css'
import {useNavigate, useParams} from 'react-router-dom'
import AILogo from "@/components/Icons";
export interface IProviderCardProps {
  id: string
  providerName: string
  Icon: string
}
const ProviderCard: FC<IProviderCardProps> = ({ providerName, Icon, id }: IProviderCardProps) => {
  const navigate = useNavigate()
  const handleClick = () => {
    navigate(`/settings/model/${providerName}&id=${id}`)
  }
  const rawId= useParams();
  console.log('rawId',rawId)
  // @ts-ignore
  const { id: currentId } = useParams();
  const isActive = currentId === id
  return (
    <div
      onClick={() => {
        handleClick()
      }}
      className={
        styles.card + ' flex h-14 items-center justify-between rounded border border-[#f3f3f3] p-2'
          +(isActive ? ' bg-[#F0F0F0] font-semibold text-blue-600' : '')
      }
    >
      <div className="flex items-center text-lg">
        <div className="h-9 w-9 flex  items-center">
          <AILogo name={Icon}  />
        </div>
        <div className="font-semibold">{providerName}</div>
      </div>
      <div>
        <Switch />
      </div>
    </div>
  )
}
export default ProviderCard
