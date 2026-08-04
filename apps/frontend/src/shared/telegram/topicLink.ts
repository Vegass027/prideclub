export function buildTopicLink(
  chatId: number,
  threadId: number,
): string {
  // Telegram показывает в ссылках t.me/c/<short_id> короткий chat_id без
  // префикса -100, который добавляет Bot API для супергрупп. Префикс
  // нужно отбросить, иначе Telegram не откроет топик корректно.
  let shortId = chatId;
  if (chatId < -100_000_000_0000) {
    shortId = -(chatId + 100_000_000_0000);
  } else if (chatId > 0 && String(chatId).length >= 12) {
    shortId = -100_000_000_0000 - chatId;
  }
  return `https://t.me/c/${shortId}/${threadId}`;
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

export function openChatRoot(
  chatId: number | null,
  inviteLink: string | null,
): boolean {
  if (inviteLink) {
    const tg = window.Telegram?.WebApp;
    if (tg?.openTelegramLink) {
      tg.openTelegramLink(inviteLink);
      return true;
    }
    if (tg?.openLink) {
      tg.openLink(inviteLink);
      return true;
    }
    window.open(inviteLink, "_blank", "noopener,noreferrer");
    return true;
  }
  if (!chatId || chatId === 0) return false;
  const url = buildTopicLink(chatId, 1);
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