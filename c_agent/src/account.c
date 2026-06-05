/* account.c — per-account WS+take orchestration and the registry. */
#define _GNU_SOURCE
#include "account.h"
#include "ws.h"
#include "taker.h"
#include "parser.h"
#include "events.h"
#include "redis_cache.h"

#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define TAKE_RING        256
#define MAX_INFLIGHT       8
#define RECONNECT_MIN_S    1
#define RECONNECT_MAX_S   30

/* ---- per-account dedup set (open addressing, fixed, no alloc) --------- */
typedef struct {
    char ids[P2C_SEEN_CAPACITY][P2C_ID_LEN];
} seen_t;

static uint64_t fnv1a(const char *s)
{
    uint64_t h = 1469598103934665603ULL;
    for (; *s; ++s) { h ^= (unsigned char)*s; h *= 1099511628211ULL; }
    return h;
}

static int seen_check_add(seen_t *s, const char *id)
{
    size_t mask = P2C_SEEN_CAPACITY - 1;
    size_t i = fnv1a(id) & mask;
    for (size_t probe = 0; probe < P2C_SEEN_CAPACITY; ++probe) {
        char *slot = s->ids[i];
        if (slot[0] == '\0') { snprintf(slot, P2C_ID_LEN, "%s", id); return 0; } /* added */
        if (strcmp(slot, id) == 0) return 1;  /* already seen */
        i = (i + 1) & mask;
    }
    return 1; /* table full -> treat as seen (safe: skip) */
}

/* ---- take ring (SPSC, mutex+condvar; enqueue is the only hot touch) --- */
typedef struct {
    char id[P2C_ID_LEN];
} take_item_t;

struct p2c_account {
    char id[P2C_LABEL_MAX];

    pthread_mutex_t lock;          /* guards cookie/session/filter          */
    char            cookie[P2C_COOKIE_MAX];
    char            access_token[P2C_TOKEN_MAX];
    p2c_filter_t    filter;
    _Atomic int     mode;          /* p2c_mode_t                            */

    seen_t          seen;
    _Atomic int     inflight;

    /* ring */
    take_item_t     ring[TAKE_RING];
    int             r_head, r_tail;
    pthread_mutex_t r_lock;
    pthread_cond_t  r_cv;

    p2c_taker_t    *taker;
    p2c_redis_t    *redis;         /* cache-aside read (NULL if no --redis)  */

    _Atomic uint64_t orders_seen, takes, wins;

    const p2c_config_t *cfg;
    volatile int   *global_running;
    volatile int    alive;         /* account should keep running           */
    volatile int    conn_run;      /* current WS connection should continue  */

    pthread_t       ws_th, take_th;
    int             threads_started;
};

struct p2c_registry {
    const p2c_config_t *cfg;
    volatile int *global_running;
    pthread_mutex_t lock;
    p2c_account_t *accounts[P2C_MAX_ACCOUNTS];
    int count;
};

static uint64_t now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + ts.tv_nsec;
}

/* ---- filter ---------------------------------------------------------- */
static int filter_match(const p2c_filter_t *f, const p2c_order_t *o)
{
    long long amt = atoll(o->amount);
    if (f->min_amount && amt < f->min_amount) return 0;
    if (f->max_amount && amt > f->max_amount) return 0;
    if (f->currency_count > 0) {
        int ok = 0;
        for (int i = 0; i < f->currency_count; ++i)
            if (strcmp(f->currencies[i], o->currency) == 0) { ok = 1; break; }
        if (!ok) return 0;
    }
    return 1;
}

/* ---- detect callback (WS thread) ------------------------------------- */
struct detect_ctx { p2c_account_t *acc; uint64_t t0; };

static void on_order(const p2c_order_t *order, void *user)
{
    struct detect_ctx *ctx = user;
    p2c_account_t *a = ctx->acc;

    atomic_fetch_add(&a->orders_seen, 1);

    if (atomic_load(&a->mode) != P2C_MODE_RUNNING) return;

    pthread_mutex_lock(&a->lock);
    int pass = filter_match(&a->filter, order);
    pthread_mutex_unlock(&a->lock);
    if (!pass) return;

    if (seen_check_add(&a->seen, order->id)) return;       /* already tried  */
    if (atomic_load(&a->inflight) >= MAX_INFLIGHT) return; /* slot budget    */

    uint64_t detect_ns = now_ns() - ctx->t0;
    ev_order_detected(a->id, order->id, order->amount, order->currency, detect_ns);

    /* enqueue to take thread (decision stays in WS thread; only the blocking
     * curl POST is handed off, per §4.9 v1 note) */
    pthread_mutex_lock(&a->r_lock);
    int next = (a->r_tail + 1) % TAKE_RING;
    if (next != a->r_head) {
        snprintf(a->ring[a->r_tail].id, P2C_ID_LEN, "%s", order->id);
        a->r_tail = next;
        atomic_fetch_add(&a->inflight, 1);
        atomic_fetch_add(&a->takes, 1);
        pthread_cond_signal(&a->r_cv);
        pthread_mutex_unlock(&a->r_lock);
        ev_take_sent(a->id, order->id);
    } else {
        pthread_mutex_unlock(&a->r_lock);  /* ring full: drop */
    }
}

static void on_list_update(void *user, const char *msg, size_t len)
{
    p2c_account_t *a = user;
    struct detect_ctx ctx = { a, now_ns() };
    parser_extract_orders(msg, len, on_order, &ctx);
}

static void on_connected(void *user)
{
    p2c_account_t *a = user;
    ev_ws_connected(a->id);
}

static void on_disconnected(void *user, int code)
{
    p2c_account_t *a = user;
    ev_ws_disconnected(a->id, code);
}

/* ---- take thread ----------------------------------------------------- */
static void *take_thread(void *arg)
{
    p2c_account_t *a = arg;
    while (a->alive && *a->global_running) {
        take_item_t item;
        pthread_mutex_lock(&a->r_lock);
        while (a->r_head == a->r_tail && a->alive && *a->global_running) {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            ts.tv_sec += 1;
            pthread_cond_timedwait(&a->r_cv, &a->r_lock, &ts);
        }
        if (a->r_head == a->r_tail) { pthread_mutex_unlock(&a->r_lock); continue; }
        item = a->ring[a->r_head];
        a->r_head = (a->r_head + 1) % TAKE_RING;
        pthread_mutex_unlock(&a->r_lock);

        take_result_t res;
        taker_post(a->taker, item.id, &res);
        atomic_fetch_sub(&a->inflight, 1);

        ev_take_result(a->id, item.id, res.status, res.http_ms, res.payment_id);
        if (res.status == 200 && res.payment_id >= 0) {
            atomic_fetch_add(&a->wins, 1);
            ev_claim_won(a->id, item.id, res.payment_id);
        } else {
            ev_claim_lost(a->id, item.id, res.status,
                          res.reason[0] ? res.reason : "");
        }
    }
    return NULL;
}

/* ---- ws thread ------------------------------------------------------- */
static void *ws_thread(void *arg)
{
    p2c_account_t *a = arg;
    int backoff = RECONNECT_MIN_S;
    ws_callbacks_t cb = { on_connected, on_disconnected, on_list_update };

    while (a->alive && *a->global_running) {
        /* cache-aside: if we have no session yet, read it from Redis (cold
         * start). On miss, signal FastAPI and wait for a session push. This
         * is the connect path, NOT the hot path (spec §3.3). */
        pthread_mutex_lock(&a->lock);
        int have_cookie = a->cookie[0] != '\0';
        pthread_mutex_unlock(&a->lock);

        if (!have_cookie && a->redis) {
            p2c_session_t s;
            if (redis_get_session(a->redis, a->id, &s) == 0) {
                pthread_mutex_lock(&a->lock);
                snprintf(a->cookie, sizeof(a->cookie), "%s", s.cookie_header);
                snprintf(a->access_token, sizeof(a->access_token), "%s", s.access_token);
                taker_set_cookie(a->taker, a->cookie);
                have_cookie = a->cookie[0] != '\0';
                pthread_mutex_unlock(&a->lock);
            }
        }
        if (!have_cookie) {
            ev_session_miss(a->id);   /* FastAPI: load from PG -> push session */
            for (int s = 0; s < 2 && a->alive && *a->global_running; ++s) {
                struct timespec ts = { .tv_sec = 1, .tv_nsec = 0 };
                nanosleep(&ts, NULL);
            }
            continue;
        }

        pthread_mutex_lock(&a->lock);
        char cookie[P2C_COOKIE_MAX];
        snprintf(cookie, sizeof(cookie), "%s", a->cookie);
        pthread_mutex_unlock(&a->lock);

        p2c_ws_t *ws = ws_create(a->cfg, cookie, &cb, a);
        if (!ws) { ev_error(a->id, "ws", "create failed"); }
        else {
            uint64_t t_start = now_ns();
            a->conn_run = 1;
            ws_run(ws, &a->conn_run, a->cfg->session_refresh_s);
            ws_destroy(ws);
            if (now_ns() - t_start > 5000000000ull) backoff = RECONNECT_MIN_S; /* stable conn -> reset */
        }
        if (!a->alive || !*a->global_running) break;

        for (int s = 0; s < backoff && a->alive && *a->global_running; ++s) {
            struct timespec ts = { .tv_sec = 1, .tv_nsec = 0 };
            nanosleep(&ts, NULL);
        }
        backoff = backoff * 2 > RECONNECT_MAX_S ? RECONNECT_MAX_S : backoff * 2;
    }
    return NULL;
}

/* ---- account lifecycle ----------------------------------------------- */
static p2c_account_t *account_create(const p2c_config_t *cfg, volatile int *grun,
                                     const char *id, const p2c_session_t *sess,
                                     const p2c_filter_t *filter)
{
    p2c_account_t *a = calloc(1, sizeof(*a));
    if (!a) return NULL;
    snprintf(a->id, sizeof(a->id), "%s", id);
    pthread_mutex_init(&a->lock, NULL);
    pthread_mutex_init(&a->r_lock, NULL);
    pthread_cond_init(&a->r_cv, NULL);
    a->cfg = cfg;
    a->global_running = grun;
    a->alive = 1;
    atomic_store(&a->mode, P2C_MODE_RUNNING);  /* active on add; pause via cmd */
    if (sess) {
        snprintf(a->cookie, sizeof(a->cookie), "%s", sess->cookie_header);
        snprintf(a->access_token, sizeof(a->access_token), "%s", sess->access_token);
    }
    if (filter) a->filter = *filter;

    a->taker = taker_create(cfg, a->cookie);
    if (!a->taker) { free(a); return NULL; }
    taker_prewarm(a->taker);

    if (cfg->redis_url) a->redis = redis_connect(cfg->redis_url);

    pthread_create(&a->take_th, NULL, take_thread, a);
    pthread_create(&a->ws_th, NULL, ws_thread, a);
    a->threads_started = 1;
    return a;
}

static void account_destroy(p2c_account_t *a)
{
    if (!a) return;
    a->alive = 0;
    a->conn_run = 0;
    pthread_mutex_lock(&a->r_lock);
    pthread_cond_broadcast(&a->r_cv);
    pthread_mutex_unlock(&a->r_lock);
    if (a->threads_started) {
        pthread_join(a->ws_th, NULL);
        pthread_join(a->take_th, NULL);
    }
    if (a->taker) taker_destroy(a->taker);
    if (a->redis) redis_close(a->redis);
    pthread_mutex_destroy(&a->lock);
    pthread_mutex_destroy(&a->r_lock);
    pthread_cond_destroy(&a->r_cv);
    free(a);
}

/* ---- registry -------------------------------------------------------- */
p2c_registry_t *registry_create(const p2c_config_t *cfg, volatile int *grun)
{
    p2c_registry_t *r = calloc(1, sizeof(*r));
    if (!r) return NULL;
    r->cfg = cfg;
    r->global_running = grun;
    pthread_mutex_init(&r->lock, NULL);
    return r;
}

static p2c_account_t *find_locked(p2c_registry_t *r, const char *id)
{
    for (int i = 0; i < r->count; ++i)
        if (strcmp(r->accounts[i]->id, id) == 0) return r->accounts[i];
    return NULL;
}

int registry_add_account(p2c_registry_t *r, const char *id,
                          const p2c_session_t *sess, const p2c_filter_t *filter)
{
    pthread_mutex_lock(&r->lock);
    p2c_account_t *a = find_locked(r, id);
    if (a) {  /* update existing */
        pthread_mutex_lock(&a->lock);
        if (sess) {
            snprintf(a->cookie, sizeof(a->cookie), "%s", sess->cookie_header);
            snprintf(a->access_token, sizeof(a->access_token), "%s", sess->access_token);
            taker_set_cookie(a->taker, a->cookie);
        }
        if (filter) a->filter = *filter;
        pthread_mutex_unlock(&a->lock);
        a->conn_run = 0; /* reconnect with new cookie */
        pthread_mutex_unlock(&r->lock);
        return 0;
    }
    if (r->count >= P2C_MAX_ACCOUNTS) { pthread_mutex_unlock(&r->lock); return -1; }
    a = account_create(r->cfg, r->global_running, id, sess, filter);
    if (!a) { pthread_mutex_unlock(&r->lock); return -1; }
    r->accounts[r->count++] = a;
    pthread_mutex_unlock(&r->lock);
    log_info("account added id=%s total=%d", id, r->count);
    return 0;
}

int registry_set_session(p2c_registry_t *r, const char *id, const p2c_session_t *sess)
{
    pthread_mutex_lock(&r->lock);
    p2c_account_t *a = find_locked(r, id);
    if (a) {
        pthread_mutex_lock(&a->lock);
        snprintf(a->cookie, sizeof(a->cookie), "%s", sess->cookie_header);
        snprintf(a->access_token, sizeof(a->access_token), "%s", sess->access_token);
        taker_set_cookie(a->taker, a->cookie);
        pthread_mutex_unlock(&a->lock);
        a->conn_run = 0; /* force reconnect with fresh cookie */
    }
    pthread_mutex_unlock(&r->lock);
    return a ? 0 : -1;
}

int registry_set_filter(p2c_registry_t *r, const char *id, const p2c_filter_t *filter)
{
    pthread_mutex_lock(&r->lock);
    int n = 0;
    for (int i = 0; i < r->count; ++i) {
        p2c_account_t *a = r->accounts[i];
        if (id[0] && strcmp(a->id, id) != 0) continue;
        pthread_mutex_lock(&a->lock);
        a->filter = *filter;
        pthread_mutex_unlock(&a->lock);
        n++;
    }
    pthread_mutex_unlock(&r->lock);
    return n ? 0 : -1;
}

int registry_set_mode(p2c_registry_t *r, const char *id, p2c_mode_t mode)
{
    pthread_mutex_lock(&r->lock);
    int n = 0;
    for (int i = 0; i < r->count; ++i) {
        p2c_account_t *a = r->accounts[i];
        if (id[0] && strcmp(a->id, id) != 0) continue;
        atomic_store(&a->mode, mode);
        n++;
    }
    pthread_mutex_unlock(&r->lock);
    return n ? 0 : -1;
}

int registry_remove(p2c_registry_t *r, const char *id)
{
    pthread_mutex_lock(&r->lock);
    int idx = -1;
    for (int i = 0; i < r->count; ++i)
        if (strcmp(r->accounts[i]->id, id) == 0) { idx = i; break; }
    if (idx < 0) { pthread_mutex_unlock(&r->lock); return -1; }
    p2c_account_t *a = r->accounts[idx];
    r->accounts[idx] = r->accounts[--r->count];
    pthread_mutex_unlock(&r->lock);
    account_destroy(a);
    log_info("account removed id=%s", id);
    return 0;
}

void registry_heartbeat(p2c_registry_t *r)
{
    ev_heartbeat_begin();
    pthread_mutex_lock(&r->lock);
    for (int i = 0; i < r->count; ++i) {
        p2c_account_t *a = r->accounts[i];
        ev_heartbeat_account(a->id,
            atomic_load(&a->orders_seen), atomic_load(&a->takes), atomic_load(&a->wins));
    }
    pthread_mutex_unlock(&r->lock);
    ev_heartbeat_end();
}

void registry_destroy(p2c_registry_t *r)
{
    if (!r) return;
    pthread_mutex_lock(&r->lock);
    int n = r->count;
    p2c_account_t *list[P2C_MAX_ACCOUNTS];
    memcpy(list, r->accounts, sizeof(p2c_account_t *) * n);
    r->count = 0;
    pthread_mutex_unlock(&r->lock);
    for (int i = 0; i < n; ++i) account_destroy(list[i]);
    pthread_mutex_destroy(&r->lock);
    free(r);
}
