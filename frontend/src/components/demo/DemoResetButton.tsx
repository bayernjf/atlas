import { useState } from 'react'
import { Button, Popconfirm, message } from 'antd'
import { resetDemo } from '../../lib/apiClient'
import { useTranslation } from '../../locales'

/**
 * docs/101 D59：演示面自助重置按钮。
 *
 * 渲染条件由父级传入（demo_surface × admin 角色），本组件只负责
 * 二次确认 → admin-only reset 端点 → 成功提示 + 整页刷新。
 * 独立成组件以便组件测试覆盖「按钮条件 / 点击调用 / 成功提示」。
 */
export function DemoResetButton({
  visible,
}: {
  /** demo_surface 开启且当前角色 admin 时才为 true（父级在 Editor 内判断）。 */
  visible: boolean
}) {
  const { t } = useTranslation('editor')
  const [resetting, setResetting] = useState(false)

  const handleReset = async () => {
    setResetting(true)
    try {
      await resetDemo()
      message.success(t('header.resetDemoSuccess'))
      window.location.reload()
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      setResetting(false)
    }
  }

  if (!visible) return null

  return (
    <Popconfirm
      title={t('header.resetDemoConfirm')}
      onConfirm={handleReset}
      okButtonProps={{ danger: true }}
      okText={t('header.resetDemo')}
    >
      <Button danger loading={resetting}>
        {t('header.resetDemo')}
      </Button>
    </Popconfirm>
  )
}
