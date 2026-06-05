/* parser.c — extract add-orders from a list:update frame via yyjson. */
#include "parser.h"

#include <string.h>
#include "yyjson.h"

static void copy_field(char *dst, size_t cap, yyjson_val *v)
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

int parser_extract_orders(const char *msg, size_t len,
                          p2c_order_cb cb, void *user)
{
    /* skip the "42" Socket.IO event prefix */
    if (len < 3 || msg[0] != '4' || msg[1] != '2') return -1;
    const char *json = msg + 2;
    size_t jlen = len - 2;

    /* yyjson reads in place; INSITU would mutate the buffer, so use a normal
     * read (still no per-value allocation on the hot path of interest). */
    yyjson_doc *doc = yyjson_read(json, jlen, 0);
    if (!doc) return -1;

    yyjson_val *root = yyjson_doc_get_root(doc);   /* ["list:update",[...]] */
    if (!yyjson_is_arr(root) || yyjson_arr_size(root) < 2) {
        yyjson_doc_free(doc);
        return -1;
    }

    yyjson_val *items = yyjson_arr_get(root, 1);   /* array of {op,data} */
    if (!yyjson_is_arr(items)) {
        yyjson_doc_free(doc);
        return -1;
    }

    int found = 0;
    size_t idx, max;
    yyjson_val *entry;
    yyjson_arr_foreach(items, idx, max, entry) {
        if (!yyjson_is_obj(entry)) continue;
        const char *op = yyjson_get_str(yyjson_obj_get(entry, "op"));
        if (!op || strcmp(op, "add") != 0) continue;

        yyjson_val *data = yyjson_obj_get(entry, "data");
        if (!yyjson_is_obj(data)) continue;

        p2c_order_t order;
        copy_field(order.id, sizeof(order.id), yyjson_obj_get(data, "id"));
        copy_field(order.amount, sizeof(order.amount), yyjson_obj_get(data, "in_amount"));
        copy_field(order.currency, sizeof(order.currency), yyjson_obj_get(data, "in_asset"));

        if (order.id[0] == '\0') continue;  /* no id -> skip */
        cb(&order, user);
        found++;
    }

    yyjson_doc_free(doc);
    return found;
}
