/* agent.h — core data model and integration contracts for the P2C C-agent.
 *
 * This header is the single source of truth for the stdin command / stdout
 * event NDJSON protocol shared with the FastAPI supervisor (see
 * docs/C_AGENT_SPEC_RU.md §4.6, §4.7) and the Redis key layout (§3.2).
 *
 * Keep it dependency-free (only libc) so every module can include it.
 */
#ifndef P2C_AGENT_H
#define P2C_AGENT_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define P2C_MAX_ACCOUNTS        32
#define P2C_ID_LEN              25   /* 24 hex chars + NUL                  */
#define P2C_TOKEN_MAX          1024
#define P2C_COOKIE_MAX         4096
#define P2C_LABEL_MAX            64
#define P2C_CURRENCIES_MAX        8
#define P2C_CURRENCY_LEN          8
#define P2C_SEEN_CAPACITY     16384  /* per-account dedup set (power of two)*/

/* ----- run mode (per account) ----------------------------------------- */
typedef enum {
    P2C_MODE_PAUSED = 0,   /* detect but do not send take                  */
    P2C_MODE_RUNNING = 1,  /* detect AND send take                         */
} p2c_mode_t;

/* ----- amount filter (fixed-point, minor units — no float) ------------- */
typedef struct {
    int64_t  min_amount;            /* inclusive, minor units (e.g. kopeks) */
    int64_t  max_amount;            /* inclusive; 0 == no upper bound       */
    char     currencies[P2C_CURRENCIES_MAX][P2C_CURRENCY_LEN];
    int      currency_count;        /* 0 == any currency                    */
} p2c_filter_t;

/* ----- session (cache-aside payload, Redis p2c:session:{id}) ----------- */
typedef struct {
    char     access_token[P2C_TOKEN_MAX];
    char     cookie_header[P2C_COOKIE_MAX];   /* full Cookie incl. __cf_bm  */
    int64_t  expires_at;                      /* unix sec; 0 == unknown     */
} p2c_session_t;

/* ----- account: everything one WS/take thread needs -------------------- */
typedef struct p2c_account p2c_account_t;     /* opaque; defined in account.c */

/* ----- command kinds (stdin NDJSON {"cmd":...}) ------------------------ */
typedef enum {
    P2C_CMD_UNKNOWN = 0,
    P2C_CMD_ADD_ACCOUNT,
    P2C_CMD_SESSION,
    P2C_CMD_FILTER,
    P2C_CMD_MODE,
    P2C_CMD_REMOVE_ACCOUNT,
    P2C_CMD_SHUTDOWN,
} p2c_cmd_kind_t;

/* parsed stdin command (one line of NDJSON) */
typedef struct {
    p2c_cmd_kind_t kind;
    char           account[P2C_LABEL_MAX];    /* "" == applies to all       */
    p2c_session_t  session;                   /* for ADD_ACCOUNT / SESSION  */
    p2c_filter_t   filter;                    /* for ADD_ACCOUNT / FILTER   */
    bool           has_filter;
    p2c_mode_t     mode;                      /* for MODE                   */
} p2c_cmd_t;

/* ----- global agent configuration (CLI args) --------------------------- */
typedef struct {
    const char *ws_url;        /* wss://app.send.tg/socket.io/?EIO=4&...    */
    const char *base_url;      /* https://app.send.tg                       */
    const char *origin;        /* https://app.send.tg                       */
    const char *redis_url;     /* redis://127.0.0.1:6379/0  (optional)      */
    const char *impersonate;   /* chrome131                                 */
    const char *cpu_map;       /* "acc1:2,acc2:3"  (optional)               */
    bool        realtime;      /* SCHED_FIFO + mlockall                      */
    int         take_timeout_ms;
    int         ping_keepalive_ms;
    int         session_refresh_s; /* force WS reconnect + cache re-read     */
} p2c_config_t;

#endif /* P2C_AGENT_H */
