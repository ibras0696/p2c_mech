/* events.c — thread-safe NDJSON emitter to stdout, human logs to stderr. */
#include "events.h"

#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <time.h>

static pthread_mutex_t g_out = PTHREAD_MUTEX_INITIALIZER;

static int64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Minimal JSON string escaper for the small set of fields we emit.
 * Account labels / reasons are controlled by us, so we only guard quotes,
 * backslashes and control chars. */
static void put_jstr(FILE *f, const char *s)
{
    fputc('"', f);
    for (; s && *s; ++s) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); }
        else if (c == '\n') { fputs("\\n", f); }
        else if (c == '\r') { fputs("\\r", f); }
        else if (c == '\t') { fputs("\\t", f); }
        else if (c < 0x20)  { fprintf(f, "\\u%04x", c); }
        else                { fputc(c, f); }
    }
    fputc('"', f);
}

void ev_init(void)
{
    setvbuf(stdout, NULL, _IOLBF, 0); /* line-buffered NDJSON */
    setvbuf(stderr, NULL, _IONBF, 0);
}

#define LOCK()   pthread_mutex_lock(&g_out)
#define UNLOCK() do { fputc('\n', stdout); fflush(stdout); pthread_mutex_unlock(&g_out); } while (0)

void ev_ws_connected(const char *account)
{
    LOCK();
    fputs("{\"event\":\"ws_connected\",\"account\":", stdout);
    put_jstr(stdout, account);
    fprintf(stdout, ",\"ts\":%lld}", (long long)now_ms());
    UNLOCK();
}

void ev_ws_disconnected(const char *account, int code)
{
    LOCK();
    fputs("{\"event\":\"ws_disconnected\",\"account\":", stdout);
    put_jstr(stdout, account);
    fprintf(stdout, ",\"code\":%d,\"ts\":%lld}", code, (long long)now_ms());
    UNLOCK();
}

void ev_session_miss(const char *account)
{
    LOCK();
    fputs("{\"event\":\"session_miss\",\"account\":", stdout);
    put_jstr(stdout, account);
    fputc('}', stdout);
    UNLOCK();
}

void ev_error(const char *account, const char *where, const char *msg)
{
    LOCK();
    fputs("{\"event\":\"error\",\"account\":", stdout);
    put_jstr(stdout, account);
    fputs(",\"where\":", stdout);
    put_jstr(stdout, where);
    fputs(",\"msg\":", stdout);
    put_jstr(stdout, msg);
    fputc('}', stdout);
    UNLOCK();
}

void ev_order_detected(const char *account, const char *order,
                       const char *amount, const char *currency,
                       uint64_t detect_ns)
{
    LOCK();
    fputs("{\"event\":\"order_detected\",\"account\":", stdout);
    put_jstr(stdout, account);
    fputs(",\"order\":", stdout);
    put_jstr(stdout, order);
    fputs(",\"amount\":", stdout);
    put_jstr(stdout, amount);
    fputs(",\"currency\":", stdout);
    put_jstr(stdout, currency);
    fprintf(stdout, ",\"detect_ns\":%llu}", (unsigned long long)detect_ns);
    UNLOCK();
}

void ev_take_sent(const char *account, const char *order)
{
    LOCK();
    fputs("{\"event\":\"take_sent\",\"account\":", stdout);
    put_jstr(stdout, account);
    fputs(",\"order\":", stdout);
    put_jstr(stdout, order);
    fprintf(stdout, ",\"ts\":%lld}", (long long)now_ms());
    UNLOCK();
}

void ev_take_result(const char *account, const char *order,
                    int status, double http_ms, long payment_id)
{
    LOCK();
    fputs("{\"event\":\"take_result\",\"account\":", stdout);
    put_jstr(stdout, account);
    fputs(",\"order\":", stdout);
    put_jstr(stdout, order);
    fprintf(stdout, ",\"status\":%d,\"http_ms\":%.1f,\"payment_id\":%ld}",
            status, http_ms, payment_id);
    UNLOCK();
}

void ev_claim_won(const char *account, const char *order, long payment_id)
{
    LOCK();
    fputs("{\"event\":\"claim_won\",\"account\":", stdout);
    put_jstr(stdout, account);
    fputs(",\"order\":", stdout);
    put_jstr(stdout, order);
    fprintf(stdout, ",\"payment_id\":%ld}", payment_id);
    UNLOCK();
}

void ev_claim_lost(const char *account, const char *order,
                   int status, const char *reason)
{
    LOCK();
    fputs("{\"event\":\"claim_lost\",\"account\":", stdout);
    put_jstr(stdout, account);
    fputs(",\"order\":", stdout);
    put_jstr(stdout, order);
    fprintf(stdout, ",\"status\":%d,\"reason\":", status);
    put_jstr(stdout, reason);
    fputc('}', stdout);
    UNLOCK();
}

/* heartbeat is emitted as a single line built across begin / account / end */
void ev_heartbeat_begin(void)
{
    pthread_mutex_lock(&g_out);
    fprintf(stdout, "{\"event\":\"heartbeat\",\"ts\":%lld,\"per_account\":[",
            (long long)now_ms());
}

static int g_hb_first = 1;
void ev_heartbeat_account(const char *account, uint64_t orders_seen,
                          uint64_t takes, uint64_t wins)
{
    if (!g_hb_first) fputc(',', stdout);
    g_hb_first = 0;
    fputs("{\"account\":", stdout);
    put_jstr(stdout, account);
    fprintf(stdout, ",\"orders_seen\":%llu,\"takes\":%llu,\"wins\":%llu}",
            (unsigned long long)orders_seen, (unsigned long long)takes,
            (unsigned long long)wins);
}

void ev_heartbeat_end(void)
{
    fputs("]}", stdout);
    fputc('\n', stdout);
    fflush(stdout);
    g_hb_first = 1;
    pthread_mutex_unlock(&g_out);
}

static void vlog(const char *level, const char *fmt, va_list ap)
{
    flockfile(stderr);
    fprintf(stderr, "[%s] ", level);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    funlockfile(stderr);
}

void log_info(const char *fmt, ...) { va_list a; va_start(a, fmt); vlog("info", fmt, a); va_end(a); }
void log_warn(const char *fmt, ...) { va_list a; va_start(a, fmt); vlog("warn", fmt, a); va_end(a); }
void log_err(const char *fmt, ...)  { va_list a; va_start(a, fmt); vlog("err",  fmt, a); va_end(a); }
