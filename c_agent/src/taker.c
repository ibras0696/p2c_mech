/* taker.c — warm HTTP/2 POST of the take request via libcurl. */
#define _GNU_SOURCE
#include "taker.h"
#include "events.h"

#include <curl/curl.h>
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
    char   cookie_hdr[P2C_COOKIE_MAX + 16];  /* "Cookie: ..." */
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

static void rebuild_cookie(struct p2c_taker *t, const char *cookie_header)
{
    snprintf(t->cookie_hdr, sizeof(t->cookie_hdr), "Cookie: %s",
             cookie_header ? cookie_header : "");
}

p2c_taker_t *taker_create(const p2c_config_t *cfg, const char *cookie_header)
{
    struct p2c_taker *t = calloc(1, sizeof(*t));
    if (!t) return NULL;
    t->cfg = cfg;
    t->curl = curl_easy_init();
    if (!t->curl) { free(t); return NULL; }

    snprintf(t->url_prefix, sizeof(t->url_prefix),
             "%s/internal/v1/p2c/payments/take/", cfg->base_url);
    rebuild_cookie(t, cookie_header);

    CURL *c = t->curl;
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
#ifdef P2C_USE_IMPERSONATE
    /* curl-impersonate: apply Chrome TLS/H2 fingerprint */
    curl_easy_impersonate(c, cfg->impersonate, 1);
#endif
    return t;
}

void taker_set_cookie(p2c_taker_t *t, const char *cookie_header)
{
    rebuild_cookie(t, cookie_header);
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

int taker_post(p2c_taker_t *t, const char *order_id, take_result_t *out)
{
    char url[600];
    snprintf(url, sizeof(url), "%s%s", t->url_prefix, order_id);

    struct curl_slist *hdrs = NULL;
    hdrs = curl_slist_append(hdrs, t->cookie_hdr);
    /* tell libcurl not to add Expect: 100-continue */
    hdrs = curl_slist_append(hdrs, "Expect:");

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
        return -1;
    }

    long code = 0;
    curl_easy_getinfo(t->curl, CURLINFO_RESPONSE_CODE, &code);
    out->status = (int)code;

    /* parse body */
    if (t->resp_len > 0) {
        yyjson_doc *doc = yyjson_read(t->resp, t->resp_len, 0);
        if (doc) {
            yyjson_val *root = yyjson_doc_get_root(doc);
            if (yyjson_is_obj(root)) {
                yyjson_val *pid = yyjson_obj_get(root, "payment_id");
                if (pid && yyjson_is_int(pid)) out->payment_id = (long)yyjson_get_sint(pid);
                const char *reason = yyjson_get_str(yyjson_obj_get(root, "reason"));
                if (reason) snprintf(out->reason, sizeof(out->reason), "%s", reason);
            }
            yyjson_doc_free(doc);
        }
    }
    return 0;
}

void taker_destroy(p2c_taker_t *t)
{
    if (!t) return;
    if (t->curl) curl_easy_cleanup(t->curl);
    free(t);
}
