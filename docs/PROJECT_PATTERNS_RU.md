# Паттерны проекта, структура, линтинг и инфраструктура

Дата: 19 мая 2026

## 1. Назначение

Этот документ фиксирует общие стандарты проекта:

- структуру файлов и каталогов;
- слои приложения и границы ответственности;
- паттерны кода;
- правила линтинга и типизации;
- базовую Docker-инфраструктуру;
- общую картину разворачивания на `Ubuntu`.

Документ задает инженерную базу до начала основной бизнес-реализации.

## 2. Общая картина

Проект должен строиться как серверное асинхронное Python-приложение с несколькими runtime-контурами:

- `API service` для health-check, admin endpoints и orchestration hooks;
- `Telegram Bot` на `aiogram 3+` для управления агентом;
- `Browser worker` для `Playwright` fallback и ручной/полуавтоматической reauth-логики;
- `PostgreSQL` для постоянных данных;
- `Redis` для быстрых состояний, блокировок и координации.

Рекомендуемая модель разворачивания:

- первичная разработка и быстрые проверки выполняются на `Windows`;
- финальная runtime-проверка выполняется на `Ubuntu`;
- `Docker` для сборки образов;
- `docker compose` для сервиса на одном `Ubuntu` сервере;
- в дальнейшем возможен переход к раздельным сервисам или оркестрации.

## 3. Рекомендуемая структура репозитория

```text
project/
  app/
    api/
    bot/
    core/
    domain/
    integrations/
      platform_api/
      platform_ws/
      telegram/
      browser/
    repositories/
    services/
    workers/
  tests/
  docker-compose.yml
  Dockerfile
  pyproject.toml
  .env.example
  .editorconfig
  .gitignore
  .dockerignore
```

## 4. Ответственность каталогов

### `app/core`

Хранит инфраструктурный минимум:

- конфигурацию;
- логирование;
- общие константы;
- базовые исключения;
- примитивы приложения.

Запрещено:

- держать здесь бизнес-логику захвата заявок;
- смешивать core со специфичными для Telegram или платформы деталями.

### `app/domain`

Хранит доменную модель:

- сущности;
- value objects;
- доменные статусы;
- доменные правила;
- интерфейсы доменных сервисов.

Правило:

- домен не должен зависеть от Telegram, Redis, HTTP-клиентов и Playwright.

### `app/services`

Хранит use-case слой:

- orchestration;
- state transitions;
- claim flow;
- paid flow;
- обработку команд оператора.

Правило:

- сервисы используют domain и repositories;
- сервисы не должны напрямую знать про SQL-реализацию.

### `app/repositories`

Хранит адаптеры к данным:

- PostgreSQL repositories;
- Redis repositories;
- read/write access layer.

### `app/integrations`

Хранит интеграции с внешними системами:

- API платформы;
- `WebSocket` платформы;
- Telegram transport;
- Playwright/browser adapter.

### `app/api`

Хранит HTTP-слой:

- `FastAPI` app;
- health endpoints;
- внутренние admin endpoints;
- webhook endpoints, если позже понадобятся.

### `app/bot`

Хранит Telegram-бота:

- router;
- handlers;
- keyboards;
- bot bootstrap.

Правило:

- Telegram handlers разделяются по сценариям;
- нельзя складывать все handlers в один файл;
- команды должны быть минимальными;
- основной UX строится через единую inline-панель;
- общие guards, callback helpers и UI renderers выносятся отдельно.

### `app/workers`

Хранит долгоживущие background entrypoints:

- browser worker;
- синхронизацию;
- возможные async consumers.

### `tests`

Хранит:

- unit tests;
- integration tests;
- smoke tests.

## 5. Слои и зависимость между ними

Правильное направление зависимостей:

`api/bot/workers -> services -> domain`

`services -> repositories / integrations interfaces`

`repositories / integrations -> concrete external systems`

Нельзя:

- импортировать `FastAPI` или `aiogram` в domain;
- тянуть SQLAlchemy-модели напрямую в handlers бота;
- строить бизнес-решения внутри transport-слоя.

## 6. Ключевые паттерны кода

### 6.1 State Pattern

Используется для:

- состояния агента;
- состояния отдельной заявки.

Причина:

- проект stateful;
- логика сильно зависит от текущего operational status;
- нужны явные допустимые переходы.

### 6.2 Strategy Pattern

Используется для:

- выбора механизма захвата заявки.
- выбора механизма обработки капчи.

Стратегии:

- `DirectApiClaimStrategy`
- `PlaywrightClaimStrategy`
- `PrimaryCaptchaSolverStrategy`
- `BackupCaptchaSolverStrategy`
- `HumanCaptchaFallbackStrategy`

Причина:

- быстрый путь и fallback должны быть взаимозаменяемыми;
- нельзя зашивать browser path как единственный сценарий.
- внешний captcha solver не должен становиться частью ядра бизнес-логики.

### 6.3 Repository Pattern

Используется для:

- доступа к агентам;
- активным заявкам;
- сессиям платформы;
- логу событий.

Причина:

- бизнес-логика должна тестироваться без реальной БД;
- смена persistence-реализации не должна ломать services.

### 6.4 Command Pattern

Используется для:

- команд, приходящих из Telegram.

Примеры:

- `RunAgentCommand`
- `PauseAgentCommand`
- `MarkPaidCommand`

### 6.5 Pub-Sub / Event-driven

Используется для:

- реакции на новые заявки из сокета;
- событий логина, капчи, переподключения;
- развязки контуров уведомлений и основной логики.

### 6.6 Circuit Breaker

Используется для:

- защиты от повторяющихся ошибок платформы;
- временной блокировки интенсивных повторов.

### 6.7 Retry with Backoff

Используется для:

- reconnect сокета;
- временных сетевых ошибок;
- второстепенных интеграционных шагов.

Ограничение:

- критическое действие `claim` нельзя повторять без идемпотентности.

## 7. Паттерны на уровне кода

### 7.1 Один файл — одна роль

Файл должен иметь узкую ответственность.

Хорошо:

- `config.py` только конфиг;
- `runner.py` только bootstrap;
- `health.py` только health router.

Плохо:

- один модуль с конфигом, бизнес-логикой, ботом и HTTP-клиентом сразу.

### 7.2 Явные интерфейсы

На границах модулей должны быть:

- typed DTO;
- protocols или abstract interfaces;
- явные return types;
- минимум "магических" dict без схемы.

### 7.3 Async-first

Проект должен быть асинхронным по умолчанию.

Правило:

- новый IO-bound код пишется как `async`;
- блокирующие операции выносятся отдельно;
- нельзя случайно тянуть sync DB/HTTP клиент в async runtime.

### 7.4 Fail-fast config

Если не хватает обязательного секрета или URL, сервис должен падать при старте, а не в середине runtime.

### 7.5 Typed config

Переменные окружения должны входить в приложение через типизированный `Settings` объект.

### 7.6 Structured logging

Логи должны иметь структуру и идентификаторы:

- `agent_id`
- `order_id`
- `event_type`
- `correlation_id`

### 7.7 Не хранить бизнес-правила в handlers

`FastAPI` handlers и `aiogram` handlers должны:

- валидировать вход;
- вызвать service;
- отдать результат.

Бизнес-логика не должна жить в transport-слое.

### 7.8 Captcha Solver Boundary

Контур автоматического решения капчи должен быть отдельным адаптером.

Правила:

- ядро работает с интерфейсом `CaptchaSolverProvider`;
- провайдеры включаются только через конфиг;
- ручной оператор остается fallback;
- ошибки provider API не должны ломать весь агент;
- секреты solver-провайдеров не попадают в логи.

## 8. Стандарты именования

### Python-модули

- `snake_case`

### Классы

- `PascalCase`

### Константы

- `UPPER_SNAKE_CASE`

### Асинхронные сервисы

Имена должны отражать действие:

- `claim_order`
- `mark_order_paid`
- `reconnect_socket`

### Telegram handlers

Имена по сценарию:

- `handle_run_command`
- `handle_pause_command`
- `handle_mark_paid_callback`

## 9. Линтинг и качество

### 9.1 Обязательные инструменты

- `ruff` для lint и import rules;
- `mypy` для статической типизации;
- `pytest` для тестов;
- `pytest-asyncio` для async tests.

### 9.2 Базовые правила линтинга

- импортов без использования быть не должно;
- wildcard imports запрещены;
- длинные функции дробятся на сервисные шаги;
- публичные функции должны иметь типы;
- сложные dict-based контракты выносятся в модели.

### 9.3 Минимальная дисциплина тестов

Нужно покрывать тестами:

- state transitions;
- фильтрацию заявок;
- dedup;
- освобождение слота после `Оплачено`;
- обработку ошибок platform API.

## 10. Инфраструктурные паттерны

### 10.1 Один образ, несколько entrypoints

На старте проекта допустимо иметь один Docker image, который запускается разными командами:

- API;
- bot;
- browser worker.

Плюсы:

- проще поддержка;
- одна dependency graph;
- быстрее MVP.

Минус:

- образ тяжелее;
- Playwright runtime тянет лишние зависимости.

### 10.2 Разделение runtime-ролей

Даже при одном образе роли должны быть разделены на уровне процессов и entrypoints.

### 10.2.1 Browser Runtime Boundary

Браузерный runtime должен быть заменяемым.

Базовый вариант:

- `Playwright Chromium`.

Опциональный кандидат для исследования:

- `CloakBrowser`.

Правила:

- browser runtime не должен быть зашит в бизнес-логику;
- выбор runtime должен идти через конфиг или отдельную factory;
- `CloakBrowser` не считается solver'ом капчи;
- решение о production default принимается только после тестов на Windows и Ubuntu.

### 10.3 Redis как operational storage

`Redis` нужен не вместо БД, а для:

- active set;
- locks;
- короткоживущих счетчиков;
- быстрой координации.

### 10.4 PostgreSQL как system of record

`PostgreSQL` хранит:

- историю заявок;
- статусы;
- аудит;
- сессии;
- конфигурацию.

Сессии платформы хранятся только в зашифрованном виде:

- приложение принимает `access_token` и `__cf_bm` через Telegram-бота;
- перед записью в PostgreSQL значения шифруются через `SESSION_ENCRYPTION_KEY`;
- таблица `platform_sessions` не должна содержать открытых токенов;
- `SESSION_ENCRYPTION_KEY` хранится только в `.env` или secret storage;
- при потере ключа старые сессии нельзя расшифровать, нужно заново передать socket cURL через бота.

## 11. Docker-паттерны

### 11.1 Что должно быть в `Dockerfile`

- базовый `python:3.12-slim`;
- системные зависимости под Python и Playwright;
- рабочая директория `/app`;
- установка зависимостей из `pyproject.toml`;
- copy исходников;
- не-root пользователь;
- дефолтная команда только как запасной вариант.

### 11.2 Что должно быть в `docker-compose.yml`

- `app`
- `bot`
- `postgres`
- `redis`

Опционально:

- `browser-worker`
- `nginx`

### 11.3 Что должно быть в `.dockerignore`

- `.git`
- `.venv`
- `__pycache__`
- `.pytest_cache`
- `.mypy_cache`
- локальные логи и временные файлы

## 12. Разворачивание на Ubuntu

Базовый стек сервера:

- `Ubuntu 22.04 LTS` или новее;
- `Docker Engine`;
- `Docker Compose Plugin`;
- `ufw`;
- системный пользователь под деплой.

Минимальный operational подход:

- код на сервере;
- `.env` с секретами;
- `docker compose up -d --build`;
- restart policy `unless-stopped`;
- логирование через `docker logs` или внешний сборщик.

## 13. Что закреплено в репозитории

В репозитории должны лежать:

- `pyproject.toml` с `ruff`, `mypy`, `pytest` конфигом;
- `Dockerfile`;
- `docker-compose.yml`;
- `.env.example`;
- `.editorconfig`;
- `.gitignore`;
- `.dockerignore`;
- минимальный каркас `app/`.

## 14. Правила дальнейшего развития

Когда начнется реализация бизнес-логики, нужно соблюдать:

- не размывать слои;
- не класть интеграционные детали в домен;
- не делать `Playwright` ядром бизнес-логики;
- не превращать Telegram handlers в сервисный слой;
- не хранить состояние только в памяти процесса.

## 15. Практический вывод

Правильная база проекта для вашего кейса:

- асинхронный Python backend;
- `FastAPI` для API-контура;
- `aiogram 3+` для control-plane;
- `Redis` для active state;
- `PostgreSQL` для истории;
- `Docker Compose` для Ubuntu;
- `ruff + mypy + pytest` как минимальный quality gate;
- кодовая база, построенная на `State`, `Strategy`, `Repository`, `Command` и event-driven модели.
