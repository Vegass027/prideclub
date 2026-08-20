## Резюме

Проблема не в CSP Telegram WebApp iframe и не в кросс-доменном редиректе как таковом. Корневая причина — заголовок `Content-Disposition: attachment`, который Telegram CDN отправляет вместе с `Content-Type: application/octet-stream`. Браузер интерпретирует эту комбинацию как явную команду «скачать файл, не показывать» и отказывается рендерить содержимое внутри `<img>` независимо от того, находится страница в iframe или открыта напрямую . Правильное и единственное production-safe решение — **backend-проксирование с переустановкой корректных заголовков**, а не попытки заставить браузер игнорировать заголовки Telegram CDN.

## Почему `<img>` не рендерится: точный механизм отказа

Спецификация `Content-Disposition` прямо указывает: если этот заголовок установлен в значение `attachment` даже вместе с типом `application/octet-stream`, «подразумеваемая инструкция — что user agent не должен отображать ответ, а сразу должен предложить диалог сохранения файла» . Это подтверждается независимо в справочнике по HTTP-заголовкам: «Content-Type: application/octet-stream вместе с attachment принудительно вызывает скачивание для любого типа файла» . Ровно эта комбинация присутствует в ответе Telegram CDN (`content-type: application/octet-stream`, `content-disposition: attachment`), которую вы зафиксировали через `curl -I`.

Важный нюанс: это поведение браузера **не связано с CSP** и не специфично для Telegram WebApp iframe. Обычный `<img>` тег на любой веб-странице (внутри iframe или нет) откажется рендерить ресурс с такими заголовками — это подтверждают независимые обсуждения на Stack Overflow и практика проекта PocketBase, которые явно указывают: файлы отдаются с `Content-Disposition: inline` только для явно распознанных image-MIME-типов (`image/png`, `image/jpeg` и т.п.), а всё остальное получает `attachment` и не рендерится . Таким образом, гипотеза про специфичный CSP Telegram Mini App иframe не подтверждается — достаточно уже известной механики браузера.

Это также объясняет, почему `curl` «работает»: `curl` не интерпретирует `Content-Disposition`/`Content-Type` — он просто получает байты. А браузерный `<img>`, наоборот, строго следует этим заголовкам для решения «от��исовать или скачать».

## Ответы на исходные вопросы

**1. Есть ли специфичный CSP у Telegram WebApp iframe, блокирующий img с cross-origin редиректами или octet-stream?** Официальной публичной CSP-спецификации именно для `img-src` внутри Mini App iframe в документации Telegram Bot API/Mini Apps не существует — это подтверждается отсутствием такого раздела в официальных источниках . Проблема воспроизводится из-за заголовков Telegram CDN, а не CSP-политики самого iframe — обычная страница вне Telegram столкнулась бы с тем же отказом рендеринга по той же причине.

**2. Как работает `@telegram-apps/telegram-ui` Avatar с прямыми URL?** Компонент — обычный `<img src="...">`, не делающий магии; он работает в примерах, потому что примеры используют внешние CDN-URL (собственные картинки авторов или сторонние сервисы), которые отдают корректный `Content-Type: image/*` и не ставят `Content-Disposition: attachment`. Если тот же компонент получит на входе URL Telegram Bot API `/file/bot<TOKEN>/...`, он столкнётся с той же проблемой — библиотека не решает вопрос заголовков CDN.

**3. Публичный CDN-URL без `bot<TOKEN>/`, работающий в `<img>`?** Такого официального публичного URL-формата не существует. Все методы Telegram Bot API (`getFile`, `getUserProfilePhotos`) возвращают только `file_path`, который комбинируется в URL `https://api.telegram.org/file/bot<TOKEN>/<file_path>` — эта структура жёстко завязана на токен бота, что уже само по себе является причиной, почему прямая отдача такого URL клиенту небезопасна (раскрывает токен) .

**4. Что делают реальные production Mini Apps?** Проект `deptyped/telegram-file-proxy` — открытый прокси-сервер, созданный специально для этой задачи: «позволяет предоставлять пользователям ссылки на файлы по `file_id` без раскрытия токена бота... особенно полезно для функции WebApp, чтобы использовать файлы Telegram в вашем веб-приложении» . Это прямое доказательство того, что стандартный паттерн в экосистеме — собственный backend-прокси, а не прямые ссылки на `api.telegram.org`.

## Сравнение архитектурных вариантов

| Вариант | Безопасность | Нагрузка | Корректность заголовков | Рекомендация |
|---|---|---|---|---|
| A. Backend-проксирование (стриминг с переустановкой Content-Type) | Высокая — токен не раскрывается | Небольшая (I/O бандвич через backend) | Полный контроль над заголовками | **Рекомендуется** |
| B. Локальное кеширование JPEG на диск + отдача через nginx | Высокая | Минимальная после первого запроса | Полный контроль | **Рекомендуется как оптимизация A** |
| C. Редирект на другой формат URL (`t.me/i/userpic/...`) | Не гарантирована — недокументированный внутренний формат | Низкая | Не контролируется вами | Не рекомендуется — недокументированный API, может измениться без предупреждения |
| D. `tg://` протокол | Неприменимо к `<img>` | — | — | Не работает — `tg://` — deep-link схема для открытия приложения Telegram, не ресурс для рендеринга изображений в HTML |
| E. Другой метод Bot API, отдающий иной URL | Не существует такого метода | — | — | `getUserProfilePhotos`/`getFile` возвращают только `file_id`/`file_path`, ведущие к тому же CDN с тем же токеном  |

## Рекомендуемое решение

### Вариант A+B (гибрид): проксирование с диск-кешем — оптимально для вашего масштаба (10-100 пользователей, файлы 30-50 КБ)

Учитывая небольшой объём данных, лучшее решение — модифицировать существующий `AvatarService`, чтобы backend скачивал файл с Telegram CDN один раз, сохранял на диск (volume, монтированный в контейнер) и отдавал напрямую через nginx с корректными заголовками при последующих запросах. Это устраняет 307-редирект полностью — клиент получает `200 OK` с `image/jpeg` сразу с вашего домена, без обращения к `api.telegram.org` из браузера.

```python
# apps/backend/app/services/avatar_service.py
import hashlib
from pathlib import Path

AVATAR_CACHE_DIR = Path("/data/avatars")  # монтируется как Docker volume

class AvatarService:
    async def get_or_fetch_local_path(self, user_id: int, file_id: str) -> Path | None:
        cache_path = AVATAR_CACHE_DIR / f"{user_id}.jpg"
        cached_file_id = await self._get_cached_file_id(user_id)  # Redis, 6h TTL

        if cache_path.exists() and cached_file_id == file_id:
            return cache_path

        file_path = await self._fetch_file_path(file_id)  # bot.getFile
        if not file_path:
            return None

        cdn_url = self._build_cdn_url(file_path)
        async with self._http_session.get(cdn_url) as resp:
            if resp.status != 200:
                return None
            content = await resp.read()

        AVATAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(content)
        await self._cache_file_id(user_id, file_id)
        return cache_path
```

```python
# apps/backend/app/api/v1/users.py
from fastapi.responses import FileResponse

@router.get("/users/{user_id}/photo")
async def get_user_photo(
    user_id: int,
    caller: TelegramUserDep,
    session: SessionDep,
    avatar: AvatarServiceDep,
) -> FileResponse:
    target = await session.get(User, user_id)
    if target is None or not target.photo_file_id:
        raise PhotoUnavailableError()

    local_path = await avatar.get_or_fetch_local_path(target.id, target.photo_file_id)
    if local_path is None:
        raise PhotoUnavailableError()

    return FileResponse(
        local_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=21600"},  # 6h, синхронно с Redis TTL
    )
```

Для отдачи через nginx напрямую (минуя даже FastAPI, для максимальной производительности при кешированном файле):

```nginx
location /api/v1/users/ {
    location ~ ^/api/v1/users/(\d+)/photo/cached$ {
        alias /data/avatars/$1.jpg;
        add_header Content-Type image/jpeg;
        add_header Cache-Control "public, max-age=21600";
        try_files $1.jpg @backend_proxy;
    }
    location @backend_proxy {
        proxy_pass http://backend:8000;
    }
}
```

### Почему не вариант «чистый проксирующий стриминг без диска» (только A)

Потоковое проксирование каждого запроса через FastAPI/aiohttp без диск-кеша (Вариант A в чистом виде) добавляет постоянную нагрузку на backend и внешний трафик к Telegram при каждом просмотре лидерборда каждым пользователем — при 100 пользователях и частом обновлении лидерборда это неэффективно по сравнению с одноразовым скачиванием на диск. Диск-кеш (Вариант B) снижает нагрузку почти до нуля после первого запроса ��а аватар, а Redis-кеш `file_path` (который у вас уже реализован) остаётся полезным для инвалидации при смене фото пользователем.

## Итоговый архитектурный принцип

Ни один продакшн Mini App не должен отдавать клиенту прямую ссылку на `api.telegram.org/file/bot<TOKEN>/...` — это раскрывает токен бота третьим лицам через DOM/Network tab и одновременно ломается из-за заголовков `Content-Disposition: attachment`, которые Telegram CDN отправляет по умолчанию для всех файлов, включая фото профиля . Правильный паттерн, подтверждённый существующими открытыми решениями в экосистеме (`deptyped/telegram-file-proxy`), — собственный backend-прокси, который скрывает токен и переустанавливает `Content-Type`/`Content-Disposition` в значения, понятные браузеру .