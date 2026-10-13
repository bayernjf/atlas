/**
 * 流式运行分代守门（项目级审查发现 #6，docs/125 §3.4，U1382）。
 *
 * 发起新一运行时 next() 递增分代并 abort 上一运行的 SSE 连接；所有迟到帧/终帧/
 * reject 的回调先经 isCurrent() 判定，旧代一律丢弃，不得写入新运行的状态。
 * abort 只管前端连接生命周期，不通知后端取消（断连不取消，docs/24 §3）。
 */
export class RunGeneration {
  private current = 0
  private controller: AbortController | null = null

  /** 开始新一运行：旧代失效并断旧连接，返回当代号与本次 fetch 用的 signal。 */
  next(): { generation: number; signal: AbortSignal } {
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    this.current += 1
    return { generation: this.current, signal: controller.signal }
  }

  isCurrent(generation: number): boolean {
    return generation === this.current
  }

  /** 外部关闭（如关闭 Modal）：当前运行同样作废并断连，其迟到回调按旧代丢弃。 */
  abort(): void {
    this.current += 1
    this.controller?.abort()
    this.controller = null
  }
}
