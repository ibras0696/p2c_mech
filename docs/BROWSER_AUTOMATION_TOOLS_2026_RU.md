# Инструменты браузерной автоматизации в 2026: что смотреть кроме Playwright

Дата: 16 мая 2026

## Короткий вывод

Если вопрос стоит как "что нового появилось рядом с `Playwright`", то рынок сейчас делится на 2 класса:

- классические code-first фреймворки;
- новые AI-native инструменты для браузерной автоматизации.

Если нужен мой практический вывод в одной строке:

- для надежных детерминированных сценариев `Playwright` все еще остается сильной базой;
- если хочется более "живучую" автоматизацию на меняющихся интерфейсах, смотреть надо в первую очередь на `Stagehand`, `Skyvern` и `Browser Use`;
- если нужен enterprise-совместимый стандарт, смотреть на `Selenium` и `WebdriverIO`;
- если задача в основном Chrome/Chromium и нужен легкий runtime, смотреть на `Puppeteer`.

## 1. Что реально новое по сравнению с классическим стеком

### `Stagehand`

Что это:

- open-source SDK для browser agents;
- делает упор на AI-управление браузером через команды вроде `act`, `extract`, `observe`, `agent`;
- позиционируется как связка "точность скриптов + гибкость агентов".

Почему он важен:

- это один из самых заметных новых инструментов именно в сегменте "альтернатива хрупким селекторам";
- Stagehand начинался как слой над Playwright, но в октябре 2025 команда официально описала уход глубже в браузерный runtime, а не тестовый слой;
- в Stagehand v3 команда заявила, что фреймворк стал extensible, быстрее и может работать с Puppeteer / CDP-драйверами.

Практический смысл:

- если вам нравится идея Playwright, но вы хотите меньше вручную поддерживать селекторы;
- если у вас сложные кабинеты, маркетплейсы, формы, админки;
- если нужна автоматизация, а не только тестирование.

Сильные стороны:

- natural-language действия;
- structured extraction;
- AI-resilience к изменениям DOM;
- session inspector и наблюдаемость;
- Python и TypeScript, плюс расширение в другие языки.

Слабые стороны:

- это уже не "чисто тестовый" инструмент;
- стоимость и стабильность сильно зависят от LLM-слоя;
- для строго детерминированных high-volume сценариев обычный код все еще часто дешевле и проще.

## 2. Самые интересные AI-native инструменты

### `Skyvern`

Что это:

- AI automation platform для browser workflows;
- облачный браузер + SDK + agent/task модель;
- по документации ваш код работает с браузером через Playwright `Page`, но поверх добавлены AI-методы `act`, `extract`, `validate`, `prompt`.

Что в нем сильного:

- cloud/browser session как продукт из коробки;
- запись всех прогонов;
- step-by-step reasoning и replay;
- есть локальный режим, но облачный сценарий у них основной;
- есть `page.agent.run_task(prompt)` для многошаговых задач.

Когда он подходит:

- если вам нужны реальные бизнес-автоматизации, а не только тесты;
- если важны запись, наблюдаемость, task lifecycle, API и cloud delivery;
- если надо быстро собирать полуагентные сценарии без низкоуровневой рутины.

Когда не лучший выбор:

- если нужен очень тонкий контроль и минимальная зависимость от внешнего SaaS;
- если вы не хотите тянуть LLM-слой, API-ключи и облачную инфраструктуру.

### `Browser Use`

Что это:

- современный CLI и SDK для browser automation;
- умеет работать с headless Chromium, реальным Chrome с существующими профилями и cloud-hosted браузерами;
- делает упор на persistent browser sessions и agent-friendly workflow.

Что интересно:

- persistent daemon и быстрые повторные команды;
- удобно для агентных сценариев, где браузер должен жить между шагами;
- есть cloud API, tasks, profiles, browser sessions;
- может подключаться к реальному Chrome и использовать существующие логины/куки.

Когда подходит:

- если вы хотите автоматизацию браузера как рабочий инструмент для агентов;
- если вам нужен CLI-first подход;
- если важен сценарий "подключиться к живому браузеру пользователя или профилю".

Ограничения:

- это уже не прямой тестовый фреймворк в духе Playwright Test;
- сильнее заточен под operational browser automation и agent tooling.

## 3. Классические альтернативы, которые все еще актуальны

### `Puppeteer`

Что это:

- JavaScript-библиотека для управления Chrome или Firefox через DevTools Protocol или WebDriver BiDi.

Почему по-прежнему актуален:

- очень легкий и понятный runtime;
- хорош для скриптов, скрейпинга, генерации PDF/скриншотов и Chrome-first автоматизации;
- на май 2026 официальный сайт показывает актуальную ветку `25.0.2`, docs описывают работу с Chrome и Firefox, плюс поддержку WebDriver BiDi.

Когда брать:

- если основная цель не E2E-тестовый фреймворк, а именно browser scripting;
- если живете в Node.js;
- если нужен простой контроль браузера без большой экосистемы.

Когда не брать:

- если вам нужна сильная multi-browser стратегия уровня Playwright/WebDriver-экосистемы;
- если нужна богатая тестовая обвязка из коробки.

### `WebdriverIO`

Что это:

- зрелый framework для browser и mobile automation;
- работает через WebDriver, WebDriver BiDi и смежные протоколы;
- docs для актуальной версии указывают ветку `>=9.x`.

Почему сейчас интересен:

- он уже не выглядит "устаревшим Selenium-wrapper";
- умеет web + mobile + Appium + cloud vendors;
- делает auto-setup драйверов и браузеров;
- по документации по умолчанию пытается стартовать локальную сессию через WebDriver BiDi.

Когда брать:

- если нужен один стек под web и mobile;
- если у вас Node.js и вы хотите enterprise-friendly automation с широкой совместимостью;
- если важны плагины, интеграции и long-term maintainability.

### `Selenium`

Что это:

- стандартный umbrella-проект для browser automation;
- основа на W3C WebDriver.

Почему до сих пор жив:

- максимум совместимости;
- поддержка многих языков;
- очень понятная enterprise-позиция;
- Grid, IDE, WebDriver и стандартные драйверы.

Когда брать:

- если команда на Java/.NET/Python и нужен максимально нейтральный стандарт;
- если важна поддержка больших enterprise-контуров;
- если нужна совместимость и предсказуемость на длинной дистанции.

Когда не лучший выбор:

- если вам нужен лучший developer experience "из коробки";
- если стартуете новый JS/TS-проект и не привязаны к корпоративным стандартам.

### `Cypress`

Что это:

- мощный E2E и component testing tool с сильным DX.

Почему остается в списке:

- очень удобен для frontend-команд;
- отличная отладка;
- быстрый feedback loop.

Но:

- это больше testing product, чем универсальная browser automation platform;
- для general automation, multi-step кабинетов, нестабильных внешних сайтов и agent-подхода он обычно менее интересен, чем Playwright/Stagehand/Skyvern;
- браузерная матрица и режимы все еще не его главное преимущество.

### `TestCafe`

Что это:

- старый, но живой end-to-end framework без зависимости от Selenium;
- использует свой URL-rewriting proxy подход.

Когда смотреть:

- если нужен более простой и человекочитаемый стек;
- если хочется быстро стартовать JS-based UI automation без WebDriver-слоя.

Почему обычно не первый выбор:

- по текущему ландшафту он уже не задает направление рынка;
- в новых AI/browser-agent сценариях он слабее как экосистема.

## 4. Если сравнивать именно с `Playwright`

### Что у Playwright все еще очень сильное

- зрелый test runner;
- хороший DX;
- сильная работа с Chromium, Firefox, WebKit;
- trace/debug/reporting;
- хороший баланс между скоростью и предсказуемостью.

### Где новые инструменты пытаются его обойти

Новые AI-native инструменты атакуют слабое место любого selector-driven подхода:

- UI поменялся;
- локатор сломался;
- сценарий надо чинить руками.

Именно поэтому `Stagehand`, `Skyvern`, `Browser Use` продают не просто "управление браузером", а:

- self-healing;
- semantic actions;
- prompt-driven navigation;
- structured extraction;
- live recordings и reasoning traces.

## 5. Практический выбор по сценариям

### Если у вас SaaS / кабинет / CRM / внутренняя панель

Смотреть:

- `Playwright`
- `Stagehand`
- `Skyvern`

Логика:

- `Playwright`, если UI стабилен и нужен строгий контроль;
- `Stagehand`, если UI часто меняется и хочется сохранить кодовый контроль;
- `Skyvern`, если нужен уже более "платформенный" подход с облаком, записями и agent workflows.

### Если задача про web scraping / сбор данных / формы на внешних сайтах

Смотреть:

- `Puppeteer`
- `Stagehand`
- `Browser Use`
- `Skyvern`

Логика:

- `Puppeteer` для дешевых и понятных Chrome-first сценариев;
- `Stagehand` для более устойчивого извлечения и действий;
- `Browser Use` для agent/CLI/persistent-session сценариев;
- `Skyvern`, если нужен orchestration и cloud execution.

### Если это QA и regression automation

Смотреть:

- `Playwright`
- `WebdriverIO`
- `Selenium`
- `Cypress`

Логика:

- `Playwright` как дефолт для нового web-проекта;
- `WebdriverIO`, если нужен web + mobile;
- `Selenium`, если сильны enterprise-ограничения и multi-language стек;
- `Cypress`, если команда чисто frontend и ценит DX сильнее универсальности.

### Если это "автоматизация действий пользователя" для AI-агентов

Смотреть в первую очередь:

- `Stagehand`
- `Browser Use`
- `Skyvern`

Потому что они строятся уже не вокруг модели "пишем тесты", а вокруг модели:

- браузер как инструмент агента;
- живые сессии;
- reasoning;
- observability;
- cloud execution;
- integration с LLM/MCP/agent tooling.

## 6. Мой прикладной рейтинг на май 2026

Если бы выбирать не "вообще", а под реальные задачи, я бы ранжировал так:

### Для новых продуктовых автоматизаций

1. `Stagehand`
2. `Playwright`
3. `Skyvern`
4. `Browser Use`

### Для QA / E2E

1. `Playwright`
2. `WebdriverIO`
3. `Selenium`
4. `Cypress`

### Для agent/browser automation

1. `Stagehand`
2. `Skyvern`
3. `Browser Use`
4. `Playwright`

### Для lightweight browser scripting

1. `Puppeteer`
2. `Playwright`
3. `Browser Use`

## 7. Что бы я рекомендовал вам смотреть первым

Если у вас задача не про классические автотесты, а про реальную автоматизацию бизнес-процессов в браузере, я бы смотрел в таком порядке:

1. `Stagehand`
2. `Skyvern`
3. `Browser Use`
4. потом уже сравнивал с `Playwright`

Причина простая:

- они лучше отражают текущее смещение рынка из "тестов браузера" в "исполняемые browser agents".

Если же вам нужна именно надежная инженерная интеграция в продукт без излишней агентности:

1. `Playwright`
2. `WebdriverIO`
3. `Puppeteer`

## 8. Главный вывод без маркетинга

`Playwright` не "умер" и не стал плохим. Наоборот, он остается сильной инженерной базой.

Но новое поколение инструментов уже строится вокруг другой идеи:

- не просто кликать по DOM;
- а выполнять бизнес-задачу через браузер, даже если интерфейс немного изменился.

Именно поэтому в 2026 действительно стоит смотреть на:

- `Stagehand`
- `Skyvern`
- `Browser Use`

А не только на старую тройку:

- `Selenium`
- `Cypress`
- `Puppeteer`

## Источники

Официальные источники:

- Playwright: https://playwright.dev/docs/intro
- Puppeteer: https://pptr.dev/
- Puppeteer WebDriver BiDi: https://pptr.dev/webdriver-bidi
- Selenium: https://www.selenium.dev/documentation/
- WebdriverIO: https://webdriver.io/docs/gettingstarted/
- WebdriverIO automation protocols: https://webdriver.io/docs/automationProtocols/
- Cypress install/docs: https://docs.cypress.io/app/get-started/install-cypress
- Cypress browser support: https://docs.cypress.io/app/references/launching-browsers
- TestCafe: https://testcafe.io/documentation/402635/guides/overview/getting-started
- Browser Use CLI: https://docs.browser-use.com/open-source/browser-use-cli
- Skyvern quickstart: https://www.skyvern.com/docs/developers/getting-started/quickstart
- Skyvern credential management: https://docs.skyvern.com/credentials/introduction
- Stagehand quickstart: https://docs.browserbase.com/welcome/quickstarts/stagehand
- Stagehand product page: https://www.browserbase.com/stagehand/
- Stagehand v3: https://www.browserbase.com/blog/stagehand-v3/
- Why Stagehand moved beyond Playwright: https://www.browserbase.com/blog/stagehand-playwright-evolution-browser-automation/
