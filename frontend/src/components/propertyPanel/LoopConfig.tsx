import { Input, InputNumber, Select, Space, Typography } from 'antd'
import { MAX_LOOP_ITERATIONS, type NodeConfig } from '../../lib/nodeCatalog'
import { validateExpression } from '../../lib/conditions'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  targetOptions: { value: string; label: string }[]
}

export function LoopConfig({ config, update, variablePaths, targetOptions }: Props) {
  const expression = config.continueExpression ?? ''
  const expressionErrors = expression.trim() ? validateExpression(expression) : []

  return (
    <>
      <Typography.Text strong>条件循环（表达式为真时进入循环体，04 §5.3）</Typography.Text>
      <label className="property-field">
        <Typography.Text type="secondary">继续条件（每轮重入时求值）</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>
          循环体内可用 {'{{loop-x.index}}'} 引用当前轮次（从 1 开始）
        </Typography.Text>
        <Input.TextArea
          rows={2}
          placeholder="{{loop-1.index}} < 3"
          value={expression}
          status={!expression.trim() || expressionErrors.length > 0 ? 'error' : undefined}
          onChange={(event) => update({ continueExpression: event.target.value })}
        />
        {expressionErrors.length > 0 && (
          <Typography.Text type="danger" style={{ fontSize: 12 }}>
            {expressionErrors.join('；')}
          </Typography.Text>
        )}
        <Select
          style={{ width: '100%' }}
          placeholder="选择后追加 {{变量}} 到表达式"
          value={undefined}
          onChange={(path: string) => update({ continueExpression: `${expression}{{${path}}} ` })}
          options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
        />
      </label>
      <label className="property-field">
        <Typography.Text type="secondary">最大次数（达到后强制退出，1-{MAX_LOOP_ITERATIONS}）</Typography.Text>
        <InputNumber
          min={1}
          max={MAX_LOOP_ITERATIONS}
          precision={0}
          style={{ width: '100%' }}
          value={config.maxIterations}
          onChange={(value) => update({ maxIterations: value ?? undefined })}
        />
      </label>
      <Space orientation="vertical" size={8} style={{ width: '100%' }}>
        <label className="property-field" style={{ margin: 0 }}>
          <Typography.Text type="secondary">循环体入口（条件为真时进入；体内末端需连线回本节点）</Typography.Text>
          <Select
            style={{ width: '100%' }}
            placeholder="选择循环体入口节点"
            value={config.bodyTarget || undefined}
            status={!config.bodyTarget ? 'error' : undefined}
            onChange={(bodyTarget: string) => update({ bodyTarget })}
            options={targetOptions}
          />
        </label>
        <label className="property-field" style={{ margin: 0 }}>
          <Typography.Text type="secondary">退出目标（条件为假 / 达上限 / 表达式异常时）</Typography.Text>
          <Select
            style={{ width: '100%' }}
            placeholder="选择退出目标节点"
            value={config.exitTarget || undefined}
            status={!config.exitTarget ? 'error' : undefined}
            onChange={(exitTarget: string) => update({ exitTarget })}
            options={targetOptions}
          />
        </label>
      </Space>
    </>
  )
}
