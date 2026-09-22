import type { BasicCredential, CredentialInput, CredentialValue } from './apiClient'

export function isBasicCredential(
  value: CredentialValue | undefined,
): value is BasicCredential {
  return typeof value === 'object' && value !== null
}

export function emptyBasic(): BasicCredential {
  return { username: '', password: '' }
}

export function compactCredentialDrafts(
  drafts: Record<string, CredentialValue>,
): Record<string, CredentialValue> {
  const result: Record<string, CredentialValue> = {}
  for (const [name, value] of Object.entries(drafts)) {
    if (typeof value === 'string') {
      const trimmed = value.trim()
      if (trimmed) result[name] = trimmed
      continue
    }
    const username = value.username.trim()
    const password = value.password.trim()
    if (username && password) result[name] = { username, password }
  }
  return result
}

export function credentialUpsertPayload(
  drafts: Record<string, CredentialValue>,
): Record<string, CredentialInput> {
  const result: Record<string, CredentialInput> = {}
  for (const [name, value] of Object.entries(drafts)) {
    if (typeof value === 'string') {
      result[name] = value.trim()
      continue
    }
    const username = value.username.trim()
    const password = value.password.trim()
    result[name] = username && password ? { username, password } : null
  }
  return result
}
