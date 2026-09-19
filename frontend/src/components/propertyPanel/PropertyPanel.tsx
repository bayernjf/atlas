import {
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'
import { useEditorStore, validateNodeId } from '../../store/editorStore'
import {
  NODE_CATALOG,
  ON_ERROR_STRATEGIES,
  type NodeConfig,
} from '../../lib/nodeCatalog'
import { useScopeIndex, useToolOutputSchemas } from '../../lib/useScope'
import { useNodeDiagnostics } from '../../store/validationStore'
import { validateExpression } from '../../lib/conditions'
import { ConditionConfig } from './ConditionConfig'
import { LoopConfig } from './LoopConfig'
import { ParallelConfig } from './ParallelConfig'
import { WaitConfig } from './WaitConfig'
import { SubgraphConfig } from './SubgraphConfig'
import { HumanApprovalConfig } from './HumanApprovalConfig'
import { ToolCallConfig } from './ToolCallConfig'
import { NodeConfigForm } from '../../lib/forms/NodeConfigForm'

export function PropertyPanel() {
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const variables = useEditorStore((state) => state.variables)
  const selectedNodeId = useEditorStore((state) => state.selectedNodeId)
  const updateSelectedNode = useEditorStore((state) => state.updateSelectedNode)
  const updateSelectedConfig = useEditorStore((state) => state.updateSelectedConfig)
  const deleteSelectedNode = useEditorStore((state) => state.deleteSelectedNode)
  const breakpoints = useEditorStore((state) => state.breakpoints)
  const toggleBreakpoint = useEditorStore((state) => state.toggleBreakpoint)
  const setBreakpointExpression = useEditorStore(
    (state) => state.setBreakpointExpression,
  )
  const setBreakpointHitCount = useEditorStore((state) => state.setBreakpointHitCount)
  const setBreakpointLogMessage = useEditorStore((state) => state.setBreakpointLogMessage)

  const scope = useScopeIndex({ nodes, edges, variables })
  const toolOutputSchemas = useToolOutputSchemas()
  // M4 批 2 ⑦：诊断来自分层校验引擎结果 store（L1 同步、L2 防抖）。
  const engineDiagnostics = useNodeDiagnostics(selectedNodeId ?? '')
  const renameNode = useEditorStore((state) => state.renameNode)

  // D30/B3：节点 ID 可编辑重命名（草稿态；切换选中节点时同步并清错）。
  const [idDraft, setIdDraft] = useState('')
  const [idError, setIdError] = useState<string | null>(null)
  useEffect(() => {
    setIdDraft(nodes.find((node) => node.id === selectedNodeId)?.id ?? '')
    setIdError(null)
    // 仅随选中节点切换重置；编辑草稿不依赖 nodes，避免输入中被冲掉。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId])

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
  const diagnostics = engineDiagnostics
  const config = data.config
  const variablePaths = scope.listPathsAt(selectedNode.id, toolOutputSchemas)
  const targetOptions = nodes
    .filter((node) => node.id !== selectedNode.id)
    .map((node) => ({ value: node.id, label: `${node.data.label}（${node.id}）` }))

  const commitRename = () => {
    if (!selectedNodeId) return
    const trimmed = idDraft.trim()
    if (trimmed === selectedNodeId) {
      setIdError(null)
      setIdDraft(selectedNodeId)
      return
    }
    const error = validateNodeId(idDraft, selectedNodeId, nodes.map((node) => node.id))
    if (error) {
      setIdError(error)
      return
    }
    renameNode(selectedNodeId, idDraft)
    setIdError(null)
  }

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
          <Input
            size="small"
            value={idDraft}
            status={idError ? 'error' : undefined}
            aria-label="节点 ID"
            onChange={(event) => {
              setIdDraft(event.target.value)
              setIdError(null)
            }}
            onPressEnter={commitRename}
            onBlur={commitRename}
          />
          {idError ? (
            <div>
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                {idError}
              </Typography.Text>
            </div>
          ) : null}
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

        {data.kind === 'trigger' && (
          <NodeConfigForm
            kind="trigger"
            config={config}
            update={updateSelectedConfig}
            variablePaths={[]}
            targetOptions={[]}
            diagnostics={diagnostics}
          />
        )}
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
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'loop' && (
          <LoopConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'parallel' && (
          <ParallelConfig
            config={config}
            update={updateSelectedConfig}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'wait' && <WaitConfig config={config} update={updateSelectedConfig} />}
        {data.kind === 'subgraph' && (
          <SubgraphConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'human_approval' && (
          <HumanApprovalConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'tool_call' && (
          <ToolCallConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            onInsert={insertVariable}
            nodeId={selectedNode.id}
          />
        )}

        <Typography.Text strong>调试（04 §5.12 会话级断点）</Typography.Text>
        <Field label="执行到此节点前暂停">
          <Switch
            checked={selectedNode.id in breakpoints}
            onChange={() => toggleBreakpoint(selectedNode.id)}
          />
        </Field>
        {selectedNode.id in breakpoints && (
          <Field label="条件表达式（可空；为真才暂停）">
            <Input
              value={breakpoints[selectedNode.id]?.expression ?? ''}
              placeholder="{{trigger-1.context.payload.amount}} > 1000"
              status={
                breakpoints[selectedNode.id]?.expression?.trim() &&
                validateExpression(breakpoints[selectedNode.id]!.expression!.trim()).length > 0
                  ? 'error'
                  : undefined
              }
              onChange={(event) =>
                setBreakpointExpression(selectedNode.id, event.target.value)
              }
            />
            {breakpoints[selectedNode.id]?.expression?.trim() &&
              validateExpression(breakpoints[selectedNode.id]!.expression!.trim()).map(
                (message) => (
                  <Typography.Text key={message} type="danger">
                    {message}
                  </Typography.Text>
                ),
              )}
            <Typography.Text type="secondary">
              断点仅本会话有效，不随 Graph 保存；异常表达式 fail-safe 视为不命中。
            </Typography.Text>
          </Field>
        )}
        {selectedNode.id in breakpoints && (
          <Field label="命中计数 hitCount（每 N 次命中暂停一次；留空＝每次命中都暂停）">
            <InputNumber
              min={1}
              precision={0}
              style={{ width: '100%' }}
              placeholder="如 3（循环/重入节点常用）"
              value={breakpoints[selectedNode.id]?.hitCount ?? null}
              onChange={(value) =>
                setBreakpointHitCount(selectedNode.id, (value as number | null) ?? null)
              }
            />
          </Field>
        )}
        {selectedNode.id in breakpoints && (
          <Field label="日志断点 logMessage（非空＝命中只记日志、不暂停）">
            <Input
              value={breakpoints[selectedNode.id]?.logMessage ?? ''}
              placeholder="如：已到达退款节点（消息原样输出，不做插值）"
              onChange={(event) =>
                setBreakpointLogMessage(selectedNode.id, event.target.value)
              }
            />
            <Typography.Text type="secondary">
              填写消息后该断点成为日志断点：命中仅在调试控制台输出，不暂停运行；v1 不与
              hitCount 组合、消息原样不插值。
            </Typography.Text>
          </Field>
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

        {diagnostics.length > 0 ? (
          <div className="property-errors">
            {diagnostics.map((diagnostic, index) => (
              <div key={`${diagnostic.code}-${index}`}>• {diagnostic.message}</div>
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
