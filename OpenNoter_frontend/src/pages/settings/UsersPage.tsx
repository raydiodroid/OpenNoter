import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActionButton,
  AlertDialog,
  Badge,
  Cell,
  Column,
  DialogTrigger,
  Picker,
  PickerItem,
  Row,
  TableBody,
  TableHeader,
  TableView,
  Text,
} from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }
import DeleteIcon from '@react-spectrum/s2/icons/Delete'
import UserGroup from '@react-spectrum/s2/illustrations/linear/UserGroup'

import { EmptyState, Page, PageHeader, Section } from '@/components/Page'
import { deleteUser, listUsers, updateUser, type IUser } from '@/services/auth'
import { useAuthStore } from '@/store/authStore'
import { formatShortTime } from '@/lib/format'
import { toast } from '@/lib/toast'

type RowData = IUser & { created_at: string | null; last_login_at: string | null }

const muted = style({ font: 'detail', color: 'neutral-subdued', margin: 0 })
const userCell = style({ display: 'flex', flexDirection: 'column', gap: 0, minWidth: 0 })
const userName = style({
  font: 'ui',
  color: 'neutral',
  fontWeight: 'bold',
  lineHeight: '[18px]',
  truncate: true,
})
const userMail = style({
  font: 'detail-sm',
  color: 'neutral-subdued',
  lineHeight: '[16px]',
  truncate: true,
})
const cellText = style({ font: 'ui', color: 'neutral-subdued' })
const actions = style({ display: 'flex', justifyContent: 'end', gap: 8 })

/**
 * 角色下拉框：表格里行内改权限。
 *
 * 按 Spectrum 2 的规范，这里该用 S2 `Picker`（下拉框）而不是给标签套按钮：
 * - 选值控件是 Picker，quiet 变体 —— Spectrum 明确说 quiet picker 适合
 *   「表格 / 网格」这类一行一个控件的密集布局（默认样式有底色，表格里会显得啰嗦）。
 * - 标签是静态元数据（`Badge`），不该自己变成按钮；这里用 Picker 官方的
 *   `renderValue` 把选中值渲染成角色标签，保留「管理员带强调色」的视觉语言。
 * - 表格里不再重复一列「角色」标签，靠列头 + `aria-label` 说明这一格是什么。
 */
const ROLE_OPTIONS = [
  { id: 'admin', label: '管理员' },
  { id: 'user', label: '普通用户' },
]

const userNameOf = (user: RowData) => user.name || user.email.split('@')[0]

/** 表格铺满可用宽度（官方 TableView 示例的写法） */
const tableStyles = style({ width: 'full' })

/**
 * 可点的状态 Badge：外层按钮不画框，只提供点击与焦点；宽度随内容。
 *
 * 注意：`style()` 宏一旦带条件值（isHovered 之类）就返回**函数**，只能交给组件的
 * render prop；直接当 className 用会被字符串化、样式全失效（会露出浏览器原生按钮的灰框）。
 * 所以这里只写无条件值，hover / disabled 的表现放 `global.css` 的 `.status-chip`。
 */
/** 角色 Badge 统一宽度：「管理员」和「普通用户」字数不同，宽度不一致会显得参差 */
const roleBadgeStyles = style({ width: 84 })

const statusButton = style({
  display: 'inline-flex',
  width: 'fit',
  padding: 0,
  borderStyle: 'none',
  backgroundColor: 'transparent',
  borderRadius: 'full',
  transition: 'default',
})

/**
 * 角色标签：管理员用 accent，普通用户用 gray。
 *
 * 两种角色共用同一个组件、同一个 `fillStyle="subtle"`，只用 `variant` 区分强调程度 ——
 * 这是 DESIGN-AUDIT §17.3 定的规则（避免「只有一种取值有底色」的不一致）。
 * 两个取值都固定 `width: 84`：「管理员」三个字、「普通用户」四个字，
 * 内容宽度不同会让这一列的标签参差不齐；给死宽度后两行左缘右缘都对齐。
 */
function RoleBadge({ isSuperuser }: { isSuperuser: boolean }) {
  return (
    <Badge
      variant={isSuperuser ? 'accent' : 'gray'}
      fillStyle="subtle"
      styles={roleBadgeStyles}
      // Badge 默认左对齐，固定宽度后两种角色文字会一左一右；居中对齐才像同一列
      UNSAFE_style={{ justifyContent: 'center' }}
    >
      {isSuperuser ? '管理员' : '普通用户'}
    </Badge>
  )
}

function RolePicker({
  user,
  isSelf,
  onChange,
}: {
  user: RowData
  isSelf: boolean
  onChange: (isSuperuser: boolean) => void
}) {
  return (
    <Picker
      // 表格里不重复列头，标签交给 aria-label
      label={undefined}
      // S2 Picker 没有「隐藏下拉箭头」的开关（Chevron 是它结构的一部分），
      // 这里挂一个类名，由 global.css 里的 .role-picker 把箭头宽度和图标一起收掉
      UNSAFE_className="role-picker"
      aria-label={`${userNameOf(user)} 的角色`}
      isQuiet
      size="M"
      items={ROLE_OPTIONS}
      // 不能降级自己，避免把最后一个管理员锁在门外
      isDisabled={isSelf}
      selectedKey={user.is_superuser ? 'admin' : 'user'}
      onSelectionChange={key => {
        if (key == null) return
        const isSuperuser = String(key) === 'admin'
        // 选回当前角色不发请求
        if (isSuperuser !== user.is_superuser) onChange(isSuperuser)
      }}
      renderValue={items => <RoleBadge isSuperuser={items[0]?.id === 'admin'} />}
    >
      {/* 选项保持纯文本：Badge 放进选项会被列表项的截断样式压成竖排一列。 */}
      {item => (
        <PickerItem id={item.id} textValue={item.label}>
          {item.label}
        </PickerItem>
      )}
    </Picker>
  )
}

/**
 * 状态列：一枚可点的 Badge —— 绿底「启用」/ 灰底「停用」点一下就能互转。
 *
 * 为什么从 Switch 换成 Badge：
 * - 开关本身要占用一整条 26×16 的轨道 + 一段文字，而「状态」在表格里真正的信息只有
 *   两个字：启用 / 停用。Badge 是 Spectrum 里表达「color-categorized metadata」的组件，
 *   正好承担这种短标签；用色（positive 绿 / neutral 灰）就已经把状态说清楚了。
 * - 上一版把开关标签写成「允许登录」，是为了满足 Switch 的 content standard
 *   （标签描述恒定设置、不随状态变）。但那四个字在中文里读起来像一句废话，
 *   信息密度低又不直观。换回 Badge 后这个约束自然消失：Badge 的内容本来就是
 *   **当前状态**，写「启用 / 停用」才是对的。
 * - 可点性靠 `<button>` 提供，不用 div[role=button]：键盘 Tab / Enter / Space 都是原生行为。
 *   套在 Badge 外层的按钮不画自己的框（padding 0 / 透明底），视觉上就是一枚 Badge，
 *   但带 `focus-ring` 可见焦点环。
 * - Badge 是 `display: flex` 且宽度随内容，外面再包一层 `width: fit`，
 *   否则会被单元格的 `width: full` 拉成整格宽。
 *
 * 为什么不再弹二次确认：Spectrum 要求 AlertDialog「sparingly，不要做过量确认」。
 * 停用可逆、历史数据保留，所以用「立即执行 + 可撤销提示」，比「先问再做」少一次点击。
 * 删除用户仍保留 destructive AlertDialog（不可撤销）。
 */
function StatusToggle({
  user,
  isSelf,
  onToggle,
}: {
  user: RowData
  isSelf: boolean
  onToggle: (next: boolean) => void
}) {
  const active = user.is_active
  const name = userNameOf(user)
  return (
    <button
      type="button"
      className={`${statusButton} status-chip focus-ring`}
      // 读屏念「张嘉瑞：启用，按下按钮」；可见文字就是当前状态
      aria-pressed={active}
      aria-label={`${name}：${active ? '启用' : '停用'}`}
      title={isSelf ? '不能停用自己' : active ? '点击停用' : '点击启用'}
      disabled={isSelf}
      onClick={() => onToggle(!active)}
    >
      <Badge variant={active ? 'positive' : 'neutral'} fillStyle="subtle">
        {active ? '启用' : '停用'}
      </Badge>
    </button>
  )
}

/** 删除用户：账号与他的 API 密钥一起清除，历史任务保留但解除归属 */
function DeleteUserTrigger({
  user,
  isSelf,
  onDone,
}: {
  user: RowData
  isSelf: boolean
  onDone: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)

  const remove = async () => {
    setBusy(true)
    try {
      await deleteUser(user.id)
      toast.success('已删除用户')
      await onDone()
    } catch {
      // 拦截器已提示
    } finally {
      setBusy(false)
    }
  }

  return (
    <DialogTrigger>
      <ActionButton
        isQuiet
        size="S"
        // 删除是破坏性操作：图标转 semantic negative（红）。
        // S2 的 ActionButton 没有 danger/negative 变体，而图标是 currentColor，
        // 所以挂一个类名由 global.css 把 color 设成 negative 语义色。
        UNSAFE_className="delete-action"
        aria-label={`删除用户 ${userNameOf(user)}`}
        isDisabled={busy || isSelf}
      >
        <DeleteIcon />
      </ActionButton>
      <AlertDialog
        title={`删除用户 ${userNameOf(user)}？`}
        variant="destructive"
        primaryActionLabel="删除"
        cancelLabel="取消"
        onPrimaryAction={remove}
      >
        删除后该用户的账号与 API 密钥会一并清除，历史任务记录保留但不再归属该用户。 此操作不可撤销。
      </AlertDialog>
    </DialogTrigger>
  )
}

export default function UsersPage() {
  const me = useAuthStore(s => s.user)
  const [rows, setRows] = useState<RowData[]>([])
  const [isLoading, setIsLoading] = useState(true)
  /**
   * 每行的状态切换队列：同一时间只飞一个 PATCH，连点时只记下「最后要变成什么」。
   * 不并发发请求，是因为两个相反的 PATCH 到达服务端的先后顺序不确定，
   * 开关的最终视觉可能与数据库里的值相反 —— 串行化之后再无歧义。
   */
  const switchJobs = useRef<
    Record<number, { busy: boolean; desired: boolean | null; serverValue: boolean }>
  >({})

  const refresh = useCallback(async () => {
    try {
      setRows(await listUsers())
    } catch {
      // 拦截器已提示
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const patchUser = async (
    id: number,
    payload: { is_active?: boolean; is_superuser?: boolean },
    message: string
  ) => {
    try {
      await updateUser(id, payload)
      toast.success(message)
      await refresh()
    } catch {
      // 拦截器已提示
    }
  }

  /**
   * 切换启用状态：开关先跟手（乐观更新），请求按行串行提交，失败回滚。
   *
   * 为什么不用二次确认框：Spectrum 对 AlertDialog 的要求是「sparingly，只用于必须确认才继续的
   * 重要信息，不要做过量确认」。停用是可逆、且历史数据保留的低风险操作，所以改成
   * 「立即执行 + 可撤销提示」（`toast.successAction`），比「先问再做」少一次点击，
   * 也让「点开关」这个动作保持即时感。
   *
   * 撤销本身不再叠一个可撤销提示 —— 换来换去会一路刷屏，规范也要求尽量别同时弹多条；
   * 让用户确认撤销生效的是开关自己翻回去。
   */
  const toggleActive = async (user: RowData, next: boolean, undoable = true) => {
    const name = userNameOf(user)

    // 乐观更新：不等网络，先把开关画成用户点的那个状态
    setRows(list => list.map(row => (row.id === user.id ? { ...row, is_active: next } : row)))

    const job = (switchJobs.current[user.id] ??= {
      busy: false,
      desired: null,
      serverValue: user.is_active,
    })
    job.desired = next
    if (job.busy) return // 已有请求在飞，等它回来顺手把最新意愿发出去
    job.busy = true

    let settled = job.serverValue
    try {
      while (job.desired !== null) {
        const target = job.desired
        job.desired = null
        // 用服务端的回包校准，而不是直接信任本地意图：别的管理员同时改这一行时，
        // 以真正落库的值为准，开关不会停在一个「看起来成功、其实被覆盖」的状态
        const updated = await updateUser(user.id, { is_active: target })
        job.serverValue = updated?.is_active ?? target
        settled = job.serverValue
      }
      if (!undoable) {
        // 撤销已生效：给一句结果确认即可，不再叠「撤销」按钮
        toast.success(settled ? `已启用 ${name}` : `已停用 ${name}`)
        return
      }
      toast.successAction(settled ? `已启用 ${name}` : `已停用 ${name}`, '撤销', () => {
        // 撤销以「服务端实际落库的值」为基准取反，而不是以用户最初点的那一下
        void toggleActive({ ...user, is_active: settled }, !settled, false)
      })
    } catch {
      // 拦截器已提示，把这一行退回服务端已知的状态（burst 里以最后一次成功为准）
      job.desired = null
      setRows(list =>
        list.map(row => (row.id === user.id ? { ...row, is_active: job.serverValue } : row))
      )
    } finally {
      job.busy = false
    }
  }

  return (
    <Page>
      <PageHeader
        title="用户管理"
        description="通过统一身份登录的用户会自动出现在这里。停用后无法登录，历史数据保留。"
      />

      <Section title="用户列表">
        {/* 表格自带 1px gray-300 边框 + 6px 圆角，不再外套 Panel，避免双层边框；
            加载态与空态用官方 loadingState / renderEmptyState 渲染在表格内部。
            空表要加 .table-empty（见 global.css）：S2 虚拟化表格在没有数据时会把表体
            压成 0 高度，内置空态被定位到可视区之外，页面上根本看不到。 */}
        <div className={rows.length === 0 && !isLoading ? 'table-empty' : undefined}>
          <TableView
            aria-label="用户列表"
            selectionMode="none"
            density="spacious"
            overflowMode="truncate"
            loadingState={isLoading ? 'loading' : 'idle'}
            styles={tableStyles}
          >
            <TableHeader>
              <Column isRowHeader>用户</Column>
              <Column width={120}>身份来源</Column>
              {/* 角色列：等宽 Badge 84 + 单元格 32px。箭头已去掉，原先预留的宽度不再需要 */}
              <Column width={116}>角色</Column>
              {/* 状态列：Badge「启用/停用」≈ 46px（含内边距）+ 单元格 32px */}
              <Column width={96}>状态</Column>
              <Column width={156}>最后登录</Column>
              {/* 只剩删除一个 24px 图标按钮 + 32px 单元格内边距 */}
              <Column width={64}> </Column>
            </TableHeader>
            <TableBody
              items={rows}
              renderEmptyState={() => (
                <EmptyState illustration={<UserGroup />} title="还没有用户">
                  通过统一身份登录的用户会自动出现在这里。
                </EmptyState>
              )}
            >
              {(user: RowData) => {
                const isSelf = me?.id === user.id
                return (
                  <Row key={user.id}>
                    <Cell>
                      <div className={userCell}>
                        <span className={userName}>{userNameOf(user)}</span>
                        <span className={userMail}>{user.email}</span>
                      </div>
                    </Cell>
                    <Cell>
                      <Text styles={cellText}>
                        {user.oidc_bound ? 'OIDC' : '本地账号'}
                        {user.has_password && user.oidc_bound ? ' + 密码' : ''}
                      </Text>
                    </Cell>
                    <Cell>
                      {/* 角色列是安静型 Picker：点标签直接改权限。 */}
                      <RolePicker
                        user={user}
                        isSelf={isSelf}
                        onChange={isSuperuser =>
                          void patchUser(
                            user.id,
                            { is_superuser: isSuperuser },
                            isSuperuser ? '已设为管理员' : '已取消管理员'
                          )
                        }
                      />
                    </Cell>
                    <Cell>
                      {/* 状态列就是绿色 Badge：点一下启用/停用互转，行尾不再重复一个锁形按钮。 */}
                      <StatusToggle
                        user={user}
                        isSelf={isSelf}
                        onToggle={next => void toggleActive(user, next)}
                      />
                    </Cell>
                    <Cell>
                      <span className={`${cellText} u-tabular`}>
                        {formatShortTime(user.last_login_at)}
                      </span>
                    </Cell>
                    <Cell>
                      <div className={actions}>
                        <DeleteUserTrigger user={user} isSelf={isSelf} onDone={refresh} />
                      </div>
                    </Cell>
                  </Row>
                )
              }}
            </TableBody>
          </TableView>
        </div>

        <p className={muted}>
          状态列点一下就切换，点错了可以在提示里撤销。不能停用、删除或降级自己，避免把最后一个管理员锁在门外。
          删除用户会连同他的 API 密钥一起清除，历史任务记录保留。忘记密码时用 manage.py
          reset-password 重置。
        </p>
      </Section>
    </Page>
  )
}
