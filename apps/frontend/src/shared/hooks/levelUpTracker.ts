/**
 * Phase 3 v2 Task 3.9: ephemeral level-up detection для CharacterPage.
 *
 * ⚠️ CRITICAL — direction-aware (Task 3.9 план, blocker Dmitry):
 * CharacterService.decrement_on_penalty реально уменьшает total_value при
 * поимке нарушителя. Если после штрафа total падает ниже текущего порога,
 * status.name может смениться на БОЛЕЕ НИЗКИЙ уровень (например,
 * «На волне» → «В потоке»). Праздновать это как level-up — UX-баг.
 *
 * Условие срабатывания toast (justLeveledUp=true) — СТРОГОЕ:
 *   initialized === true
 *   AND previousName !== null
 *   AND previousTotal !== null
 *   AND currentName !== previousName
 *   AND currentTotal > previousTotal           ← рост total = повышение
 *
 * Downgrade (currentTotal < previousTotal, даже при смене name) →
 * justLeveledUp=false.
 *
 * ⚠️ Инвариант хранения previous-значений:
 * previousName и previousTotal хранятся в ОДНОМ useRef, обновляются
 * ТОЛЬКО внутри acknowledgeLevelUp() + одноразовая calibration. На
 * каждом рендере НЕ обновляются — иначе сравнение использовало бы
 * уже актуализированное значение.
 *
 * ⚠️ Side effects в render body — ЗАПРЕЩЕНЫ (Task 3.9 review blocker):
 * React официально допускает setState в render-теле для derived-state,
 * но только если сам блок чистый. hapticImpact — это вызов нативного
 * Telegram API (side effect). В StrictMode / concurrent rendering
 * функция рендера может быть вызвана дважды без commit'а → haptic
 * сработает >1 раз на одно реальное повышение. Решение: вынести haptic
 * в useEffect, привязанный к переходу justLeveledUp false→true.
 *
 * Хранилище: useRef (НЕ useState, НЕ localStorage).
 * Haptic: useEffect, ровно один раз за реальный commit.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { hapticImpact } from "@/shared/telegram/tma";

export interface UseLevelUpStatusResult {
  previousName: string | null;
  previousTotal: number | null;
  justLeveledUp: boolean;
  acknowledgeLevelUp: () => void;
}

export function useLevelUpStatus(
  currentName: string,
  currentTotal: number,
): UseLevelUpStatusResult {
  // BOTH значения — в одном ref-объекте, обновляются атомарно.
  const previousRef = useRef<{ name: string | null; total: number | null }>({
    name: null,
    total: null,
  });
  const initializedRef = useRef(false);

  const [justLeveledUp, setJustLeveledUp] = useState(false);

  // Calibration: одноразовая установка previousRef в текущие значения
  // при ПЕРВОМ получении реальных данных (currentName !== "" означает
  // CharacterPage передал character.status.name, не пустую строку при loading).
  // На calibration рендере previousName === currentName → toast не выдаётся.
  if (!initializedRef.current && currentName !== "") {
    previousRef.current = { name: currentName, total: currentTotal };
    initializedRef.current = true;
  }

  const previousName = previousRef.current.name;
  const previousTotal = previousRef.current.total;

  // ⚠️ Чистый расчёт — никаких side effects.
  // Direction check строгий: currentTotal > previousTotal.
  const leveledUp =
    initializedRef.current &&
    previousName !== null &&
    previousTotal !== null &&
    currentName !== previousName &&
    currentTotal > previousTotal;

  // ✅ ДОПУСТИМЫЙ render-time side effect: setState для derived state.
    // Без вызова API, без mutation внешнего состояния. Если React вызовет
    // render дважды — setState просто выставит уже выставленное значение,
    // без побочек. После commit'а useEffect ниже выполнит haptic ровно один раз.
  if (leveledUp && !justLeveledUp) {
    setJustLeveledUp(true);
  }

  // ⚠️ Side effect (hapticImpact) — ТОЛЬКО в useEffect, привязан к переходу
  // justLeveledUp false→true. Гарантия: один вызов за реальный commit,
  // не за render-pass. StrictMode / concurrent rendering безопасны.
  useEffect(() => {
    if (justLeveledUp) {
      // No-op если WebApp.HapticFeedback недоступен (см. tma.ts).
      hapticImpact("medium");
    }
  }, [justLeveledUp]);

  const acknowledgeLevelUp = useCallback(() => {
    // Атомарное обновление previous → current.
    previousRef.current = { name: currentName, total: currentTotal };
    setJustLeveledUp(false);
  }, [currentName, currentTotal]);

  return {
    previousName,
    previousTotal,
    justLeveledUp,
    acknowledgeLevelUp,
  };
}