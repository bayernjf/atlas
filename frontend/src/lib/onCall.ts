/**
 * docs/33 §5.4 值班轮值表纯函数（不引 React/i18n，组件负责 t() 与请求）。
 */

/** 值班成员上限（与后端 PUT /api/monitoring/on-call 校验一致，1-20）。 */
export const ONCALL_MEMBERS_LIMIT = 20

/**
 * 把自由文本解析为成员列表：英文/中文逗号、分号、换行、空白均可分隔；
 * 去首尾空白、丢弃空段、去重并保持首次出现顺序。
 */
export function parseMembers(text: string): string[] {
  const seen = new Set<string>()
  const members: string[] = []
  for (const raw of text.split(/[,，;；\n\r\t\s]+/)) {
    const name = raw.trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    members.push(name)
  }
  return members
}

/** 校验解析后的成员列表：1-20 个；返回错误 i18n key（合法返回 null）。 */
export function validateMembers(members: string[]): string | null {
  if (members.length === 0) return 'onCall.error.empty'
  if (members.length > ONCALL_MEMBERS_LIMIT) return 'onCall.error.tooMany'
  return null
}
