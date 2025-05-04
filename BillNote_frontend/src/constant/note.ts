/* -------------------- 常量 -------------------- */
export const noteFormats = [
    { label: '目录', value: 'toc' },
    { label: '原片跳转', value: 'link' },
    { label: '原片截图', value: 'screenshot' },
    { label: 'AI总结', value: 'summary' },
] as const

export const noteStyles = [
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