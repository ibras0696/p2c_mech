# Sprint 1 — Reverse Engineering платформы

Дата старта: 19 мая 2026

## 1. Цель

Понять реальный протокол платформы до реализации боевого агента:

- как проходит авторизация;
- где и как появляется `access token`;
- как подключается `WebSocket`;
- какое событие означает новую заявку;
- какой запрос реально выполняет захват заявки;
- какие ограничения есть у multi-order режима;
- где появляется капча и как часто.

## 2. Главный результат спринта

К концу Sprint 1 должна появиться техническая карта протокола:

- `login flow`;
- `token lifecycle`;
- `WebSocket flow`;
- `new order event`;
- `claim request`;
- `claim response`;
- `multi-order behavior`;
- `captcha behavior`;
- вывод: можно ли делать `socket -> direct claim` без UI.

## 3. Правила безопасности исследования

Нельзя сохранять в репозиторий:

- реальные пароли;
- реальные access/refresh токены;
- cookies;
- localStorage/sessionStorage сессии;
- HAR-файлы с секретами;
- скриншоты с персональными данными;
- полные payload, если в них есть приватные данные.

Все чувствительные артефакты должны храниться локально в `research/ignored/` или вне репозитория.

В документах фиксируем только:

- структуру запроса;
- названия заголовков;
- типы полей;
- редактированные payload;
- выводы.

## 4. Что нужно получить перед началом

### 4.1 Доступы

- [ ] URL платформы
- [ ] Логин
- [ ] Пароль
- [ ] Подтверждение, можно ли использовать аккаунт для тестов
- [ ] Подтверждение, можно ли держать несколько активных заявок
- [ ] Telegram user id оператора

### 4.2 Среда исследования

- [ ] Windows-машина с браузером и DevTools
- [ ] Возможность открыть Network tab
- [ ] Возможность сохранить HAR локально
- [ ] Возможность сделать безопасные скриншоты
- [ ] Доступ к Ubuntu-серверу для финальной проверки сетевой доступности

## 5. Рабочий порядок исследования

### Шаг 1. Авторизация

Цель:

- понять, как платформа выдает и хранит токены.

Нужно зафиксировать:

- URL login endpoint;
- HTTP method;
- request headers;
- request body;
- response status;
- где лежит `access token`;
- есть ли `refresh token`;
- есть ли cookies;
- срок жизни токена;
- что происходит после истечения токена.

Чек-лист:

- [ ] Найден login request
- [ ] Найдено место хранения access token
- [ ] Понятно, есть ли refresh
- [ ] Понятно, можно ли восстановить сессию без ручного логина
- [ ] Понятно, появляется ли капча на login

### Шаг 2. WebSocket

Цель:

- понять, как клиент получает новые заявки.

Нужно зафиксировать:

- WebSocket URL;
- query params;
- headers;
- cookies;
- auth message после подключения, если есть;
- ping/pong или heartbeat;
- reconnect behavior;
- список типов сообщений.

Чек-лист:

- [ ] Найден WebSocket URL
- [ ] Понятна авторизация сокета
- [ ] Понятен heartbeat
- [ ] Найден event новой заявки
- [ ] Понятны поля заявки

### Шаг 3. Новая заявка

Цель:

- понять payload заявки до появления в UI.

Нужно зафиксировать:

- event type;
- id заявки;
- сумма;
- валюта;
- направление;
- таймстамп;
- любые поля фильтрации;
- признаки риска;
- срок жизни заявки.

Чек-лист:

- [ ] Найден `order_id`
- [ ] Найдены поля для фильтрации
- [ ] Понятно, приходит ли одна заявка несколько раз
- [ ] Понятно, как отличить новую заявку от обновления старой

### Шаг 4. Захват заявки

Цель:

- найти request, который делает фронт при нажатии "взять заявку".

Нужно зафиксировать:

- endpoint;
- HTTP method;
- headers;
- cookies;
- body;
- idempotency key, если есть;
- nonce/device id/fingerprint, если есть;
- response при успехе;
- response при проигрыше конкуренту;
- response при истекшем токене;
- response при капче.

Чек-лист:

- [ ] Найден claim request
- [ ] Понятны обязательные headers
- [ ] Понятен body
- [ ] Понятен success response
- [ ] Понятен failure response
- [ ] Понятно, можно ли повторять request
- [ ] Понятно, нужен ли UI после claim

### Шаг 5. Multi-order behavior

Цель:

- подтвердить, что можно держать несколько активных заявок.

Нужно проверить:

- можно ли взять вторую заявку, пока первая активна;
- есть ли лимит платформы;
- что происходит при превышении лимита;
- как UI показывает активные заявки;
- как завершение одной заявки освобождает слот.

Чек-лист:

- [ ] Multi-order разрешен
- [ ] Известен реальный лимит платформы
- [ ] Известно, как выглядит активная заявка
- [ ] Известно, как завершение влияет на новые заявки

### Шаг 6. Капча

Цель:

- понять реальный тип капчи и точку появления.

Нужно зафиксировать:

- появляется ли капча на login;
- появляется ли капча при claim;
- появляется ли капча после частых действий;
- тип капчи;
- можно ли продолжить после ручного прохождения;
- сбрасываются ли токены после капчи.

Чек-лист:

- [ ] Понятно, где появляется капча
- [ ] Понятен тип капчи
- [ ] Понятно, ломает ли капча socket/API flow
- [ ] Понятен ручной recovery flow
- [ ] Решение по автоматизации капчи отложено до отдельного анализа

## 6. Карта протокола

Заполняется по мере исследования.

### 6.1 Login

```text
Endpoint:
Method:
Headers:
Body fields:
Response fields:
Access token location:
Refresh token:
Cookies:
Captcha:
Notes:
```

### 6.2 WebSocket

```text
URL: wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket
Protocol: Engine.IO / Socket.IO style transport, EIO=4
Auth method: cookie-based auth
Required cookies: access_token=<redacted>; __cf_bm=<redacted>
Origin: https://app.send.tg
User-Agent: Chromium-based browser UA
Handshake message: server sends Engine.IO open packet `0{...}`, client sends Socket.IO namespace connect packet `40`
Heartbeat: server sends Engine.IO ping `2`, client must answer pong `3`
Reconnect: to be captured
New order event type: to be captured
Notes: socket is protected by Cloudflare cookie and platform access_token; never store real cookie values in repo.
```

### 6.2.1 Captured P2C Socket Curl

Дата фиксации: 20 мая 2026

Как получить правильный cURL из браузера:

1. Открыть `https://app.send.tg/p2c/orders` в авторизованной браузерной сессии.
2. Открыть DevTools через `F12`.
3. Перейти во вкладку `Network`.
4. Включить фильтр `WS` или в поиске Network ввести `p2c-socket`.
5. Найти запрос `wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket`.
6. Проверить, что это именно WebSocket-запрос, а не `https://app.send.tg/p2c/orders`.
7. Нажать правой кнопкой по запросу и выбрать `Copy` -> `Copy as cURL (bash)`.
8. Для анализа payload открыть вкладку `Messages` или `Frames` у этого же WebSocket-запроса.
9. Для передачи сессии в бота отправить скопированный socket cURL через экран `🔐 Сессия`.

Контрольный признак:

- в socket cURL должна быть cookie-строка с `access_token=<...>` и `__cf_bm=<...>`;
- обычный page cURL `/p2c/orders` может содержать только `__cf_bm` и не подходит для socket listener;
- реальные значения токенов нельзя вставлять в документацию, git, issue или публичный чат.

Редактированная структура подключения:

```bash
curl 'wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket' \
  -H 'Upgrade: websocket' \
  -H 'Origin: https://app.send.tg' \
  -H 'Cache-Control: no-cache' \
  -H 'Accept-Language: ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7' \
  -H 'Pragma: no-cache' \
  -b 'access_token=<redacted>; __cf_bm=<redacted>' \
  -H 'Connection: Upgrade' \
  -H 'Sec-WebSocket-Key: <browser-generated>' \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36' \
  -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits'
```

Предварительные выводы:

- подключение идет не через публичный `Crypto Pay API`, а через внутренний P2C socket интерфейса `app.send.tg`;
- авторизация сокета завязана на cookie `access_token`;
- присутствует Cloudflare cookie `__cf_bm`, значит прямой headless/client-only коннект может зависеть от browser session и fingerprint;
- URL содержит `EIO=4`, поэтому нужно исследовать Engine.IO handshake, ping/pong и формат Socket.IO messages;
- следующий шаг — снять первые входящие websocket frames после подключения и событие появления новой P2C-заявки.

Проверка подключения от 20 мая 2026:

```text
CONNECTED
RECV[0] '0{"sid":"<redacted>","upgrades":[],"pingInterval":25000,"pingTimeout":20000,"maxPayload":1000000}'
SEND '40'
RECV[1] '40{"sid":"<redacted>"}'
RECV[2] '2'
SEND '3'
```

Подтверждено:

- WebSocket handshake проходит с cookie-based auth;
- `pingInterval` равен `25000` ms;
- `pingTimeout` равен `20000` ms;
- upgrades пустые, соединение сразу работает как websocket transport;
- после Engine.IO open packet нужно отправлять `40`;
- на Engine.IO ping `2` нужно отвечать `3`.

Наблюдение около 80 секунд от 20 мая 2026:

```text
CONNECTED
RECV[0] '0{"sid":"<redacted>","upgrades":[],"pingInterval":25000,"pingTimeout":20000,"maxPayload":1000000}'
SEND '40'
RECV[1] '40{"sid":"<redacted>"}'
RECV[2] '2'
SEND '3'
RECV[3] '2'
SEND '3'
RECV[4] '2'
SEND '3'
RECV[5] '41'
CONNECTION_CLOSED
```

Интерпретация:

- бизнес-событий заявок за время наблюдения не пришло;
- `2` продолжает приходить как Engine.IO ping, ответ `3` принимается;
- `41` означает Socket.IO disconnect namespace;
- после `41` сервер закрыл WebSocket без status code;
- рабочий listener должен уметь обрабатывать `41` и выполнять reconnect;
- нужно проверить, является ли `41` штатным idle-disconnect, следствием устаревшей cookie, отсутствия подписки или серверной логикой P2C.

Подтвержденные исходящие действия клиента:

- `40` — подключение к Socket.IO namespace после Engine.IO open packet;
- `3` — pong-ответ на Engine.IO ping `2`.

Сверка cookies по предоставленным curl:

- socket curl содержит `access_token` и `__cf_bm`;
- обычный curl страницы `https://app.send.tg/p2c/orders` содержит только `__cf_bm`;
- значит для WebSocket listener нужно уметь получать и хранить оба значения, но понимать, что обычный page curl не всегда обновит `access_token`;
- Telegram-бот должен принимать curl/cookie string, извлекать секреты, не показывать их в интерфейсе и по возможности удалять исходное сообщение.

Реализация в проекте:

- `app/integrations/platform_ws/p2c_socket.py` — клиент с handshake, heartbeat и reconnect/backoff;
- `app/workers/p2c_socket.py` — worker entrypoint;
- `docker-compose.yml` — сервис `p2c-socket-worker` в профиле `socket`;
- `.env.example` — параметры `PLATFORM_WS_URL`, `PLATFORM_ACCESS_TOKEN`, `PLATFORM_CF_BM_COOKIE`, `PLATFORM_COOKIE_HEADER`.

Запуск worker через Docker Compose:

```bash
docker compose --profile socket up p2c-socket-worker
```

Локальный запуск:

```bash
py -3 -m app.workers.p2c_socket
```

Пока не подтверждено:

- можно ли отправлять прикладные Socket.IO events;
- какие event names поддерживает сервер;
- нужен ли отдельный subscribe/join message для P2C-заявок;
- можно ли выполнять claim через WebSocket;
- или claim выполняется отдельным HTTP request.

Риски:

- `access_token` и `__cf_bm` являются секретами и не должны попадать в git, логи или документацию;
- при смене Cloudflare cookie или fingerprint прямое подключение без браузерной сессии может перестать работать;
- для production может понадобиться связка browser session + socket client или socket listener внутри browser context.

### 6.2.2 Captured P2C Socket Frames (23 мая 2026)

Окно наблюдения:

- локальное время: `22:58:30` — `22:58:50` (MSK);
- тип транспорта: Socket.IO поверх Engine.IO (`42[...]` business frames).

Зафиксированная последовательность:

1. `0{"sid":"...","upgrades":[],"pingInterval":25000,"pingTimeout":20000,...}`
2. `40`
3. `40{"sid":"..."}`
4. `42["list:initialize"]` (исходящее действие клиента)
5. `42["list:snapshot", [...]]` (полный снимок текущей очереди)
6. `42["list:update", [...]]` (патчи очереди)

Подтвержденная семантика событий:

- `list:snapshot` — полный текущий список доступных заявок;
- `list:update` с `op=add` — новая заявка и полный объект `data`;
- `list:update` с `op=remove` и `pos` — удаление элемента по индексу в текущем локальном массиве;
- `list:update` с пустым массивом `[]` — no-op обновление без изменений.

Критично для реализации:

- удаление идет по `pos`, не по `id`;
- клиент обязан применять патчи строго по порядку, иначе локальное состояние "поедет";
- событие `remove` не подтверждает, что заявку взяли именно мы, оно подтверждает только исчезновение из общей ленты.

Поля заявки, которые приходят в сокете:

- `id`
- `payload`
- `url`
- `brand_name`
- `in_asset`
- `out_asset`
- `boost`
- `provider`
- `in_amount`
- `out_amount`
- `exchange_rate`
- `fee_amount`
- `mcc`
- `mcc_info`
- `expires_at`

Наблюдение по TTL и скорости исчезновения:

- `snapshot` содержал заявки с оставшимся временем около `16-21` секунд;
- новые `add` часто приходили с `expires_at` примерно через `45-60` секунд;
- часть заявок удалялась из ленты через `0.3-0.9` секунды после `add`;
- часть — через `2-6` секунд.

Точные примеры из захвата 23 мая 2026:

- `22:58:31.241` `add` `id=6a1206de6be8aa36fc345358`, `expires_at=2026-05-23T19:59:31.338Z` (`22:59:31.338` MSK), затем `22:58:32.064` `remove pos=0` (видимость ~`0.823` сек);
- `22:58:34.945` `add` `id=6a1206e67440f5cd5e5c6e50`, `expires_at=2026-05-23T19:59:19.995Z`, затем `22:58:35.303` `remove pos=0` (видимость ~`0.358` сек);
- `22:58:35.303` `add` `id=6a1206e7b99cd7d36dcda9ac`, `expires_at=2026-05-23T19:59:25.986Z`, затем `22:58:41.293` `remove pos=0` (видимость ~`5.990` сек);
- `22:58:47.112` `add` `id=6a1206f7dd80abd8a343361a`, `expires_at=2026-05-23T19:59:34.959Z`, затем `22:58:49.920` `remove pos=0` (видимость ~`2.808` сек).

Интерпретация:

- окно конкуренции очень узкое;
- `expires_at` не равен времени видимости в ленте (заявку часто снимают раньше);
- опираться на "я вижу заявку в ленте" как на признак владения нельзя.

Операционный инцидент от 23 мая 2026:

- после оплаты и подтверждения получен `15`-минутный блок из-за долгой оплаты;
- это подтверждает наличие SLA между захватом и завершением оплаты;
- агент должен блокировать захват заявок, которые оператор не успеет закрыть в допустимое окно.

Обязательные правила безопасности:

- не отправлять заявку в Telegram как "к оплате" до подтверждения владения;
- считать заявку нашей только после `claim success` + отдельной проверки ownership по `order_id`;
- если ownership не подтвержден, статус `claim_uncertain` и запрет на оплату;
- запускать таймер оплаты сразу после подтвержденного claim;
- при приближении к дедлайну поднимать alert и останавливать новые захваты при перегрузе оператора.

### 6.3 New Order Event

```json
{
  "event_type": "replace_with_real_type",
  "order_id": "redacted",
  "amount": "redacted",
  "currency": "redacted"
}
```

### 6.4 Claim Request

Подтвержденные endpoint'ы по захвату и завершению:

1. Захват заявки:

```text
Endpoint: /internal/v1/p2c/payments/take/{socket_order_id}
Method: POST
Пример id из сокета: 6a1206db7440f5cd5e5c69c7
```

2. Чтение деталей уже взятой заявки:

```text
Endpoint: /internal/v1/p2c/payments/{payment_id}
Method: GET
Пример id: 3566992
```

3. Подтверждение "Оплачено":

```text
Endpoint: /internal/v1/p2c/payments/{payment_id}/complete
Method: POST
Body: {"method":"<payment_method_id>"}
```

Подтвержденный runtime-flow (23 мая 2026):

1. Клик "взять" в UI.
2. `POST /internal/v1/p2c/payments/take/{socket_order_id}` -> `200`.
3. Навигация на `/p2c/orders/{payment_id}?back=orders`.
4. Цикл `GET /internal/v1/p2c/payments/{payment_id}` (polling статуса).
5. Статус в ответе: `processing` (+ `url`, `payload`, `provider`, `processing_at`).
6. Клик "Оплачено".
7. `POST /internal/v1/p2c/payments/{payment_id}/complete` с `method`.
8. Повторный `GET /internal/v1/p2c/payments/{payment_id}` и возврат на список `/p2c/orders`.

Важные выводы:

- `socket_order_id` и `payment_id` разные сущности:
  - `socket_order_id` приходит в ленте (`6a...`);
  - `payment_id` числовой (`3566992`) появляется после успешного `take`.
- `cf-ray` в таких запросах не является бизнес-параметром логики захвата.
- `sentry-trace`/`baggage` можно не считать обязательными для бизнес-логики claim.
- запросы на `sentry.cbt.dev/.../envelope` — это телеметрия фронта, не канал работы с заявками.

Что нужно дополнительно подтвердить:

- формат успешного тела ответа `take`;
- типы ошибок `take` (конкурент забрал раньше, таймаут, невалидная сессия);
- обязательность поля `method` в `/complete` и источник `payment_method_id`;
- финальный статус заявки после `complete`.

### 6.5 Multi-order

```text
Can hold multiple orders: подтверждено бизнес-требованием проекта
Observed platform limit: не зафиксирован в протоколе, требует отдельного теста
Expected bot default limit: 3 (конфигурируется через Telegram)
What releases slot: только подтвержденное завершение заявки (`/complete`) и перевод в closed status
Notes: удаление из ленты сокета (`list:update remove`) не освобождает слот активной заявки агента
```

## 7. Решения, которые должны выйти из Sprint 1

- [ ] Используем ли direct API claim как основной путь
- [ ] Нужен ли Playwright для claim или только для login/reauth
- [ ] Нужен ли `CloakBrowser` эксперимент в Sprint 5
- [ ] Какой начальный лимит активных заявок ставим
- [ ] Какие поля заявки доступны для фильтрации
- [ ] Какие данные нужно хранить в БД
- [ ] Какие ошибки платформы нужно обрабатывать первыми

## 8. Definition of Done

Sprint 1 считается завершенным, если:

- login flow описан;
- token lifecycle описан;
- WebSocket flow описан;
- event новой заявки найден;
- claim request найден;
- success/failure responses описаны;
- multi-order behavior подтвержден или опровергнут;
- captcha behavior описан;
- принято решение по основному claim path.

## 9. Текущий статус

- [x] Sprint 1 рабочий документ создан
- [x] Правила хранения чувствительных артефактов зафиксированы
- [x] Первичный P2C WebSocket URL зафиксирован
- [x] WebSocket handshake проверен
- [x] Engine.IO heartbeat проверен
- [ ] Доступы к платформе получены
- [ ] Login flow исследован
- [ ] Event новой P2C-заявки исследован
- [ ] Claim request исследован
- [ ] Multi-order проверен
- [ ] Captcha behavior проверен
