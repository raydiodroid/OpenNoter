import ProviderCard from '@/components/Form/modelForm/components/providerCard.tsx'
import { Button } from '@/components/ui/button.tsx'
import { useProviderStore } from '@/store/providerStore'

const Provider = () => {
    const providers = useProviderStore(state => state.provider)


  return (
    <div className="flex flex-col gap-2">
      <div className={'search flex gap-1 py-1.5'}>
        <Button type={'button'} className="w-full">
          添加模型供应商
        </Button>
      </div>
      <div className="text-sm font-light">模型供应商列表</div>
      <div>
        {providers &&
            providers.map((provider, index) => {
            return (
              <ProviderCard
                key={index}
                providerName={provider.name}
                Icon={provider.logo}
                id={provider.id}
              />
            )
          })}
      </div>
    </div>
  )
}
export default Provider
