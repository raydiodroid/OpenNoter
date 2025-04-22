import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Form,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormDescription,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { useParams } from 'react-router-dom';
import { useProviderStore } from '@/store/providerStore';
import {useEffect, useState} from 'react';

// ✅ 表单校验 schema
const ProviderSchema = z.object({
  name: z.string().min(2, '名称不能少于 2 个字符'),
  apiKey: z.string().optional(),
  baseUrl: z.string().url('必须是合法 URL'),
  type: z.string(), // 只展示，不可改
});

type ProviderFormValues = z.infer<typeof ProviderSchema>;

const ProviderForm = () => {
  const rawId= useParams();
  console.log('rawId',rawId)
  // @ts-ignore
  const [providerName, idPart] = rawId.id.split('&');
  const [id,setId ]= useState(Number(idPart?.split('=')[1])) // => "1"
  const getProviderById = useProviderStore((state) => state.getProviderById);
  const provider = getProviderById(id);

  const form = useForm<ProviderFormValues>({
    resolver: zodResolver(ProviderSchema),
    defaultValues: {
      name: '',
      apiKey: '',
      baseUrl: '',
      type: '',
    },
  });

  useEffect(() => {
    console.log(provider)
    // if (provider) {
    //   form.reset({
    //     name: provider.name,
    //     apiKey: provider.apiKey,
    //     baseUrl: provider.baseUrl,
    //     type: provider.type,
    //   });
    // }
  }, [id,provider, form]);

  const isBuiltIn = provider?.type === 'built-in';

  const onSubmit = (values: ProviderFormValues) => {
    console.log('📝 提交表单数据:', values);
    // TODO: 提交接口 /update_provider
  };

  // if (!provider) return <div className="p-4">加载中...</div>;

  return (

      <Form {...form}>

        <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="w-full max-w-xl p-4 flex flex-col gap-4"
        >
          <div className="text-lg font-bold">模型供应商配置</div>

          {/* 名称 */}
          <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                  <FormItem className="flex items-center gap-4">
                    <FormLabel className="w-24 text-right">名称</FormLabel>
                    <FormControl>
                      <Input {...field} disabled={isBuiltIn} className="flex-1" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
              )}
          />

          {/* API Key */}
          <FormField
              control={form.control}
              name="apiKey"
              render={({ field }) => (
                  <FormItem className="flex items-center gap-4">
                    <FormLabel className="w-24 text-right">API Key</FormLabel>
                    <FormControl>
                      <Input placeholder={'sk-xxx'} {...field} className="flex-1" />
                    </FormControl>
                    <FormMessage />


                  </FormItem>



              )}
          />

          {/* Base URL */}
          <FormField
              control={form.control}
              name="baseUrl"
              render={({ field }) => (
                  <FormItem className="flex items-center gap-4">
                    <FormLabel className="w-24 text-right">API 代理地址</FormLabel>
                    <FormControl>
                      <Input {...field} className="flex-1" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
              )}
          />

          {/* 类型 */}
          <FormField
              control={form.control}
              name="type"
              render={({ field }) => (
                  <FormItem className="flex items-center gap-4">
                    <FormLabel className="w-24 text-right">类型</FormLabel>
                    <FormControl>
                      <Input {...field} disabled className="flex-1" />
                    </FormControl>
                  </FormItem>
              )}
          />

          <div className="pt-2">
            <Button type="submit" disabled={!form.formState.isDirty}>
              保存修改
            </Button>
          </div>
        </form>
      </Form>
  );
};

export default ProviderForm;