/* config.c — CLI args + stdin NDJSON command parsing (yyjson). */
#include "config.h"
#include "events.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "yyjson.h"

static const char *DEFAULTS_WS  = "wss://app.send.tg/socket.io/?EIO=4&transport=websocket";
static const char *DEFAULTS_URL = "https://app.send.tg";

void config_print_help(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  --ws-url URL          Engine.IO websocket URL\n"
        "  --base-url URL        platform base (https://app.send.tg)\n"
        "  --origin URL          Origin header\n"
        "  --redis URL           redis://host:port/db (optional, cache-aside)\n"
        "  --impersonate NAME    curl-impersonate target (default chrome131)\n"
        "  --cpu-map MAP         pin account threads, e.g. acc1:2,acc2:3\n"
        "  --take-timeout-ms N   take POST timeout (default 2500)\n"
        "  --ping-keepalive-ms N HTTP/2 PING interval (default 12000)\n"
        "  --realtime            SCHED_FIFO + mlockall (needs CAP_SYS_NICE)\n"
        "  --help                this help\n"
        "\n"
        "Sessions/cookies are NOT passed as args (visible in ps); they arrive\n"
        "via Redis (cache-aside) or stdin NDJSON commands.\n",
        prog);
}

int config_parse_args(int argc, char **argv, p2c_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->ws_url           = DEFAULTS_WS;
    cfg->base_url         = DEFAULTS_URL;
    cfg->origin           = DEFAULTS_URL;
    cfg->redis_url        = NULL;
    cfg->impersonate      = "chrome131";
    cfg->cpu_map          = NULL;
    cfg->realtime         = false;
    cfg->take_timeout_ms  = 2500;
    cfg->ping_keepalive_ms = 12000;
    cfg->session_refresh_s = 1500;  /* 25 min: proactive __cf_bm rotation */

    for (int i = 1; i < argc; ++i) {
        const char *a = argv[i];
        #define NEXT() (i + 1 < argc ? argv[++i] : NULL)
        if (!strcmp(a, "--help"))                 { config_print_help(argv[0]); return 1; }
        else if (!strcmp(a, "--ws-url"))          { cfg->ws_url = NEXT(); }
        else if (!strcmp(a, "--base-url"))        { cfg->base_url = NEXT(); }
        else if (!strcmp(a, "--origin"))          { cfg->origin = NEXT(); }
        else if (!strcmp(a, "--redis"))           { cfg->redis_url = NEXT(); }
        else if (!strcmp(a, "--impersonate"))     { cfg->impersonate = NEXT(); }
        else if (!strcmp(a, "--cpu-map"))         { cfg->cpu_map = NEXT(); }
        else if (!strcmp(a, "--take-timeout-ms")) { const char *v = NEXT(); if (v) cfg->take_timeout_ms = atoi(v); }
        else if (!strcmp(a, "--ping-keepalive-ms")){ const char *v = NEXT(); if (v) cfg->ping_keepalive_ms = atoi(v); }
        else if (!strcmp(a, "--realtime"))        { cfg->realtime = true; }
        else { fprintf(stderr, "unknown arg: %s\n", a); config_print_help(argv[0]); return 2; }
        #undef NEXT
    }
    if (!cfg->ws_url || !cfg->base_url) {
        fprintf(stderr, "ws-url and base-url are required\n");
        return 2;
    }
    return 0;
}

/* ---- stdin command parsing ------------------------------------------- */

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

static void parse_filter(yyjson_val *obj, p2c_filter_t *f)
{
    memset(f, 0, sizeof(*f));
    yyjson_val *mn = yyjson_obj_get(obj, "min_amount");
    yyjson_val *mx = yyjson_obj_get(obj, "max_amount");
    if (mn) f->min_amount = yyjson_get_sint(mn);
    if (mx) f->max_amount = yyjson_get_sint(mx);
    yyjson_val *cur = yyjson_obj_get(obj, "currencies");
    if (cur && yyjson_is_arr(cur)) {
        size_t idx, max;
        yyjson_val *item;
        yyjson_arr_foreach(cur, idx, max, item) {
            if (f->currency_count >= P2C_CURRENCIES_MAX) break;
            if (yyjson_is_str(item)) {
                copy_str(f->currencies[f->currency_count], P2C_CURRENCY_LEN, item);
                f->currency_count++;
            }
        }
    }
}

int config_parse_command(const char *line, size_t len, p2c_cmd_t *cmd)
{
    memset(cmd, 0, sizeof(*cmd));
    yyjson_doc *doc = yyjson_read(line, len, 0);
    if (!doc) return -1;
    yyjson_val *root = yyjson_doc_get_root(doc);
    if (!yyjson_is_obj(root)) { yyjson_doc_free(doc); return -1; }

    const char *c = yyjson_get_str(yyjson_obj_get(root, "cmd"));
    if (!c) { yyjson_doc_free(doc); return -1; }

    if      (!strcmp(c, "add_account"))    cmd->kind = P2C_CMD_ADD_ACCOUNT;
    else if (!strcmp(c, "session"))        cmd->kind = P2C_CMD_SESSION;
    else if (!strcmp(c, "filter"))         cmd->kind = P2C_CMD_FILTER;
    else if (!strcmp(c, "mode"))           cmd->kind = P2C_CMD_MODE;
    else if (!strcmp(c, "remove_account")) cmd->kind = P2C_CMD_REMOVE_ACCOUNT;
    else if (!strcmp(c, "shutdown"))       cmd->kind = P2C_CMD_SHUTDOWN;
    else                                   cmd->kind = P2C_CMD_UNKNOWN;

    copy_str(cmd->account, sizeof(cmd->account), yyjson_obj_get(root, "account"));
    copy_str(cmd->session.access_token, sizeof(cmd->session.access_token),
             yyjson_obj_get(root, "access_token"));
    copy_str(cmd->session.cookie_header, sizeof(cmd->session.cookie_header),
             yyjson_obj_get(root, "cookie_header"));

    yyjson_val *flt = yyjson_obj_get(root, "filters");
    if (cmd->kind == P2C_CMD_FILTER) flt = root; /* flat filter cmd */
    if (flt) { parse_filter(flt, &cmd->filter); cmd->has_filter = true; }

    if (cmd->kind == P2C_CMD_MODE) {
        const char *v = yyjson_get_str(yyjson_obj_get(root, "value"));
        cmd->mode = (v && !strcmp(v, "running")) ? P2C_MODE_RUNNING : P2C_MODE_PAUSED;
    }

    yyjson_doc_free(doc);
    return 0;
}
