# Критический анализ потока P2C и E2E-покрытие

## 1) Критический путь

Бизнес-критичный пайплайн:

1. Приходит `list:update`/`list:snapshot` из Socket.IO.
2. Агент фильтрует заявку по `min/max` и свободным слотам.
3. Делает `POST /internal/v1/p2c/payments/take/{socket_order_id}`.
4. Делает `GET /internal/v1/p2c/payments/{payment_id}` для подтверждения владения.
5. Добавляет заявку в `active_orders`.
6. Отправляет оператору данные для оплаты в Telegram.
7. По кнопке `Оплачено` делает `POST /internal/v1/p2c/payments/{payment_id}/complete`.

Это самый чувствительный участок: ошибка после `take` может привести к блоку, если заявка потеряна в локальном состоянии.

## 2) Найденные критичные риски и исправления

### Риск A: необработанное падение фоновой task

Симптом: `Task exception was never retrieved` при исключении внутри `_process_event`, пользователь не получает сигнал в боте.

Что сделано:
- Добавлен `done_callback` с централизованным логированием падения task.
- Добавлен `try/except` на уровне `_process_event`.

Логи:
- `p2c_live_agent_process_event_unhandled`
- `p2c_live_agent_process_event_task_failed`

### Риск B: take успешен, confirm падает (сетевой/API сбой), заявка теряется

Это самый опасный кейс: если `take` уже вернул `payment_id`, но `confirm` не прошел, заявка могла быть «взята», но оператор о ней не узнает.

Что сделано:
- Если `take` успешен и дальше `api_error`, заявка сохраняется как активная (`active_orders`), даже без `method_id`.
- Агент переводится в `PAUSED`.
- Сокет останавливается, чтобы не брать новые заявки вслепую.
- Оператору отправляется уведомление (если доступно).

Логи:
- `p2c_live_agent_taken_unconfirmed_paused`
- `p2c_live_agent_notify_failed_after_taken_unconfirmed`

### Риск C: гонка с ручной паузой

Сценарий: оператор поставил паузу, но уже запущенная task успевает сделать `take`.

Что сделано:
- Перед `take` добавлена дополнительная проверка `mode == PAUSED` с early return.

Лог:
- `p2c_live_agent_claim_skipped_paused`

### Риск D: сбой уведомления в Telegram после успешного claim

Сценарий: заявка взята, но уведомление не ушло (ошибка бота/сети), оператор не видит, что нужно срочно оплачивать.

Что сделано:
- При сбое уведомления агент принудительно уходит в `PAUSED`.
- Сокет останавливается.

Лог:
- `p2c_live_agent_notify_failed_paused`

### Риск E: падение цикла агента при ошибке хранилища сессии

Сценарий: временно недоступна БД/репозиторий сессии, `current()` выбрасывает исключение и task агента завершается.

Что сделано:
- Ошибки внутри итерации `run_forever` теперь перехватываются и не убивают агент.
- Добавлен отдельный лог итерации, чтобы видеть такие сбои.

Лог:
- `p2c_live_agent_loop_iteration_failed`

## 3) E2E-тесты

Добавлены E2E-тесты сервисного уровня:

- `tests/test_e2e_agent_flow.py::test_e2e_socket_update_to_complete_flow`
  - полный путь: socket update -> take -> confirm -> notify -> complete;
  - проверяет, что заявка появляется в active и корректно закрывается.

- `tests/test_e2e_agent_flow.py::test_e2e_taken_order_is_preserved_when_confirm_api_fails`
  - аварийный путь: take успешен, confirm падает;
  - проверяет, что заявка не теряется, агент уходит в `PAUSED`, уведомление формируется.

Дополнительно усилен unit-level сценарий:
- `tests/test_p2c_live_agent.py::test_live_agent_keeps_taken_order_and_pauses_when_confirm_fails`
- `tests/test_p2c_live_agent.py::test_live_agent_run_forever_survives_session_repository_errors`

## 4) Что осталось как операционные ограничения

1. `InvalidStatus` на `take` — это проигранная гонка по скорости, не баг клиента.
2. Высокий `take_http_ms` — в основном сеть/RTT/нагрузка сервера, не логическая ошибка.
3. Для боевого режима важны:
   - стабильный канал до `app.send.tg`;
   - свежая сессия (`access_token` + `__cf_bm`);
   - ручная дисциплина подтверждения `Оплачено` только после фактической оплаты.

## 5) Проверка после деплоя

После обновления контейнера проверить:

1. При искусственном сбое notify появляется `p2c_live_agent_notify_failed_paused`.
2. При `take` + проблеме confirm появляется `p2c_live_agent_taken_unconfirmed_paused`.
3. В `PAUSED` новых `take` нет.
4. Кнопка `Оплачено` закрывает активный ордер и освобождает слот.

## 6) Подготовка к Cancel (до получения cURL)

Уже подготовлено в коде:

1. В карточке активной заявки добавлена кнопка `🛑 Отменить заявку`.
2. В `P2CLiveAgent` добавлен метод `cancel_order(...)`.
3. Сейчас `cancel_order(...)` работает как безопасная заглушка и не снимает заявку локально без подтвержденного API-контракта.

После получения cURL на отмену:

1. Подключить endpoint в `P2CPaymentsClient.cancel(...)`.
2. В `cancel_order(...)` добавить вызов API + проверку статуса ответа.
3. Освобождать слот только после успешного ответа платформы.
4. Добавить E2E для `cancel happy / cancel api_error / race with complete`.
