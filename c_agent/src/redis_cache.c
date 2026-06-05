/* redis_cache.c — cache-aside read side via hiredis. */
#define _GNU_SOURCE
#include "redis_cache.h"
#include "events.h"

#include <hiredis/hiredis.h>
#include <stdlib.h>
#include <string.h>

#include "yyjson.h"

struct p2c_redis {
    redisContext *ctx;
    char host[256];
    int  port;
    int  db;
    char password[256];
};

/* Parse redis://[user:pass@]host:port/db (user is ignored; pass optional). */
static int parse_url(const char *url, struct p2c_redis *r)
{
    r->host[0] = '\0'; r->port = 6379; r->db = 0; r->password[0] = '\0';
    const char *p = url;
    if (strncmp(p, "redis://", 8) == 0) p += 8;
    else if (strncmp(p, "rediss://", 9) == 0) p += 9;

    /* optional credentials before '@' */
    const char *at = strchr(p, '@');
    if (at) {
        const char *colon = memchr(p, ':', at - p);
        const char *pw = colon ? colon + 1 : p;   /* skip user, take pass */
        size_t n = at - pw;
        if (n >= sizeof(r->password)) n = sizeof(r->password) - 1;
        memcpy(r->password, pw, n);
        r->password[n] = '\0';
        p = at + 1;
    }

    /* host[:port][/db] */
    const char *slash = strchr(p, '/');
    const char *hostend = slash ? slash : p + strlen(p);
    const char *colon = memchr(p, ':', hostend - p);
    size_t hlen = (colon ? colon : hostend) - p;
    if (hlen >= sizeof(r->host)) hlen = sizeof(r->host) - 1;
    memcpy(r->host, p, hlen);
    r->host[hlen] = '\0';
    if (colon) r->port = atoi(colon + 1);
    if (slash) r->db = atoi(slash + 1);
    return r->host[0] ? 0 : -1;
}

p2c_redis_t *redis_connect(const char *redis_url)
{
    if (!redis_url || !*redis_url) return NULL;
    struct p2c_redis *r = calloc(1, sizeof(*r));
    if (!r) return NULL;
    if (parse_url(redis_url, r) != 0) { free(r); return NULL; }

    struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
    r->ctx = redisConnectWithTimeout(r->host, r->port, tv);
    if (!r->ctx || r->ctx->err) {
        log_warn("redis connect failed host=%s port=%d: %s",
                 r->host, r->port, r->ctx ? r->ctx->errstr : "alloc");
        if (r->ctx) redisFree(r->ctx);
        free(r);
        return NULL;
    }
    if (r->password[0]) {
        redisReply *rep = redisCommand(r->ctx, "AUTH %s", r->password);
        if (rep) freeReplyObject(rep);
    }
    if (r->db) {
        redisReply *rep = redisCommand(r->ctx, "SELECT %d", r->db);
        if (rep) freeReplyObject(rep);
    }
    log_info("redis connected host=%s port=%d db=%d", r->host, r->port, r->db);
    return r;
}

static void copy_str(char *dst, size_t cap, yyjson_val *v)
{
    dst[0] = '\0';
    if (v && yyjson_is_str(v)) {
        const char *s = yyjson_get_str(v);
        size_t n = yyjson_get_len(v);
        if (n >= cap) n = cap - 1;
        memcpy(dst, s, n);
        dst[n] = '\0';
    }
}

int redis_get_session(p2c_redis_t *r, const char *account_id, p2c_session_t *out)
{
    if (!r || !r->ctx) return -1;
    memset(out, 0, sizeof(*out));

    redisReply *rep = redisCommand(r->ctx, "GET p2c:session:%s", account_id);
    if (!rep) return -1;
    if (rep->type != REDIS_REPLY_STRING || rep->len == 0) {
        freeReplyObject(rep);
        return -1; /* miss */
    }

    int rc = -1;
    yyjson_doc *doc = yyjson_read(rep->str, rep->len, 0);
    if (doc) {
        yyjson_val *root = yyjson_doc_get_root(doc);
        if (yyjson_is_obj(root)) {
            copy_str(out->access_token, sizeof(out->access_token),
                     yyjson_obj_get(root, "access_token"));
            copy_str(out->cookie_header, sizeof(out->cookie_header),
                     yyjson_obj_get(root, "cookie_header"));
            yyjson_val *exp = yyjson_obj_get(root, "expires_at");
            if (exp && yyjson_is_int(exp)) out->expires_at = yyjson_get_sint(exp);
            if (out->cookie_header[0] || out->access_token[0]) rc = 0;
        }
        yyjson_doc_free(doc);
    }
    freeReplyObject(rep);
    return rc;
}

void redis_close(p2c_redis_t *r)
{
    if (!r) return;
    if (r->ctx) redisFree(r->ctx);
    free(r);
}
