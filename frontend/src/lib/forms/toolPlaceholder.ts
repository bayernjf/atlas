/**
 * ToolCallConfig params 占位 JSON（纯逻辑：从工具名映射示例参数）。
 */

export function toolParamsPlaceholder(tool: string | undefined): string {
  if (tool?.startsWith('http/')) {
    return '{"method":"GET","url":"/orders","headers":{"X-Demo-Token":"demo-token"}}'
  }
  if (tool === 'database/query') {
    return '{"sql":"SELECT order_id, amount FROM orders WHERE amount > :min","params":{"min":1000},"limit":500}'
  }
  if (tool === 'database/execute') {
    return '{"sql":"UPDATE orders SET status = :status WHERE order_id = :id","params":{"status":"refunded","id":"12345"}}'
  }
  if (tool === 'message/send') {
    return '{"channel":"dingtalk","to":"https://oapi.dingtalk.com/robot/send?access_token=xxx","subject":"订单 {{trigger-1.context.payload.order_id}} 待审批","body":"请处理","secret":"SEC可选"}'
  }
  return '{"element_desc": "提交按钮"}'
}
