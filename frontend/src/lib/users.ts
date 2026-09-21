/**
 * 用户管理表单纯逻辑（docs/31 §0-6/§6）：前端镜像后端口令/用户名策略，
 * 返回 i18n 文案 key（由组件 t() 翻译），不抛异常。
 */

export const PASSWORD_MIN_LEN = 8
export const PASSWORD_MAX_LEN = 128
export const USERNAME_RE = /^[a-z0-9_.-]+$/

const WEAK_PASSWORDS = new Set([
  'admin123',
  'password',
  'password123',
  '12345678',
  '123456789',
  'qwerty123',
  '11111111',
  '00000000',
  'abc12345',
  'iloveyou',
  'admin@123',
  'welcome1',
])

export type FieldErrors<T extends string> = Partial<Record<T, string>>

export type NewUserFields = {
  username: string
  password: string
  displayName: string
}

export function validateNewUser(fields: Partial<NewUserFields>): FieldErrors<keyof NewUserFields> {
  const errors: FieldErrors<keyof NewUserFields> = {}
  const username = (fields.username ?? '').trim()
  if (!(username.length >= 3 && username.length <= 64 && USERNAME_RE.test(username))) {
    errors.username = 'users.error.usernameRules'
  }
  const passwordError = passwordPolicyError(fields.password ?? '')
  if (passwordError) errors.password = passwordError
  if (!(fields.displayName ?? '').trim()) {
    errors.displayName = 'users.error.displayNameRequired'
  }
  return errors
}

/** 返回错误文案 key；合规则返回 undefined。 */
export function passwordPolicyError(password: string): string | undefined {
  if (!(password.length >= PASSWORD_MIN_LEN && password.length <= PASSWORD_MAX_LEN)) {
    return 'users.error.passwordLength'
  }
  if (WEAK_PASSWORDS.has(password.toLowerCase())) {
    return 'users.error.passwordWeak'
  }
  return undefined
}

export type ChangePasswordFields = {
  oldPassword: string
  newPassword: string
  confirmPassword: string
}

export function validateChangePassword(
  fields: ChangePasswordFields,
): FieldErrors<keyof ChangePasswordFields> {
  const errors: FieldErrors<keyof ChangePasswordFields> = {}
  if (!fields.oldPassword) errors.oldPassword = 'users.error.oldPasswordRequired'
  const policyError = passwordPolicyError(fields.newPassword)
  if (policyError) {
    errors.newPassword = policyError
  } else if (fields.newPassword === fields.oldPassword) {
    errors.newPassword = 'users.error.passwordSameAsOld'
  }
  if (fields.confirmPassword !== fields.newPassword) {
    errors.confirmPassword = 'users.error.passwordMismatch'
  }
  return errors
}

const GENERATED_PASSWORD_ALPHABET = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'

/** 生成 16 位随机口令（默认 crypto；可注入 rng 便于测试）。 */
export function generatePassword(rng: { next: () => number } = defaultRng()): string {
  let out = ''
  for (let i = 0; i < 16; i++) {
    out += GENERATED_PASSWORD_ALPHABET[Math.floor(rng.next() * GENERATED_PASSWORD_ALPHABET.length)]
  }
  return out
}

function defaultRng(): { next: () => number } {
  const cryptoObj = globalThis.crypto
  if (!cryptoObj?.getRandomValues) return { next: () => Math.random() }
  const buffer = new Uint32Array(1)
  return {
    next: () => {
      cryptoObj.getRandomValues(buffer)
      return buffer[0]! / 0x100000000
    },
  }
}
