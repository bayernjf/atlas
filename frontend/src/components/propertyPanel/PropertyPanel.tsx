import {
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useEditorStore } from '../../store/editorStore'
import {
  NODE_CATALOG,
  ON_ERROR_STRATEGIES,
  validateNode,
  type NodeConfig,
} from '../../lib/nodeCatalog'
import { listVariablePaths } from '../../lib/variables'
import { ConditionConfig } from './ConditionConfig'
import { LoopConfig } from './LoopConfig'

export function PropertyPanel() {
  const nodes = useEditorStore((state) => state.nodes)
  const variables = useEditorStore((state) => state.variables)
  const selectedNodeId = useEditorStore((state) => state.selectedNodeId)
  const updateSelectedNode = useEditorStore((state) => state.updateSelectedNode)
  const updateSelectedConfig = useEditorStore((state) => state.updateSelectedConfig)
  const deleteSelectedNode = useEditorStore((state) => state.deleteSelectedNode)

  const selectedNode = nodes.find((node) => node.id === selectedNodeId)

  if (!selectedNode) {
    return (
      <Card className="side-card" title="属性面板" size="small">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择或拖入节点后配置属性" />
      </Card>
    )
  }

  const { data } = selectedNode
  const meta = NODE_CATALOG[data.kind]
  const errors = validateNode(data)
  const config = data.config
  const variablePaths = listVariablePaths(variables, nodes)
  const targetOptions = nodes
    .filter((node) => node.id !== selectedNode.id)
    .map((node) => ({ value: node.id, label: `${node.data.label}（${node.id}）` }))

  const insertVariable = (path: string) => {
    if (!path) return
    if (data.kind === 'ai_decision') {
      updateSelectedConfig({ promptTemplate: `${config.promptTemplate ?? ''}{{${path}}} ` })
    } else if (data.kind === 'tool_call') {
      updateSelectedConfig({ params: `${config.params ?? ''}{{${path}}} ` })
    }
  }

  return (
    <Card
      className="side-card"
      title="属性面板"
      size="small"
      extra={
        <Button size="small" danger onClick={deleteSelectedNode}>
          删除
        </Button>
      }
    >
      <Space orientation="vertical" style={{ width: '100%' }} size="small">
        <div>
          <Typography.Text type="secondary">节点 ID</Typography.Text>
          <div>{selectedNode.id}</div>
        </div>
        <div>
          <Typography.Text type="secondary">类型</Typography.Text>
          <div>
            <Tag color={meta.color}>{meta.label}</Tag>
          </div>
        </div>
        <Field label="节点名称">
          <Input
            value={data.label}
            status={!data.label.trim() ? 'error' : undefined}
            onChange={(event) => updateSelectedNode({ label: event.target.value })}
          />
        </Field>
        <Field label="描述">
          <Input.TextArea
            rows={2}
            value={data.description}
            onChange={(event) => updateSelectedNode({ description: event.target.value })}
          />
        </Field>

        {data.kind === 'trigger' && <TriggerConfig config={config} update={updateSelectedConfig} />}
        {data.kind === 'ai_decision' && (
          <DecisionConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            onInsert={insertVariable}
          />
        )}
        {data.kind === 'condition' && (
          <ConditionConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
          />
        )}
        {data.kind === 'loop' && (
          <LoopConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
          />
        )}
        {data.kind === 'tool_call' && (
          <ToolCallConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            onInsert={insertVariable}
          />
        )}

        <Typography.Text strong>重试与失败处理（04 §3.2 retry）</Typography.Text>
        <Field label="最大重试次数">
          <InputNumber
            min={0}
            value={data.retry.maxRetries}
            onChange={(value) =>
              updateSelectedNode({ retry: { ...data.retry, maxRetries: value ?? 0 } })
            }
          />
        </Field>
        <Field label="超时（秒）">
          <InputNumber
            min={1}
            value={data.retry.timeout}
            onChange={(value) =>
              updateSelectedNode({ retry: { ...data.retry, timeout: value ?? 30 } })
            }
          />
        </Field>
        <Field label="失败处理">
          <Select
            value={data.retry.onError}
            style={{ width: '100%' }}
            onChange={(value) => updateSelectedNode({ retry: { ...data.retry, onError: value } })}
            options={ON_ERROR_STRATEGIES.map((value) => ({ value, label: value }))}
          />
        </Field>

        {errors.length > 0 ? (
          <div className="property-errors">
            {errors.map((error) => (
              <div key={error}>• {error}</div>
            ))}
          </div>
        ) : (
          <Typography.Text type="success">配置校验通过</Typography.Text>
        )}
      </Space>
    </Card>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="property-field">
      <Typography.Text type="secondary">{label}</Typography.Text>
      {children}
    </label>
  )
}

type ConfigProps = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
}

function TriggerConfig({ config, update }: ConfigProps) {
  return (
    <>
      <Field label="触发方式">
        <Select
          value={config.triggerType}
          style={{ width: '100%' }}
          onChange={(triggerType) => update({ triggerType })}
          options={[
            { value: 'manual', label: '手动触发' },
            { value: 'schedule', label: '定时（Cron）' },
            { value: 'webhook', label: 'Webhook' },
          ]}
        />
      </Field>
      {config.triggerType === 'schedule' && (
        <Field label="Cron 表达式">
          <Input
            placeholder="0 9 * * *"
            value={config.cron}
            status={!config.cron?.trim() ? 'error' : undefined}
            onChange={(event) => update({ cron: event.target.value })}
          />
        </Field>
      )}
      {config.triggerType === 'webhook' && (
        <Field label="Webhook 路径">
          <Input
            placeholder="/hooks/approval"
            value={config.webhookUrl}
            status={!config.webhookUrl?.trim() ? 'error' : undefined}
            onChange={(event) => update({ webhookUrl: event.target.value })}
          />
        </Field>
      )}
    </>
  )
}

type VariableInsertProps = ConfigProps & {
  variablePaths: string[]
  onInsert: (path: string) => void
}

function DecisionConfig({ config, update, variablePaths, onInsert }: VariableInsertProps) {
  return (
    <>
      <Field label="提示词模板（支持 {{路径}} 引用）">
        <Input.TextArea
          rows={4}
          value={config.promptTemplate}
          status={!config.promptTemplate?.trim() ? 'error' : undefined}
          onChange={(event) => update({ promptTemplate: event.target.value })}
        />
      </Field>
      <Field label="插入变量引用">
        <Select
          style={{ width: '100%' }}
          value={undefined}
          placeholder="选择后追加到模板"
          onChange={onInsert}
          options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
        />
      </Field>
      <Field label="模型（空 = 系统默认）">
        <Input value={config.model} onChange={(event) => update({ model: event.target.value })} />
      </Field>
      <Field label="置信度阈值（06 §6.2 默认 0.6）">
        <InputNumber
          min={0}
          max={1}
          step={0.05}
          value={config.confidenceThreshold}
          onChange={(value) => update({ confidenceThreshold: value ?? 0.6 })}
        />
      </Field>
    </>
  )
}

function ToolCallConfig({ config, update, variablePaths, onInsert }: VariableInsertProps) {
  return (
    <>
      <Field label="工具（适配器能力，如 web-playwright/click）">
        <Input
          value={config.tool}
          status={!config.tool?.trim() ? 'error' : undefined}
          onChange={(event) => update({ tool: event.target.value })}
        />
      </Field>
      <Field label="参数映射（支持 {{路径}} 引用）">
        <Input.TextArea
          rows={3}
          placeholder='{"element_desc": "提交按钮"}'
          value={config.params}
          onChange={(event) => update({ params: event.target.value })}
        />
      </Field>
      <Field label="插入变量引用">
        <Select
          style={{ width: '100%' }}
          value={undefined}
          placeholder="选择后追加到参数"
          onChange={onInsert}
          options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
        />
      </Field>
    </>
  )
}
