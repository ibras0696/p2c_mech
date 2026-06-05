/* engineio.c — Engine.IO v4 / Socket.IO frame classification (hot path). */
#define _GNU_SOURCE
#include "engineio.h"

#include <string.h>

/* Fixed marker we look for inside a "42[...]" event to detect list:update.
 * The event name is the first array element: 42["list:update", ...]. We scan
 * a short bounded window rather than parsing JSON. */
static const char LIST_UPDATE[] = "\"list:update\"";

eio_kind_t eio_classify(const char *msg, size_t len)
{
    if (len == 0) return EIO_OTHER;

    /* single-byte engine frames */
    if (len == 1) {
        if (msg[0] == '2') return EIO_PING;
        if (msg[0] == '3') return EIO_PONG;
        if (msg[0] == '0') return EIO_OPEN;
        return EIO_OTHER;
    }

    if (msg[0] == '0') return EIO_OPEN;          /* "0{...}" */

    if (msg[0] == '4') {
        if (msg[1] == '0') return EIO_SOCKET_CONNECT;  /* "40" / "40{...}" */
        if (msg[1] == '1') return EIO_SOCKET_DISCONN;  /* "41" */
        if (msg[1] == '2') {
            /* "42[...]" socket event — check if it is list:update.
             * Bound the search to a small window near the front; the event
             * name is always the first element right after 42[ . */
            size_t window = len < 64 ? len : 64;
            if (memmem(msg + 2, window - 2, LIST_UPDATE, sizeof(LIST_UPDATE) - 1))
                return EIO_LIST_UPDATE;
            return EIO_EVENT;
        }
    }
    return EIO_OTHER;
}
