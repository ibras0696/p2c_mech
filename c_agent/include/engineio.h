/* engineio.h — Engine.IO v4 / Socket.IO frame classification.
 *
 * Pure, allocation-free, dependency-free (only libc). Classifies a received
 * text frame by its leading bytes so the hot path can branch in a couple of
 * comparisons before any JSON parsing (docs/C_AGENT_SPEC_RU.md §4.3).
 */
#ifndef P2C_ENGINEIO_H
#define P2C_ENGINEIO_H

#include <stddef.h>

typedef enum {
    EIO_OTHER = 0,
    EIO_OPEN,            /* "0{...}"  engine open                          */
    EIO_PING,            /* "2"       engine ping  -> reply "3"            */
    EIO_PONG,            /* "3"                                            */
    EIO_SOCKET_CONNECT,  /* "40..."   namespace connect                    */
    EIO_SOCKET_DISCONN,  /* "41"      namespace disconnect                 */
    EIO_EVENT,           /* "42[...]" socket event (list:update etc.)      */
    EIO_LIST_UPDATE,     /* "42[\"list:update\",...]"  the hot one         */
} eio_kind_t;

/* Engine.IO protocol literals (also used by ws.c to send replies). */
#define EIO_FRAME_PONG            "3"
#define EIO_FRAME_SOCKET_CONNECT  "40"
#define EIO_FRAME_LIST_INIT       "42[\"list:initialize\"]"

/* Classify a text frame. msg/len must be the raw frame payload. */
eio_kind_t eio_classify(const char *msg, size_t len);

#endif /* P2C_ENGINEIO_H */
