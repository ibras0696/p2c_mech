# ТЗ: C-агент для P2C-снайпинга (мульти-аккаунт) + FastAPI + Telegram

Версия 2.0 · Цель: стабильно ловить ордера за счёт минимальной задержки
detect→send, близкого размещения к Cloudflare-ноде и **параллельной работы
нескольких аккаунтов**.

Изменения относительно v1.0:
- мульти-аккаунт / мульти-сокет (несколько разных аккаунтов одновременно);
- Redis cache-aside (кеш → Postgres → кеш) для сессий и конфигов с TTL 15–30 мин;
- статистика вынесена в отдельный путь, не зависящий от C-агента.

---

## 0. Главный вывод и честная рамка (без изменений из v1.0)

Что уже измерено на Python:

```
detect_to_take_start_ms = 0–1 мс    (наша сторона уже быстрая)
take_http_ms            = 92–600 мс (это сеть + сервер, НЕ язык)
```

C сам по себе превратит 0–1 мс в ~0.05 мс — выигрыш ничтожный. **Реальный
выигрыш C — в стабильности хвоста (нет GC/джиттера, p99=p50) и в realtime-
приоритете.** Но **80% результата даёт ЛОКАЦИЯ** (близость к Cloudflare-ноде).

**Что добавляет мульти-аккаунт к этой картине:** если сервер раздаёт ордер
«кто первый», то N аккаунтов = N независимых попыток на один и тот же ордер,
каждая в рамках своего рейт-лимита. Это умножает шансы на победу, **не нарушая
правило одной попытки на аккаунт** (burst по одному аккаунту = бан 429 на 5 мин,
см. [[p2c-take-must-be-single-attempt]]). То есть мульти-аккаунт — это
легальный способ «нажать кнопку» несколько раз разными руками.

**Порядок работ не меняется:** сначала VPS в правильной локации + проверка
гипотезы на Python (Этап 0), потом C. C без переезда — почти зря.

---

## 1. Архитектура (мульти-аккаунт)

```
┌────────────────────┐   HTTP (REST)   ┌─────────────────────────────┐
│  Telegram-бот       │ ◄─────────────► │      FastAPI-сервис          │
│  (Python / aiogram) │                 │      (Python)                │
│  - UI, кнопки       │   уведомления   │  - supervisor C-агента       │
│  - статистика ◄─────┼─────────────────┼─ /stats (НЕ трогает C) ──┐   │
│  - старт/стоп       │                 │  - cache-aside Redis↔PG   │  │
└────────────────────┘                 │  - confirm/complete       │  │
                                         └───┬───────────────┬───────┘  │
                          stdin (команды)    │               │ Redis    │
                          stdout (события)   │               │ (SET TTL,│
                                             ▼               │  INCR)   │
                                   ┌───────────────────┐     │          │
                                   │   C-агент (бинарь) │     ▼          ▼
                                   │  ─────────────────│   ┌───────┐ ┌────────┐
                                   │  account #1 ─ WS ─┼─► │ Redis │ │Postgres│
                                   │  account #2 ─ WS ─┤   │ cache │ │ durable│
                                   │  account #N ─ WS ─┤   └───┬───┘ └────────┘
                                   │  (каждый свой     │       │ GET (hot-cold)
                                   │   токен/куки/     │◄──────┘ только на
                                   │   тёплый take)    │  connect/refresh
                                   └─────────┬─────────┘
                                             │ wss + https (N соединений)
                                             ▼
                                        app.send.tg
```

**Разделение ответственности (критично):**

| Компонент | Делает | НЕ делает |
|---|---|---|
| C-агент | N×(WS-приём, детект, take POST), читает сессии из Redis | confirm/complete, Telegram, запись в Postgres, refresh куки |
| FastAPI | supervisor, cache-aside (Redis↔PG), confirm/complete, /stats | UI |
| TG-бот | UI, команды, кнопка статистики | бизнес-логику ордеров |
| Redis | горячий кеш сессий/конфигов (TTL 15–30м), счётчики статы | — |
| Postgres | durable-хранилище сессий, аккаунтов, истории ордеров | горячий путь |

Идея прежняя: C — «палец на кнопке», максимально тупой и быстрый, но теперь
**с N пальцами** (по одному на аккаунт). Всё медленное — на Python.

---

## 2. Мульти-аккаунт — детальное ТЗ

### 2.1. Модель аккаунта

Каждый аккаунт — независимая сущность:

```
account {
  account_id    : строка/uuid (наш внутренний)
  label         : имя для UI ("acc-1", "Иван RUB", …)
  access_token  : bearer-токен платформы
  cookie_header : полный Cookie с __cf_bm (нужен для WS upgrade,
                  см. [[p2c-ws-needs-cf-bm-cookie]])
  did           : did-кука
  filters       : { min_amount, max_amount, currencies[] }
  enabled       : bool
}
```

### 2.2. Модель параллелизма: **поток на аккаунт**

Рекомендация: **один процесс C, по одному потоку на аккаунт** (а не N процессов
и не один общий epoll). Причины:

- **изоляция**: мёртвый/тормозящий аккаунт (реконнект, истёкший cf_bm) не
  блокирует остальные — у каждого свой цикл;
- **свои тёплые соединения**: у каждого потока свой WS и свой прогретый HTTP/2
  к take-хосту — никакой конкуренции за сокет;
- **простой realtime**: каждый поток можно запинить на своё ядро (`--cpu-map`),
  поставить `SCHED_FIFO`;
- N небольшое (ожидаем 2–10 аккаунтов) → накладные расходы на потоки ничтожны.

```
main (поток 0)
 ├─ читает stdin (команды FastAPI), раздаёт по аккаунтам через lock-free очередь
 ├─ пишет stdout (агрегирует события всех аккаунтов)
 ├─ thread acc#1 ─ epoll: [ws_fd_1] + [curl_multi_1]  → detect → take
 ├─ thread acc#2 ─ epoll: [ws_fd_2] + [curl_multi_2]  → detect → take
 └─ thread acc#N ─ epoll: [ws_fd_N] + [curl_multi_N]  → detect → take
```

- Каждый поток держит **свой** WS (свои куки) и **свой** прогретый take-канал.
- Никакого общего изменяемого состояния на горячем пути между потоками
  (кроме записи событий в один stdout — через защищённый mutex'ом writer или
  per-thread буфер + один flush-поток).

### 2.3. Дедупликация — **ПО АККАУНТУ**, не глобально

Ключевой момент мульти-аккаунта: один и тот же ордер **должен** браться
несколькими аккаунтами (это и есть умножение шансов). Поэтому:

- `seen`-сет (хеш id уже отправленных take) — **отдельный на каждый аккаунт**.
- Один аккаунт по одному ордеру шлёт take **ровно один раз** (правило одной
  попытки → нет burst-бана). Разные аккаунты по тому же ордеру — независимо.
- `inflight`/`free_slots` — тоже на аккаунт (свой бюджет одновременных take).

### 2.4. Анти-бан между аккаунтами

- **Разные исходящие IP по возможности.** Идеально — свой прокси/исходящий
  адрес на аккаунт (иначе N токенов с одного IP = подозрительно). Параметр
  `proxy` в модели аккаунта; на старте — все с одного IP, прокси добавим если
  начнёт прилетать 429/бан.
- **Свой TLS-отпечаток на аккаунт допустим** (chrome131/chrome120) — мелкая
  диверсификация, опционально.
- Никакого burst внутри аккаунта (см. 2.3).

---

## 3. Redis cache-aside (сессии и конфиги)

### 3.1. Зачем

Сессии (токен/куки) и конфиги аккаунтов нужны: C — при WS-connect и при
refresh; FastAPI — при пост-процессинге. Чтобы не долбить Postgres на каждый
чих, кладём в Redis с коротким TTL и читаем **сначала из кеша**.

### 3.2. Ключи и TTL

```
p2c:session:{account_id}   → JSON {access_token, cookie_header, did, expires_at}
                             TTL 15–30 мин (привязан к жизни __cf_bm ≈30 мин,
                             см. [[p2c-ws-needs-cf-bm-cookie]])
p2c:account:{account_id}   → JSON {label, filters, enabled, proxy}
                             TTL 30 мин (статика, обновляется реже)
p2c:accounts:enabled       → SET из account_id включённых аккаунтов
                             TTL 30 мин (список «кого запускать»)
```

### 3.3. Поток чтения (cache-aside, read-through)

**Где читает C:** только на холодных событиях — WS-connect и refresh. **НЕ на
горячем пути** (детект→take уже держит сессию в памяти потока).

```
C нужна сессия account_id:
  1. hiredis GET p2c:session:{id}
  2a. HIT  → парсим, поднимаем WS, держим в памяти потока. Конец.
  2b. MISS → эмитим в stdout {"event":"session_miss","account":id}
           → ждём команду session от FastAPI (см. ниже), не блокируя другие потоки

FastAPI ловит session_miss (cache-aside fallback):
  3. SELECT из Postgres сессию account_id
  4. SET Redis p2c:session:{id} = {...} EX <ttl>     ← переподогрев кеша
  5. send_cmd C: {"cmd":"session","account":id,"access_token":...,"cookie_header":...}
```

Тот же паттерн для `p2c:account:{id}` (фильтры/конфиг) и
`p2c:accounts:enabled` (список включённых).

### 3.4. Поток записи (единственный писатель — FastAPI/бот)

Чтобы кеш и БД не разъезжались — **писать может только FastAPI** (C — read-only
по Redis):

```
Пользователь прислал свежий cURL/сессию в бота:
  1. бот парсит (как сейчас), POST /agent/session
  2. FastAPI: UPSERT Postgres (durable)               ← источник правды
  3. FastAPI: SET Redis p2c:session:{id} EX <ttl>      ← кеш
  4. FastAPI: send_cmd C session (горячая замена куки без рестарта)
```

Инвариант: **Postgres — источник правды, Redis — производный кеш.** При любом
расхождении кеш инвалидируется (DEL) и перечитывается из Postgres.

### 3.5. Технически в C

- библиотека **hiredis** (синхронный GET на connect/refresh — это не горячий
  путь, блокировка на пару мс допустима);
- если Redis недоступен → fallback: эмитим `session_miss`, ждём session от
  FastAPI по stdin (тот сходит в Postgres). C никогда не ходит в Postgres сам.

---

## 4. C-агент — детальное ТЗ

### 4.1. Технологии

| Задача | Библиотека | Почему |
|---|---|---|
| WebSocket + TLS | **libwebsockets** | TLS, ping/pong, фрейминг |
| HTTP/2 POST + TLS-fingerprint | **curl-impersonate** (libcurl + BoringSSL) | Chrome-отпечаток, HTTP/2, keep-alive |
| Парсинг JSON | **yyjson** | быстрый, zero-copy, без аллокаций |
| Redis | **hiredis** | GET сессий на connect/refresh (не hot path) |
| Реал-тайм | POSIX `sched`, `mlock` | приоритет потока, лок памяти |

Альтернатива при срезе WS по TLS-отпечатку libwebsockets — ручной WS поверх
BoringSSL с Chrome-cipher-order (решаем на этапе 1).

### 4.2. Модули (файлы)

```
src/
  main.c        — аргументы, init, спавн потоков аккаунтов, realtime, stdin/stdout
  config.c      — разбор команд из stdin (NDJSON): session, filter, mode, add/remove account
  account.c     — состояние одного аккаунта: WS, take-канал, seen, slots, filters
  redis.c       — hiredis: GET session/account/enabled (cache-aside read side)
  ws.c          — WS-подключение, handshake Engine.IO/Socket.IO, цикл фреймов
  engineio.c    — разбор пакетов Engine.IO/Socket.IO (0/2/3/40/41/42)
  parser.c      — извлечение ордера из list:update через yyjson (zero-copy)
  taker.c       — HTTP/2 POST take через curl-impersonate, тёплое соединение
  events.c      — вывод событий в stdout (NDJSON, с account_id), debug → stderr
  net_opt.c     — TCP_NODELAY, SCHED_FIFO, mlockall, CPU affinity, prewarm
  ringbuf.c     — lock-free очередь stdin-команд → потоки аккаунтов
```

### 4.3. Протокол WebSocket (Engine.IO v4 / Socket.IO) — без изменений

```
"0"     ENGINE_OPEN     {pingInterval,pingTimeout}
"2"     ENGINE_PING     -> отвечаем "3"
"3"     ENGINE_PONG
"40"    SOCKET_CONNECT
"41"    SOCKET_DISCONNECT
"42[…]" SOCKET_EVENT    ["list:update",[…]]
```

Последовательность: connect (Cookie c __cf_bm, Origin, UA Chrome) → `0{...}` →
`40` → `40{...}` → `42["list:initialize"]` → `42["list:snapshot",…]` (игнор) →
`42["list:update",…]` (горячий путь). На `2` (ping) → `3` (pong).

Новый ордер: `op=="add"` в `list:update`, берём `data.id`, `data.in_amount`.

### 4.4. Горячий путь на каждый аккаунт (бюджет < 200 µs)

```
фрейм в ws.c (поток аккаунта)
  → engineio.c: префикс "42" (2 байта) и "list:update" (memcmp по смещению)
  → parser.c: yyjson на месте буфера, достаём id + in_amount
  → фильтр аккаунта: min<=amount<=max, currency  (fixed-point, без аллокаций)
  → если проходит, mode=running, slot свободен, id НЕ в seen этого аккаунта:
        taker.c: splice id в ПРЕД-СОБРАННЫЙ шаблон, curl_multi POST по тёплому HTTP/2
        seen.add(id); inflight++
  → events.c: order_detected + take_sent (с account_id) в stdout
```

Правила: ноль аллокаций, предсобранный шаблон, тёплое соединение (DNS
предрезолвлен, IP запинен, keep-alive), отправка не блокирует WS-поток
(write в открытый сокет + curl_multi в том же epoll).

### 4.5. Реконнект и устойчивость (на аккаунт)

- WS-разрыв → экспоненциальный backoff 1→30 с (изолированно, не трогает другие аккаунты).
- Ping-timeout > `pingTimeout` → реконнект.
- HTTP/2 упал → переоткрыть+прогреть, не блокируя WS.
- `__cf_bm` истёк (≈30 мин): сначала пробуем GET свежей сессии из Redis; если
  miss → `session_miss` → FastAPI пришлёт session по stdin.

### 4.6. Команды от FastAPI (stdin, NDJSON, по строке)

```json
{"cmd":"add_account","account":"acc1","access_token":"...","cookie_header":"...; __cf_bm=...","filters":{"min_amount":100,"max_amount":50000,"currencies":["RUB"]}}
{"cmd":"session","account":"acc1","access_token":"...","cookie_header":"...; __cf_bm=..."}
{"cmd":"filter","account":"acc1","min_amount":100,"max_amount":50000,"currencies":["RUB"]}
{"cmd":"mode","account":"acc1","value":"running"}      // running | paused; без account → всем
{"cmd":"remove_account","account":"acc1"}
{"cmd":"shutdown"}
```

- `add_account` — поднять новый поток+WS для аккаунта (или взять сессию из Redis).
- `session` — горячая замена куки/токена аккаунта без рестарта.
- `filter` / `mode` — на конкретный аккаунт (или всем).
- `remove_account` — корректно погасить поток аккаунта.

### 4.7. События в FastAPI (stdout, NDJSON, с `account`; debug → stderr)

```json
{"event":"ws_connected","account":"acc1","ts":...}
{"event":"ws_disconnected","account":"acc1","code":1006,"ts":...}
{"event":"session_miss","account":"acc1"}
{"event":"order_detected","account":"acc1","order":"6a1e...","amount":"1110","currency":"RUB","detect_ns":42000}
{"event":"take_sent","account":"acc1","order":"6a1e...","ts":...}
{"event":"take_result","account":"acc1","order":"6a1e...","status":200,"http_ms":92,"payment_id":123}
{"event":"claim_won","account":"acc1","order":"6a1e...","payment_id":123}
{"event":"claim_lost","account":"acc1","order":"6a1e...","status":400,"reason":"InvalidStatus"}
{"event":"error","account":"acc1","where":"ws","msg":"..."}
{"event":"heartbeat","ts":...,"per_account":[{"account":"acc1","orders_seen":N,"takes":M,"wins":K}]}
```

stdout — только NDJSON; человеческие логи — stderr.

### 4.8. Аргументы запуска

```
p2c_agent \
  --ws-url   wss://app.send.tg/socket.io/?EIO=4&transport=websocket \
  --base-url https://app.send.tg \
  --origin   https://app.send.tg \
  --redis    redis://127.0.0.1:6379/0 \
  --impersonate chrome131 \
  --cpu-map  "acc1:2,acc2:3"   # пин потоков аккаунтов на ядра
  --realtime                    # SCHED_FIFO (нужен CAP_SYS_NICE)
```

Куки/токены НЕ передаём аргументами (видны в `ps`) — только Redis/stdin.

### 4.9. Максимальная скорость горячего пути (detect → POST)

Цель — выжать каждую микросекунду между «фрейм пришёл» и «байты POST ушли в
сокет». Бюджет detect→send: **p99 < 200 µs** (на нашей стороне; сеть отдельно).

**Сеть / соединение:**
- **`TCP_NODELAY`** на WS- и take-сокете — выключаем Nagle, не ждём накопления буфера.
- **Тёплое HTTP/2 держим постоянно прогретым:** DNS предрезолвлен, IP запинен,
  TLS-сессия установлена ОДИН раз на старте. take = просто новый stream в уже
  открытом соединении (ни TCP-, ни TLS-handshake на горячем пути).
- **HTTP/2 PING каждые ~10–15 с** по take-соединению: держит его живым,
  не даёт edge закрыть по idle и заранее обнаруживает смерть канала.
- **Минимальные заголовки** — только `cookie` (подтверждено на Python: снятие
  origin/referer/accept дало 1700→200 мс, вероятно ушли с bot-check ветки).
- TLS 1.3, переиспользование сессии; никаких пере-handshake.

**CPU / планировщик:**
- **`SCHED_FIFO`** + пин потока аккаунта на выделенное ядро (`--cpu-map`).
- **`isolcpus` / `nohz_full`** на ядрах под аккаунты — ОС не пускает туда чужие
  задачи и тики таймера.
- **governor = performance**, отключить глубокие **C-states** (latency vs
  энергия — нам нужна латенси): ядро не «засыпает» между фреймами.
- **`mlockall(MCL_CURRENT|MCL_FUTURE)`** + pre-fault страниц на старте — ни
  одной page fault на горячем пути.

**Парсинг (самое горячее):**
- **Не парсим JSON целиком.** Структура `list:update` стабильна → байтовый скан:
  префикс `"42"` (2 байта) → `memmem("list:update")` → найти `"op":"add"` →
  `memmem("\"id\":\"")` и прочитать 24 hex-символа id, затем `in_amount`.
  yyjson — как запасной путь/валидация, не основной.
- Всё **fixed-point** (целые), без float и без аллокаций.

**Отправка:**
- Решение брать/слать — **синхронно в том же потоке**, без передачи в очередь
  (handoff стоит десятки µs и добавляет джиттер). Очередь — только для
  НЕгорячих вещей (события в stdout).
- POST = `splice id` в предсобранный шаблон + `write` в открытый stream. Тело
  пустое/минимальное.

**Логи — прочь с горячего пути** (уже выучено на Python: per-frame логирование
убивает латенси). События пишем в **per-thread lock-free ring buffer**,
отдельный поток-flusher сериализует их в stdout. Горячий поток не делает
ни `write(2)` в stdout, ни форматирования строк.

### 4.10. Стабильность и грамотная работа (p99 = p50, без сюрпризов)

**Предсказуемость латенси:**
- C + ноль аллокаций + mlock + realtime → хвост латенси плоский: **цель p99 = p50**.
  Никаких GC-пауз, никакого роста под нагрузкой.
- Тайминги только по **`CLOCK_MONOTONIC`** (не зависит от перевода часов/NTP).

**Корректность (чтобы скорость не вышла боком):**
- **Идемпотентность take:** один аккаунт по одному ордеру — ровно один POST
  (per-account `seen`), исключаем двойную отправку и burst-бан.
- **Аккуратный учёт слотов:** `inflight` инкрементим ДО отправки, декрементим
  по ответу/таймауту — не превышаем бюджет одновременных take на аккаунт.
- **Таймаут на take** (напр. 2–3 с): зависший запрос освобождает слот, не копит
  «утёкшие» inflight.

**Самовосстановление:**
- **Watchdog на каждый поток аккаунта:** если поток не двигал heartbeat дольше
  N с (завис на реконнекте/DNS) — рестарт только этого потока, остальные живы.
- **Проактивная ротация `__cf_bm`:** обновляем сессию из Redis заранее (на ~25-й
  минуте), не дожидаясь отказа WS на 30-й.
- **Реконнект с backoff 1→30 с** изолирован по аккаунту; восстановление WS не
  трогает соседей.
- **Авто-переоткрытие HTTP/2** при разрыве с немедленным прогревом.
- **Деградация при сбое Redis/Postgres:** горячий путь работает на сессии,
  которая уже в памяти потока; внешние сбои не роняют детект.

**Наблюдаемость (для доказательства, что всё быстро и стабильно):**
- `heartbeat` с per-account метриками (orders_seen/takes/wins).
- В `order_detected` — `detect_ns` (время recv→решение), в `take_result` —
  `http_ms`. По ним строим p50/p99 и доказываем p99≈p50.

---

## 5. FastAPI-сервис — ТЗ

### 5.1. Назначение

Supervisor C-агента; cache-aside Redis↔Postgres; пост-процессинг
(confirm/complete) после победы; отдаёт статистику боту.

### 5.2. Класс `AgentSupervisor`

```
- spawn(): asyncio.create_subprocess_exec("p2c_agent", ..., stdin/stdout/stderr=PIPE)
- _read_stdout(): фоновая задача, парсит NDJSON, диспатчит по типу события
- send_cmd(dict): NDJSON в stdin
- on_session_miss(account): cache-aside fallback (см. 3.3) → SET Redis → send_cmd session
- on_claim_won(account, payment_id):
      → P2CPaymentsClient.confirm(payment_id)   (httpx)
      → P2CPaymentsClient.complete(...)
      → запись в Postgres (история) + INCR Redis-счётчиков
      → уведомить Telegram
- on_take_result(...): INCR счётчиков, запись латенси
- on_session_refresh(account): UPSERT Postgres + SET Redis TTL + send_cmd session
```

### 5.3. Эндпоинты

```
POST /agent/start                запустить C-агента
POST /agent/stop                 остановить
POST /agent/account              добавить/обновить аккаунт {account, access_token, cookie_header, filters}
DELETE /agent/account/{id}       убрать аккаунт
POST /agent/session              протолкнуть свежие куки {account, access_token, cookie_header}
POST /agent/filter               {account, min_amount, max_amount, currencies}
POST /agent/mode                 {account?, value: running|paused}
GET  /agent/status               {running, accounts:[{account, ws_connected, mode, uptime}]}
GET  /stats                      статистика (см. раздел 6, НЕ обращается к C)
```

### 5.4. Победа: пост-процессинг (на Python, не горячий путь)

`claim_won` → confirm(payment_id) → complete(method_id) → запись истории в
Postgres + уведомление бота. Используем готовый httpx-код.

---

## 6. Статистика — отдельный путь (не зависит от C)

Требование: кнопка статистики работает **независимо от агента** — даже если C
занят/перезапускается/упал.

### 6.1. Как пишется

C только эмитит события. **Числа считает и хранит FastAPI**, не C:

```
take_result / claim_won / claim_lost  →  FastAPI:
   - INCR Redis-счётчиков (быстрый «сейчас»):
        p2c:stat:{account}:takes
        p2c:stat:{account}:wins
        p2c:stat:{account}:orders_seen
        p2c:stat:{account}:http_ms        (LIST/стрим для p50/p99, или HLL/гистограмма)
   - INSERT строки в Postgres (durable история):
        order_events(account_id, order_id, kind, status, http_ms, ts)
```

### 6.2. Как читается (кнопка в боте)

```
TG «Статистика» → GET /stats?account=acc1&period=today
   FastAPI:
     - быстрые счётчики из Redis (takes/wins/winrate сейчас)
     - агрегаты из Postgres (p50/p99 http_ms, история за период)
   → НИКОГДА не дёргает C-агента
```

Следствие: статистика доступна, **даже если C-агент остановлен** — данные
живут в Redis/Postgres, а не в памяти агента. Кнопка и снайпинг развязаны
полностью.

### 6.3. Что показываем

```
За период: orders_seen, takes, wins, win-rate %,
           p50/p99 take_http_ms, p50 detect→send,
           разбивка по аккаунтам, последние N побед/поражений.
```

---

## 7. Telegram-бот — что меняется

Тонкий HTTP-клиент к FastAPI:

- «Запустить» → `POST /agent/start` + по каждому включённому аккаунту `POST /agent/account`.
- «Стоп» → `POST /agent/stop`.
- «Добавить аккаунт» (приём cURL/сессии) → парсит как сейчас → `POST /agent/account`.
- Фильтры по сумме (на аккаунт) → `POST /agent/filter`.
- Статус → `GET /agent/status`.
- **Кнопка «Статистика»** → `GET /stats` (раздел 6) — работает независимо от C.
- Уведомления о победах → FastAPI дёргает Telegram Bot API (или SSE).

Парсинг cURL, шифрование сессии, схема БД — переиспользуются.

---

## 8. Деплой

### 8.1. Хост

VPS в правильной локации (близко к Cloudflare-ноде `app.send.tg`, цель пинг к
edge < 1 мс). Кандидаты подбираем триангуляцией (`triangulate.sh`) до выбора.

### 8.2. Состав

```
docker compose:
  postgres, redis          — durable + кеш/счётчики
  fastapi                  — supervisor + cache-aside + /stats
  telegram-bot             — UI
  c-agent                  — отдельно, realtime-приоритет (cap_add: SYS_NICE
                             или нативный systemd-юнит CPUSchedulingPolicy=fifo)
```

C-агенту нужен доступ к Redis (тот же compose-network) для cache-aside-чтения.

### 8.3. cloud-init

Установка Docker, git clone, сборка C (`cmake`/`make`), `docker compose up`.
Пишу отдельно после согласования ТЗ.

---

## 9. Этапы реализации (milestones)

| # | Этап | Результат / проверка |
|---|---|---|
| 0 | **Проверка гипотезы** | Текущий Python-бот на VPS в целевой локации: замер `take_http_ms`, win-rate. Понять, проигрываем ли по скорости вообще. Без этого C не начинаем. |
| 1 | C: WS + handshake (1 аккаунт) | Бинарь проходит Engine.IO handshake, логирует фреймы. Доказывает, что CF пускает TLS-отпечаток. |
| 2 | C: детект ордера | Парсит `list:update`, шлёт `order_detected`. |
| 3 | C: take POST | curl-impersonate POST, `take_result`. На старых ордерах (400) меряем латенси. |
| 4 | C: Redis cache-aside | hiredis GET session на connect; `session_miss` fallback через FastAPI. |
| 5 | **C: мульти-аккаунт** | Поток на аккаунт, свои WS/take/seen/slots; `add_account`/`remove_account`; изоляция реконнектов. |
| 6 | C: оптимизация латенси | Тёплые соединения, ноль аллокаций, SCHED_FIFO, pinning по `--cpu-map`. Цель detect→send p99 < 0.5 мс. |
| 7 | FastAPI supervisor | Спавн C, чтение событий, cache-aside, confirm/complete, INCR/история. |
| 8 | Статистика | Redis-счётчики + Postgres-история; `GET /stats` независим от C. |
| 9 | TG-бот → FastAPI | Перевод на HTTP-эндпоинты, кнопка статистики, уведомления. |
| 10 | Деплой | cloud-init, прод, замеры. |

**Этап 0 — обязателен первым.** Не пишем C, пока не подтвердим на живом VPS,
что проблема в скорости, а не в назначении ордеров на аккаунт.

---

## 10. Критерии успеха

- `detect→send` (внутри C): **p99 < 0.5 мс** (против 0–6 мс джиттера сейчас).
- `take_http_ms` из целевой локации: **p50 < 50 мс** (против ~300 мс).
- Мульти-аккаунт: N аккаунтов реально шлют независимые take по одному ордеру,
  без взаимной блокировки и без burst-бана внутри аккаунта.
- Статистика отдаётся при остановленном C.
- Win-rate: **> 0 и статистически значимо** (сейчас 1 / 1217).

---

## 11. Риски

| Риск | Вероятность | Митигация |
|---|---|---|
| Проигрываем не по скорости, а по назначению ордера на аккаунт | средняя | **Этап 0** проверяет до вложений в C |
| N токенов с одного IP → бан/429 | средняя | прокси/исходящий IP на аккаунт (поле `proxy`), без burst внутри аккаунта |
| Cloudflare режет WS по TLS-отпечатку libwebsockets | средняя | этап 1 проверяет; fallback BoringSSL+ручной WS |
| Redis недоступен | низкая | C-фолбэк на `session_miss`→stdin (Postgres через FastAPI); статистика — Postgres |
| Кеш и БД разъехались | низкая | Postgres — источник правды, единственный писатель FastAPI, DEL+перечитка при расхождении |
| `SCHED_FIFO` в Docker | низкая | `cap_add SYS_NICE` или нативный systemd-юнит |
| Сложность сопровождения C | высокая | C делает ТОЛЬКО горячий путь; мульти-аккаунт = тот же код × N потоков |

---

## 12. Оценка объёма

- C-агент (мульти-аккаунт): ~1000–1300 строк (поток-на-аккаунт +
  hiredis-чтение добавляют ~300–400 к v1.0).
- FastAPI supervisor + cache-aside + /stats: ~500 строк.
- Правки Telegram-бота: ~250 строк.
- Срок при наличии VPS и подтверждённой гипотезы: **7–11 дней**.
