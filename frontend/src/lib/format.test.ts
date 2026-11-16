import { describe, expect, test } from 'vitest'
import {
  branchName,
  directionName,
  formatAge,
  formatCount,
  formatDay,
  formatDelay,
  formatHour,
  formatMinutes,
  formatPercent,
  formatTime,
} from './format'

describe('formatDelay', () => {
  test.each([
    [0, 'on time'],
    [29, 'on time'],
    [-29, 'on time'],
    [30, '30 s'],
    [45, '45 s'],
    [60, '1 min'],
    [90, '1.5 min'],
    [195, '3.3 min'],
    [599, '10 min'],
    [923, '15 min'],
    [-64, '1.1 min early'],
    [-40, '40 s early'],
    [null, '–'],
    [undefined, '–'],
  ])('%s seconds is "%s"', (seconds, text) => {
    expect(formatDelay(seconds)).toBe(text)
  })
})

describe('formatMinutes', () => {
  test.each([
    [0, '0 min'],
    [270, '4.5 min'],
    [300, '5 min'],
    [540, '9 min'],
    [750, '13 min'],
    [null, '–'],
  ])('%s seconds is "%s"', (seconds, text) => {
    expect(formatMinutes(seconds)).toBe(text)
  })
})

describe('formatPercent', () => {
  test.each([
    [0, '0%'],
    [0.004, '0.4%'],
    [0.125, '13%'],
    [0.031, '3.1%'],
    [0.05, '5%'],
    [1, '100%'],
    [null, '–'],
  ])('%s is "%s"', (share, text) => {
    expect(formatPercent(share)).toBe(text)
  })
})

test('formatCount groups thousands', () => {
  expect(formatCount(12480)).toBe('12,480')
  expect(formatCount(0)).toBe('0')
  expect(formatCount(null)).toBe('–')
})

test('formatHour pads to two digits', () => {
  expect(formatHour(8)).toBe('08:00')
  expect(formatHour(17)).toBe('17:00')
})

describe('formatTime', () => {
  test('shows Paris time in winter and in summer', () => {
    expect(formatTime('2026-11-16T07:42:00Z')).toBe('08:42')
    expect(formatTime('2026-07-01T07:42:00Z')).toBe('09:42')
  })

  test('shows midnight as 00, not 24', () => {
    expect(formatTime('2026-11-16T23:05:00Z')).toBe('00:05')
  })

  test('copes with a missing time', () => {
    expect(formatTime(null)).toBe('–')
  })
})

test('formatDay names the service day', () => {
  expect(formatDay('2026-11-16')).toBe('Mon 16 Nov')
  expect(formatDay('2026-01-01')).toBe('Thu 1 Jan')
})

describe('formatAge', () => {
  const now = new Date('2026-11-16T12:00:00Z')

  test.each([
    ['2026-11-16T11:59:20Z', 'just now'],
    ['2026-11-16T11:56:00Z', '4 min ago'],
    ['2026-11-16T09:00:00Z', '3 h ago'],
    ['2026-11-13T12:00:00Z', '3 days ago'],
    // A clock a little ahead of ours must not give a negative age.
    ['2026-11-16T12:00:30Z', 'just now'],
  ])('%s is "%s"', (iso, text) => {
    expect(formatAge(iso, now)).toBe(text)
  })

  test('says never when nothing was collected', () => {
    expect(formatAge(null, now)).toBe('never')
  })
})

test('branch and direction names', () => {
  expect(branchName('saint_remy')).toBe('Saint-Rémy')
  expect(branchName('trunk')).toBe('Central section')
  expect(branchName(null)).toBe('Unknown')
  expect(directionName('northbound')).toBe('Northbound')
  expect(directionName(null)).toBe('Unknown')
})
