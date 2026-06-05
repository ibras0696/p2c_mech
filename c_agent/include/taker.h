/* taker.h — warm HTTP/2 POST of the take request via libcurl.
 *
 * Production links curl-impersonate (Chrome TLS fingerprint) by building with
 * -DP2C_USE_IMPERSONATE and linking libcurl-impersonate-chrome; local testing
 * against the plain-HTTP mock uses stock libcurl. Header set is cookie-only
 * (matches the curl-cffi finding: stripping origin/referer/accept cut latency).
 */
#ifndef P2C_TAKER_H
#define P2C_TAKER_H

#include "agent.h"

typedef struct {
    int    status;        /* HTTP status code (0 == transport error)       */
    double http_ms;       /* round-trip time                               */
    long   payment_id;    /* parsed on 200, else -1                        */
    char   reason[64];    /* parsed on non-200 (e.g. "InvalidStatus")      */
} take_result_t;

typedef struct p2c_taker p2c_taker_t;

p2c_taker_t *taker_create(const p2c_config_t *cfg, const char *cookie_header);
void taker_set_cookie(p2c_taker_t *t, const char *cookie_header); /* hot-swap */
void taker_prewarm(p2c_taker_t *t);                               /* open conn+TLS */
void taker_keepalive(p2c_taker_t *t);   /* cheap HEAD to keep the conn hot (idle) */
int  taker_post(p2c_taker_t *t, const char *order_id, take_result_t *out);
void taker_destroy(p2c_taker_t *t);

#endif /* P2C_TAKER_H */
