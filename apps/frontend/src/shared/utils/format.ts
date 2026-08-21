export const formatKopecks = (kopecks: number): string => {
  const rubles = kopecks / 100;
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(rubles);
};

export const formatTime = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
};

export const formatDate = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
  });
};

export const formatShortDate = (isoString: string): string => {
  /** DD.MM.YYYY для badge'ов ("Членство до 05.09.2026").
   *
   * Принимает ISO-строку вида "2026-09-05" (date-only, без TZ) или
   * полный ISO datetime. Для date-only парсим вручную чтобы избежать
   * TZ-сдвига (new Date("2026-09-05") в UTC-зоне = "2026-09-04" в Москве).
   */
  const dateOnlyMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoString);
  if (dateOnlyMatch) {
    const [, y, m, d] = dateOnlyMatch;
    return `${d}.${m}.${y}`;
  }
  const date = new Date(isoString);
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  return `${day}.${month}.${date.getFullYear()}`;
};

export const formatDateTime = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

export const transactionTypeLabel = (type: string): string => {
  const labels: Record<string, string> = {
    subscription: "Подписка",
    deposit_topup: "Пополнение депозита",
    deposit_withdraw: "Вывод депозита",
    penalty: "Штраф",
    prize: "Приз",
    catcher_deposit: "Доля за поимку",
  };
  return labels[type] ?? type;
};
