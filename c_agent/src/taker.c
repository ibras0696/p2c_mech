/* taker.c — warm HTTP/2 POST of the take request via libcurl. */
#define _GNU_SOURCE
#include "taker.h"
#include "events.h"

#include <curl/curl.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "yyjson.h"

#ifdef P2C_USE_IMPERSONATE
/* curl-impersonate exports this extra symbol but ships no public header for
 * it, so declare the prototype ourselves. ABI matches stock libcurl. */
extern CURLcode curl_easy_impersonate(CURL *handle, const char *target,
                                      int default_headers);
#endif

#define RESP_MAX 4096

struct p2c_taker {
    const p2c_config_t *cfg;
    CURL  *curl;
    pthread_mutex_t curl_lock;               /* one CURL handle, two callers:
                                                WS-thread take + keepalive   */
    char   cookie_raw[P2C_COOKIE_MAX];       /* "access_token=.." (access-only) */
    char   url_prefix[512];                  /* base + take path prefix     */
    char   resp[RESP_MAX];
    size_t resp_len;
};

static size_t on_body(char *ptr, size_t size, size_t nmemb, void *userdata)
{
    struct p2c_taker *t = userdata;
    size_t n = size * nmemb;
    size_t space = RESP_MAX - 1 - t->resp_len;
    size_t copy = n < space ? n : space;
    memcpy(t->resp + t->resp_len, ptr, copy);
    t->resp_len += copy;
    t->resp[t->resp_len] = '\0';
    return n; /* consume all, even if we truncated our copy */
}

/* Set a STATIC Cookie header. The cookie engine is intentionally disabled (see
 * taker_create), so curl never captures or resends Cloudflare's rotating
 * __cf_bm from Set-Cookie. We send ONLY what we seed here (access_token), to
 * minimise our Cloudflare bot-management fingerprint — a stable __cf_bm lets CF
 * correlate our takes across requests and progressively throttle us. */
static void apply_cookie(struct p2c_taker *t, const char *cookie_header)
{
    snprintf(t->cookie_raw, sizeof(t->cookie_raw), "%s",
             cookie_header ? cookie_header : "");
    if (t->curl) curl_easy_setopt(t->curl, CURLOPT_COOKIE, t->cookie_raw);
}

p2c_taker_t *taker_create(const p2c_config_t *cfg, const char *cookie_header)
{
    struct p2c_taker *t = calloc(1, sizeof(*t));
    if (!t) return NULL;
    t->cfg = cfg;
    t->curl = curl_easy_init();
    if (!t->curl) { free(t); return NULL; }
    pthread_mutex_init(&t->curl_lock, NULL);

    snprintf(t->url_prefix, sizeof(t->url_prefix),
             "%s/internal/v1/p2c/payments/take/", cfg->base_url);

    CURL *c = t->curl;
    /* Cookie engine deliberately NOT enabled: we never call CURLOPT_COOKIEFILE,
     * so curl ignores Set-Cookie and only sends our static access-only cookie.
     * This keeps __cf_bm out of every take (anti-Cloudflare-fingerprint). */
    apply_cookie(t, cookie_header);
    curl_easy_setopt(c, CURLOPT_POST, 1L);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, "");
    curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, 0L);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, on_body);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, t);
    curl_easy_setopt(c, CURLOPT_TCP_NODELAY, 1L);
    curl_easy_setopt(c, CURLOPT_HTTP_VERSION, (long)CURL_HTTP_VERSION_2TLS);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 0L);
    curl_easy_setopt(c, CURLOPT_TIMEOUT_MS, (long)cfg->take_timeout_ms);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
    /* Keep the take connection hot: cache DNS long, enable TCP keepalive, and
     * let curl reuse the pooled H2 connection across takes. The real warmth is
     * maintained by taker_keepalive() pinging the host while idle. */
    curl_easy_setopt(c, CURLOPT_DNS_CACHE_TIMEOUT, 600L);
    curl_easy_setopt(c, CURLOPT_TCP_KEEPALIVE, 1L);
    curl_easy_setopt(c, CURLOPT_TCP_KEEPIDLE, 15L);
    curl_easy_setopt(c, CURLOPT_TCP_KEEPINTVL, 15L);
#ifdef P2C_USE_IMPERSONATE
    /* curl-impersonate: apply Chrome TLS/H2 fingerprint */
    curl_easy_impersonate(c, cfg->impersonate, 1);
#endif
    return t;
}

void taker_set_cookie(p2c_taker_t *t, const char *cookie_header)
{
    apply_cookie(t, cookie_header);
}

void taker_prewarm(p2c_taker_t *t)
{
    /* Lightweight GET to open TCP+TLS and seed the connection pool so the
     * first real take reuses a warm connection. Any response is fine. */
    CURL *c = curl_easy_init();
    if (!c) return;
    curl_easy_setopt(c, CURLOPT_URL, t->cfg->base_url);
    curl_easy_setopt(c, CURLOPT_NOBODY, 1L);
    curl_easy_setopt(c, CURLOPT_TIMEOUT_MS, 3000L);
    curl_easy_setopt(c, CURLOPT_TCP_NODELAY, 1L);
    curl_easy_perform(c);
    curl_easy_cleanup(c);
    /* Also do one warm-up on the real handle so its pool has the connection. */
    char url[600];
    snprintf(url, sizeof(url), "%s", t->cfg->base_url);
    t->resp_len = 0;
    curl_easy_setopt(t->curl, CURLOPT_URL, url);
    curl_easy_setopt(t->curl, CURLOPT_NOBODY, 1L);
    curl_easy_perform(t->curl);
    curl_easy_setopt(t->curl, CURLOPT_NOBODY, 0L);
    curl_easy_setopt(t->curl, CURLOPT_POST, 1L);
}

void taker_keepalive(p2c_taker_t *t)
{
    /* Cheap HEAD on the base host to keep the pooled TLS/H2 connection warm so
     * the next real take reuses it (no fresh handshake => ~200ms not ~1500ms).
     * MUST be called only from the take thread (shares t->curl, single-writer).
     * Hits base_url root, NOT the take endpoint, so it never counts as a take
     * (avoids the 429 ban on repeated takes). */
    if (!t || !t->curl) return;
    pthread_mutex_lock(&t->curl_lock);
    t->resp_len = 0;
    t->resp[0] = '\0';
    curl_easy_setopt(t->curl, CURLOPT_URL, t->cfg->base_url);
    curl_easy_setopt(t->curl, CURLOPT_HTTPHEADER, NULL);
    curl_easy_setopt(t->curl, CURLOPT_NOBODY, 1L);
    curl_easy_perform(t->curl);
    curl_easy_setopt(t->curl, CURLOPT_NOBODY, 0L);
    curl_easy_setopt(t->curl, CURLOPT_POST, 1L);
    pthread_mutex_unlock(&t->curl_lock);
}

int taker_post(p2c_taker_t *t, const char *order_id, take_result_t *out)
{
    char url[600];
    snprintf(url, sizeof(url), "%s%s", t->url_prefix, order_id);

    /* Cookies are sent by the cookie engine (captures rotated __cf_bm); we only
     * suppress Expect: 100-continue here. */
    struct curl_slist *hdrs = NULL;
    hdrs = curl_slist_append(hdrs, "Expect:");

    pthread_mutex_lock(&t->curl_lock);
    t->resp_len = 0;
    t->resp[0] = '\0';
    out->status = 0;
    out->http_ms = 0;
    out->payment_id = -1;
    out->reason[0] = '\0';

    curl_easy_setopt(t->curl, CURLOPT_URL, url);
    curl_easy_setopt(t->curl, CURLOPT_HTTPHEADER, hdrs);

    struct timespec a, b;
    clock_gettime(CLOCK_MONOTONIC, &a);
    CURLcode rc = curl_easy_perform(t->curl);
    clock_gettime(CLOCK_MONOTONIC, &b);
    out->http_ms = (b.tv_sec - a.tv_sec) * 1000.0 + (b.tv_nsec - a.tv_nsec) / 1e6;

    curl_slist_free_all(hdrs);

    if (rc != CURLE_OK) {
        snprintf(out->reason, sizeof(out->reason), "%s", curl_easy_strerror(rc));
        pthread_mutex_unlock(&t->curl_lock);
        return -1;
    }

    long code = 0;
    curl_easy_getinfo(t->curl, CURLINFO_RESPONSE_CODE, &code);
    out->status = (int)code;

    /* DIAG: where does the take latency go? newconn>0 + tls>0 => connection NOT
     * reused (fresh TLS each take => keepalive isn't working). newconn=0 + high
     * ttfb => server-side processing/RTT dominates (warmth can't help). */
    {
        double t_dns=0, t_conn=0, t_tls=0, t_ttfb=0, t_total=0; long nconn=0;
        curl_easy_getinfo(t->curl, CURLINFO_NAMELOOKUP_TIME, &t_dns);
        curl_easy_getinfo(t->curl, CURLINFO_CONNECT_TIME, &t_conn);
        curl_easy_getinfo(t->curl, CURLINFO_APPCONNECT_TIME, &t_tls);
        curl_easy_getinfo(t->curl, CURLINFO_STARTTRANSFER_TIME, &t_ttfb);
        curl_easy_getinfo(t->curl, CURLINFO_TOTAL_TIME, &t_total);
        curl_easy_getinfo(t->curl, CURLINFO_NUM_CONNECTS, &nconn);
        log_info("take timing status=%ld dns=%.0f conn=%.0f tls=%.0f ttfb=%.0f total=%.0f newconn=%ld",
                 code, t_dns*1000, t_conn*1000, t_tls*1000, t_ttfb*1000, t_total*1000, nconn);
    }

    /* Log the raw body on a win (and any non-400) so the real payment_id field
     * shape is visible — the take 200 body schema isn't documented yet. */
    if (code == 200 || (code != 400 && code != 0))
        log_info("take status=%ld body=%.300s", code,
                 t->resp_len ? t->resp : "(empty)");

    /* parse body: try several id field names + string ids + nested data{} */
    if (t->resp_len > 0) {
        yyjson_doc *doc = yyjson_read(t->resp, t->resp_len, 0);
        if (doc) {
            yyjson_val *root = yyjson_doc_get_root(doc);
            if (yyjson_is_obj(root)) {
                static const char *id_keys[] = {"payment_id", "paymentId", "id"};
                yyjson_val *scopes[2] = { root, yyjson_obj_get(root, "data") };
                for (int s = 0; s < 2 && out->payment_id < 0; ++s) {
                    if (!yyjson_is_obj(scopes[s])) continue;
                    for (size_t k = 0; k < sizeof(id_keys)/sizeof(id_keys[0]); ++k) {
                        yyjson_val *pid = yyjson_obj_get(scopes[s], id_keys[k]);
                        if (!pid) continue;
                        if (yyjson_is_int(pid)) { out->payment_id = (long)yyjson_get_sint(pid); break; }
                        if (yyjson_is_str(pid)) {
                            long v = atol(yyjson_get_str(pid));
                            if (v > 0) { out->payment_id = v; break; }
                        }
                    }
                }
                const char *reason = yyjson_get_str(yyjson_obj_get(root, "reason"));
                if (!reason) reason = yyjson_get_str(yyjson_obj_get(root, "error"));
                if (reason) snprintf(out->reason, sizeof(out->reason), "%s", reason);
            }
            yyjson_doc_free(doc);
        }
    }
    pthread_mutex_unlock(&t->curl_lock);
    return 0;
}

void taker_destroy(p2c_taker_t *t)
{
    if (!t) return;
    if (t->curl) curl_easy_cleanup(t->curl);
    pthread_mutex_destroy(&t->curl_lock);
    free(t);
}
