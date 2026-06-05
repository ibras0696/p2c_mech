/* events.h — stdout NDJSON event emitter (thread-safe).
 *
 * Contract with FastAPI supervisor (docs/C_AGENT_SPEC_RU.md §4.7).
 * stdout = machine-readable NDJSON ONLY. Human logs go to stderr via log_*().
 *
 * Hot path note (§4.9): emitters must NOT block the WS thread. v1 uses a
 * mutex-guarded line write; a later iteration moves to a per-thread ring
 * buffer flushed by a dedicated thread (ringbuf.c).
 */
#ifndef P2C_EVENTS_H
#define P2C_EVENTS_H

#include <stdint.h>

void ev_init(void);

/* lifecycle / connection */
void ev_ws_connected(const char *account);
void ev_ws_disconnected(const char *account, int code);
void ev_session_miss(const char *account);
void ev_error(const char *account, const char *where, const char *msg);

/* hot path */
void ev_order_detected(const char *account, const char *order,
                       const char *amount, const char *currency,
                       uint64_t detect_ns);
void ev_take_sent(const char *account, const char *order);
void ev_take_result(const char *account, const char *order,
                    int status, double http_ms, long payment_id);
void ev_claim_won(const char *account, const char *order, long payment_id);
void ev_claim_lost(const char *account, const char *order,
                   int status, const char *reason);

/* periodic */
void ev_heartbeat_begin(void);
void ev_heartbeat_account(const char *account, uint64_t orders_seen,
                          uint64_t takes, uint64_t wins);
void ev_heartbeat_end(void);

/* stderr human logs */
void log_info(const char *fmt, ...);
void log_warn(const char *fmt, ...);
void log_err(const char *fmt, ...);

#endif /* P2C_EVENTS_H */
