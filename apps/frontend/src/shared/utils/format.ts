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
    bonus_catch: "Бонус за поимку",
    bonus_subscription: "Бонус за подписку",
    bonus_points: "Бонусные баллы",
  };
  return labels[type] ?? type;
};
