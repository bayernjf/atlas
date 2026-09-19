// B 包（docs/27 §4.3）：调试暂停后续跑时 globals 覆盖草稿的解析与校验。
// 规则与后端 src/atlas/debug/sessions.py 的 apply_overrides 保持一致：
// 仅 global 作用域顶层、合法标识符键、JSON 对象（值需可 JSON 序列化，请求体天然保证）。
// 抽成纯函数以便单测（U137）。

const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/

export type GlobalsOverrideResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string }

export function parseGlobalsDraft(text: string): GlobalsOverrideResult {
  const trimmed = text.trim()
  // 空白视为不覆盖（step/continue 浅合并空对象＝沿用当前 globals）。
  if (!trimmed) return { ok: true, value: {} }

  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return { ok: false, error: 'globals 必须是合法 JSON' }
  }

  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: 'globals 必须是 JSON 对象（仅 global 作用域顶层键）' }
  }

  const record = parsed as Record<string, unknown>
  // 注意空串键名也是 falsy，必须用 !== undefined 判定而非真值判断。
  const badKey = Object.keys(record).find((key) => !IDENTIFIER.test(key))
  if (badKey !== undefined) {
    return { ok: false, error: `非法 global 变量名："${badKey}"（仅允许合法标识符顶层键）` }
  }

  return { ok: true, value: record }
}
