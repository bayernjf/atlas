import { useState } from 'react'
import { Button, Card, Input, Select, Space, Table, Typography } from 'antd'
import { useEditorStore } from '../../store/editorStore'
import {
  isValidVariableName,
  VARIABLE_TYPES,
  type GraphVariable,
  type VariableType,
} from '../../lib/variables'

export function VariablesPanel() {
  const variables = useEditorStore((state) => state.variables)
  const addVariable = useEditorStore((state) => state.addVariable)
  const removeVariable = useEditorStore((state) => state.removeVariable)

  const [name, setName] = useState('')
  const [type, setType] = useState<VariableType>('string')
  const [value, setValue] = useState('')
  const [error, setError] = useState('')

  const submit = () => {
    const trimmed = name.trim()
    if (!isValidVariableName(trimmed)) {
      setError('变量名需以字母/下划线开头，仅含字母数字下划线')
      return
    }
    if (variables.some((variable) => variable.name === trimmed)) {
      setError('变量名已存在')
      return
    }
    const variable: GraphVariable = { name: trimmed, type, value, scope: 'global' }
    addVariable(variable)
    setName('')
    setValue('')
    setError('')
  }

  const columns = [
    { title: '引用', dataIndex: 'ref', render: (_: string, row: GraphVariable) => `{{global.${row.name}}}` },
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'type' },
    { title: '值', dataIndex: 'value', ellipsis: true },
    {
      title: '操作',
      render: (_: unknown, row: GraphVariable) => (
        <Button size="small" danger onClick={() => removeVariable(row.name)}>
          删除
        </Button>
      ),
    },
  ]

  return (
    <Card className="side-card" title="全局变量（04 §6）" size="small">
      <Space orientation="vertical" style={{ width: '100%' }} size="small">
        <Typography.Text type="secondary">
          统一使用 {'{{变量路径}}'} 引用，如 {'{{global.company_name}}'}
        </Typography.Text>
        <Input
          placeholder="变量名（英文标识符）"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Space style={{ width: '100%' }}>
          <Select
            value={type}
            onChange={setType}
            style={{ width: 110 }}
            options={VARIABLE_TYPES.map((value) => ({ value, label: value }))}
          />
          <Input
            placeholder="初始值"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </Space>
        {error && <Typography.Text type="danger">{error}</Typography.Text>}
        <Button type="primary" size="small" onClick={submit} block>
          新增全局变量
        </Button>
        <Table
          size="small"
          rowKey="name"
          columns={columns}
          dataSource={variables}
          pagination={false}
        />
      </Space>
    </Card>
  )
}
