export function buildTopicLink(
  chatId: number,
  threadId: number,
): string {
  return `https://t.me/c/${chatId}/${threadId}`;
}

export function openCheckinTopic(
  chatId: number | null,
  threadId: number | null,
): boolean {
  if (!chatId || chatId === 0 || !threadId) return false;
  const url = buildTopicLink(chatId, threadId);
  const tg = window.Telegram?.WebApp;
  if (tg?.openTelegramLink) {
    tg.openTelegramLink(url);
    return true;
  }
  if (tg?.openLink) {
    tg.openLink(url);
    return true;
  }
  window.open(url, "_blank", "noopener,noreferrer");
  return true;
}