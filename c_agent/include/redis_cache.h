/* redis_cache.h — cache-aside READ side for the C agent (hiredis).
 *
 * The agent reads sessions from Redis only at connect/refresh (NOT the hot
 * path). On a miss it emits session_miss and waits for FastAPI to push a
 * session via stdin (FastAPI owns the Postgres fallback + Redis repopulation).
 * See docs/C_AGENT_SPEC_RU.md §3.
 */
#ifndef P2C_REDIS_CACHE_H
#define P2C_REDIS_CACHE_H

#include "agent.h"

typedef struct p2c_redis p2c_redis_t;

/* Connect to redis://[:user:pass@]host:port/db. Returns NULL on failure. */
p2c_redis_t *redis_connect(const char *redis_url);

/* GET p2c:session:{account_id} and parse JSON into out.
 * Returns 0 on hit, -1 on miss/error. */
int redis_get_session(p2c_redis_t *r, const char *account_id, p2c_session_t *out);

void redis_close(p2c_redis_t *r);

#endif /* P2C_REDIS_CACHE_H */
