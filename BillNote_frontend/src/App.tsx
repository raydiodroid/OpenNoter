import './App.css'
import { HomePage } from './pages/HomePage/Home.tsx'
import { useTaskPolling } from '@/hooks/useTaskPolling.ts'
import SettingPage from './pages/SettingPage/index.tsx'
import { BrowserRouter, Navigate, Routes } from 'react-router-dom'
import { Route } from 'react-router-dom'
import Index from '@/pages/Index.tsx'
import NotFoundPage from '@/pages/NotFoundPage' //
import Model from '@/pages/SettingPage/Model.tsx'
import Transcriber from '@/pages/SettingPage/transcriber.tsx'
import ProviderForm from '@/components/Form/modelForm/Form.tsx'
import StepBar from '@/pages/HomePage/components/StepBar.tsx'
import Downloading from '@/components/Lottie/download.tsx'
function App() {
  useTaskPolling(3000) // 每 3 秒轮询一次
  const steps = [
    { label: '解析链接', key: 'PARSING', icon: <Downloading /> },
    { label: '下载音频', key: 'DOWNLOADING' },
    { label: '转写文字', key: 'TRANSCRIBING' },
    { label: '总结内容', key: 'SUMMARIZING' },
    { label: '保存完成', key: 'SUCCESS' },
  ]
  return (
    <>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Index />}>
            <Route index element={<HomePage />} />
            <Route path="settings" element={<SettingPage />}>
              <Route index element={<Navigate to="model" replace />} />
              <Route path="model" element={<Model />}>
                <Route path="new" element={<ProviderForm isCreate />} />
                {/*<Route index element={<Navigate to="openai" replace />} />*/}
                <Route path=":id" element={<ProviderForm />} />
              </Route>
              <Route path="transcriber" elment={<Transcriber />}></Route>
              <Route path="*" element={<NotFoundPage />} />
            </Route>
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </>
  )
}

export default App
