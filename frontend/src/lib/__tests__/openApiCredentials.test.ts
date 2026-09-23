import { describe, expect, it } from 'vitest'
import {
  compactCredentialDrafts,
  credentialUpsertPayload,
  emptyBasic,
  isBasicCredential,
} from '../openApiCredentials'

describe('compactCredentialDrafts', () => {
  it('keeps and trims non-empty string secrets', () => {
    expect(compactCredentialDrafts({ BearerAuth: '  tok-1  ' })).toEqual({
      BearerAuth: 'tok-1',
    })
  })

  it('drops blank strings', () => {
    expect(compactCredentialDrafts({ BearerAuth: '   ' })).toEqual({})
  })

  it('keeps basic credentials only when both fields are non-empty', () => {
    expect(
      compactCredentialDrafts({
        full: { username: ' alice ', password: ' secret ' },
        userOnly: { username: 'bob', password: '' },
        passOnly: { username: ' ', password: 'x' },
      }),
    ).toEqual({ full: { username: 'alice', password: 'secret' } })
  })

  it('distinguishes basic drafts from strings', () => {
    expect(isBasicCredential(emptyBasic())).toBe(true)
    expect(isBasicCredential('abc')).toBe(false)
    expect(isBasicCredential(undefined)).toBe(false)
  })

  it('maps cleared values to null for credential deletion', () => {
    expect(
      credentialUpsertPayload({
        KeyHeader: '',
        BasicAuth: { username: 'alice', password: '' },
        BearerAuth: 'tok',
      }),
    ).toEqual({
      KeyHeader: '',
      BasicAuth: null,
      BearerAuth: 'tok',
    })
  })
})
