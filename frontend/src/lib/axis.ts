/** Round a maximum up to a clean axis top with three steps: 0, step, 2·step, 3·step. */
export function axisTop(max: number): { top: number; step: number } {
  if (max <= 0) return { top: 3, step: 1 }
  const rough = max / 3
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? rough
  return { top: step * 3, step }
}
