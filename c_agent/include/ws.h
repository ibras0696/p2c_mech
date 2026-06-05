/* ws.h — libwebsockets client that owns the Engine.IO/Socket.IO handshake
 * and surfaces only list:update frames + connect/disconnect to the caller. */
#ifndef P2C_WS_H
#define P2C_WS_H

#include <stddef.h>
#include "agent.h"

typedef struct {
    void (*on_connected)(void *user);
    void (*on_disconnected)(void *user, int code);
    void (*on_list_update)(void *user, const char *msg, size_t len);
} ws_callbacks_t;

typedef struct p2c_ws p2c_ws_t;

/* Create a WS client for one account. cookie_header is copied. */
p2c_ws_t *ws_create(const p2c_config_t *cfg, const char *cookie_header,
                    const ws_callbacks_t *cb, void *user);

/* Connect and service frames until *running becomes 0, the socket drops, or
 * max_seconds elapses (0 = no limit; used for proactive session refresh).
 * Returns when disconnected; caller decides whether to reconnect. */
void ws_run(p2c_ws_t *ws, volatile int *running, int max_seconds);

void ws_destroy(p2c_ws_t *ws);

#endif /* P2C_WS_H */
