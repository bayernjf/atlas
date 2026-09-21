import { describe, expect, it } from 'vitest'
import {
  PASSWORD_MAX_LEN,
  PASSWORD_MIN_LEN,
  generatePassword,
  passwordPolicyError,
  validateChangePassword,
  validateNewUser,
} from '../users'

describe('passwordPolicyError', () => {
  it('accepts strong passwords', () => {
    expect(passwordPolicyError('Strongpass-1')).toBeUndefined()
    expect(passwordPolicyError('x'.repeat(PASSWORD_MAX_LEN))).toBeUndefined()
  })

  it('rejects by length', () => {
    expect(passwordPolicyError('x'.repeat(PASSWORD_MIN_LEN - 1))).toBe(
      'users.error.passwordLength',
    )
    expect(passwordPolicyError('x'.repeat(PASSWORD_MAX_LEN + 1))).toBe(
      'users.error.passwordLength',
    )
  })

  it('rejects weak passwords case-insensitively', () => {
    expect(passwordPolicyError('admin123')).toBe('users.error.passwordWeak')
    expect(passwordPolicyError('ADMIN123')).toBe('users.error.passwordWeak')
  })
})

describe('validateNewUser', () => {
  it('returns no errors for valid input', () => {
    const errors = validateNewUser({
      username: 'new-user',
      password: 'Strongpass-1',
      displayName: '新用户',
    })
    expect(errors).toEqual({})
  })

  it('flags each field independently', () => {
    const errors = validateNewUser({
      username: 'Bad User',
      password: 'short',
      displayName: ' ',
    })
    expect(Object.keys(errors).sort()).toEqual(['displayName', 'password', 'username'])
  })

  it('rejects too short and too long usernames', () => {
    expect(
      validateNewUser({ username: 'ab', password: 'Strongpass-1', displayName: 'x' })
        .username,
    ).toBeDefined()
    expect(
      validateNewUser({
        username: 'a'.repeat(65),
        password: 'Strongpass-1',
        displayName: 'x',
      }).username,
    ).toBeDefined()
  })
})

describe('validateChangePassword', () => {
  const valid = {
    oldPassword: 'Oldpass-1',
    newPassword: 'Newpass-2',
    confirmPassword: 'Newpass-2',
  }

  it('accepts valid change', () => {
    expect(validateChangePassword(valid)).toEqual({})
  })

  it('requires old password', () => {
    expect(validateChangePassword({ ...valid, oldPassword: '' }).oldPassword).toBe(
      'users.error.oldPasswordRequired',
    )
  })

  it('rejects weak new password and same-as-old', () => {
    expect(
      validateChangePassword({ ...valid, newPassword: 'weak', confirmPassword: 'weak' })
        .newPassword,
    ).toBe('users.error.passwordLength')
    expect(
      validateChangePassword({
        oldPassword: 'Oldpass-1',
        newPassword: 'Oldpass-1',
        confirmPassword: 'Oldpass-1',
      }).newPassword,
    ).toBe('users.error.passwordSameAsOld')
  })

  it('rejects mismatched confirmation', () => {
    expect(
      validateChangePassword({ ...valid, confirmPassword: 'Otherpass-3' }).confirmPassword,
    ).toBe('users.error.passwordMismatch')
  })
})

describe('generatePassword', () => {
  it('produces 16 chars from injected alphabet rng', () => {
    const sequence = [0, 0.99, 0.5]
    let index = 0
    const password = generatePassword({
      next: () => sequence[index++ % sequence.length]!,
    })
    expect(password).toHaveLength(16)
  })
})
