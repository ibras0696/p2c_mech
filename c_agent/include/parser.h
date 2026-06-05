/* parser.h — extract orders from a list:update frame. */
#ifndef P2C_PARSER_H
#define P2C_PARSER_H

#include <stddef.h>
#include "agent.h"

typedef struct {
    char id[P2C_ID_LEN];        /* 24 hex + NUL                            */
    char amount[32];            /* in_amount as received (string, fixed)   */
    char currency[P2C_CURRENCY_LEN]; /* in_asset                           */
} p2c_order_t;

/* Called for each {"op":"add"} order in the frame. */
typedef void (*p2c_order_cb)(const p2c_order_t *order, void *user);

/* Parse a raw "42[\"list:update\",[...]]" frame, invoking cb per add-order.
 * Returns number of add-orders found, or -1 on parse error.
 *
 * Primary path uses yyjson over the in-place buffer (correct for batches and
 * mixed op:add/op:remove). A byte-scan fast path for the single-add common
 * case is a later optimization (spec §4.9), gated on profiling. */
int parser_extract_orders(const char *msg, size_t len,
                          p2c_order_cb cb, void *user);

#endif /* P2C_PARSER_H */
